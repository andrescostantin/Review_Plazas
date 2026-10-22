"""Migraciones de la base SQLite de Plaza Reviews.

Diseño:
- La tabla original `reviews` (formulario de 4 categorías) NO se modifica ni se
  borra: queda como archivo histórico de solo lectura.
- Se crean tablas nuevas para el formulario de 8 categorías.
- Cada migración corre en una transacción y se registra en `schema_migrations`,
  por lo que ejecutarla varias veces es seguro (idempotente).
- Antes de migrar una base que ya tiene datos se crea una copia de seguridad
  con la API de backup de SQLite en la carpeta `backups/`.

Uso manual:  python migrations.py [ruta/a/plaza_reviews.db]
"""
import os
import sqlite3
import sys
from datetime import datetime

CATEGORY_KEYS = (
    "caminos", "iluminacion", "bancos", "limpieza",
    "juegos", "verde", "seguridad", "equipamiento",
)

_cats_sql = ", ".join(f"'{k}'" for k in CATEGORY_KEYS)

# Lista ordenada de (versión, nombre, [sentencias SQL]).
MIGRATIONS = [
    (1, "evaluaciones_por_categoria", [
        """CREATE TABLE IF NOT EXISTS evaluaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),   -- UTC
            relacion TEXT,
            frecuencia TEXT,
            usos TEXT NOT NULL DEFAULT '',          -- claves separadas por coma
            mejora_prioritaria TEXT NOT NULL DEFAULT '',
            comentario_general TEXT NOT NULL DEFAULT ''
        )""",
        f"""CREATE TABLE IF NOT EXISTS evaluacion_categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evaluacion_id INTEGER NOT NULL
                REFERENCES evaluaciones(id) ON DELETE CASCADE,
            categoria TEXT NOT NULL CHECK (categoria IN ({_cats_sql})),
            puntuacion INTEGER CHECK (puntuacion BETWEEN 1 AND 5),  -- NULL = sin puntuar
            no_sabe INTEGER NOT NULL DEFAULT 0 CHECK (no_sabe IN (0, 1)),
            comentario TEXT NOT NULL DEFAULT '',
            UNIQUE (evaluacion_id, categoria),
            CHECK (NOT (no_sabe = 1 AND puntuacion IS NOT NULL))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_evalcat_categoria ON evaluacion_categorias (categoria, puntuacion)",
        "CREATE INDEX IF NOT EXISTS idx_evaluaciones_fecha ON evaluaciones (created_at)",
    ]),
]


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _has_user_data(db_path):
    """True si el archivo existe y contiene al menos una tabla de usuario."""
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return False
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone() is not None
    finally:
        conn.close()


def make_backup(db_path, tag):
    """Copia consistente de la base en <carpeta de la DB>/backups/. Devuelve la ruta."""
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.splitext(os.path.basename(db_path))[0]
    dest = os.path.join(backup_dir, f"{base}-{stamp}-{tag}.db")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest


def _applied_versions(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}


def apply_migrations(db_path, backup=True):
    """Aplica las migraciones pendientes. Devuelve la lista de versiones aplicadas."""
    had_data = _has_user_data(db_path)

    probe = sqlite3.connect(db_path)
    try:
        pending = [m for m in MIGRATIONS if m[0] not in _applied_versions(probe)]
        probe.commit()
    finally:
        probe.close()
    if not pending:
        return []

    if backup and had_data:
        make_backup(db_path, f"pre-migracion-v{pending[0][0]}")

    conn = sqlite3.connect(db_path, isolation_level=None)  # transacciones manuales
    applied = []
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for version, name, statements in MIGRATIONS:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Re-chequeo dentro del lock: otro proceso pudo migrar entretanto.
                if version in _applied_versions(conn):
                    conn.execute("ROLLBACK")
                    continue
                legacy_before = (
                    conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
                    if _table_exists(conn, "reviews") else None
                )
                for stmt in statements:
                    conn.execute(stmt)
                legacy_after = (
                    conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
                    if _table_exists(conn, "reviews") else None
                )
                if legacy_before != legacy_after:
                    raise RuntimeError("La migración alteró la cantidad de reseñas históricas")
                conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name),
                )
                conn.execute("COMMIT")
                applied.append(version)
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()
    return applied


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "plaza_reviews.db"))
    done = apply_migrations(path)
    print(f"Migraciones aplicadas en {path}: {done if done else 'ninguna (ya estaba al día)'}")
