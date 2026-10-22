"""Pruebas automatizadas. Usan SIEMPRE bases temporales: nunca tocan plaza_reviews.db.

Ejecutar:  python -m unittest discover -s tests -v
"""
import csv
import hashlib
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# La importación de app.py crea una app a nivel de módulo: apuntarla a una base temporal.
_IMPORT_DIR = tempfile.mkdtemp(prefix="plaza_import_")
os.environ["DB_PATH"] = os.path.join(_IMPORT_DIR, "import.db")
os.environ["ANALYST_PASSWORD"] = "clave-de-test"
os.environ["SECRET_KEY"] = "secreto-de-test"

import app as plaza  # noqa: E402
import migrations  # noqa: E402

REAL_DB = os.path.join(ROOT, "plaza_reviews.db")
def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


REAL_DB_HASH = _sha(REAL_DB) if os.path.exists(REAL_DB) else None

LEGACY_SCHEMA = """CREATE TABLE reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    infraestructura INTEGER NOT NULL, seguridad INTEGER NOT NULL, limpieza INTEGER NOT NULL, juegos INTEGER NOT NULL,
    infraestructura_text TEXT DEFAULT '', seguridad_text TEXT DEFAULT '', limpieza_text TEXT DEFAULT '', juegos_text TEXT DEFAULT '',
    sugerencia TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
LEGACY_ROWS = [
    (1, 3, 1, 1, 1, "No hay cosas rotas", "Peligroso de noche", "Mucha basura", "No hay juegos", "Quiero juegos para niños", "2026-10-10 21:10:11"),
    (2, 1, 1, 1, 2, "", "", "", "", "Flores autóctonas", "2026-10-11 00:32:06"),
    (3, 1, 2, 4, 2, "", "", "", "", "Más iluminación", "2026-10-15 20:35:29"),
    (4, 5, 5, 5, 5, "", "", "", "", "", "2026-10-15 20:35:36"),
    (5, 3, 2, 4, 3, "", "", "", "", "Prueba", "2026-10-15 21:10:40"),
]


def make_legacy_db(path):
    conn = sqlite3.connect(path)
    conn.execute(LEGACY_SCHEMA)
    conn.executemany("INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)", LEGACY_ROWS)
    conn.commit()
    conn.close()


def form(ratings=None, nosabe=(), comments=None, **extra):
    """Arma datos de formulario. ratings: {clave: 1..5}"""
    d = {}
    for k, v in (ratings or {}).items():
        d[f"rating_{k}"] = str(v)
    for k in nosabe:
        d[f"nosabe_{k}"] = "1"
    for k, v in (comments or {}).items():
        d[f"comentario_{k}"] = v
    d.update(extra)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="plaza_test_")
        self.db = os.path.join(self.tmp, "test.db")
        self.setup_db()
        self.app = plaza.create_app({"DB_PATH": self.db, "ANALYST_PASSWORD": "pw-test", "SECRET_KEY": "k", "TESTING": True})
        self.client = self.app.test_client()

    def setup_db(self):
        pass

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def login(self, client=None):
        c = client or self.client
        return c.post("/analistas/login", data={"password": "pw-test"})

    def q(self, sql, params=()):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def stats(self, **qs):
        r = self.client.get("/api/stats", query_string=qs)
        self.assertEqual(r.status_code, 200, r.data)
        return r.get_json()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="plaza_mig_")
        self.db = os.path.join(self.tmp, "m.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_legacy_rows_preserved_and_backup_created(self):
        make_legacy_db(self.db)
        before = sqlite3.connect(self.db).execute("SELECT * FROM reviews ORDER BY id").fetchall()
        applied = migrations.apply_migrations(self.db)
        self.assertEqual(applied, [1])
        after = sqlite3.connect(self.db).execute("SELECT * FROM reviews ORDER BY id").fetchall()
        self.assertEqual(before, after)                  # 5 filas idénticas, sin puntuaciones inventadas
        self.assertEqual(len(after), 5)
        backups = os.listdir(os.path.join(self.tmp, "backups"))
        self.assertEqual(len(backups), 1)
        bk = sqlite3.connect(os.path.join(self.tmp, "backups", backups[0]))
        self.assertEqual(bk.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 5)
        bk.close()

    def test_idempotent(self):
        make_legacy_db(self.db)
        self.assertEqual(migrations.apply_migrations(self.db), [1])
        self.assertEqual(migrations.apply_migrations(self.db), [])
        self.assertEqual(migrations.apply_migrations(self.db), [])
        self.assertEqual(len(os.listdir(os.path.join(self.tmp, "backups"))), 1)  # no hay backups repetidos
        n = sqlite3.connect(self.db).execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        self.assertEqual(n, 1)

    def test_fresh_db_no_backup(self):
        self.assertEqual(migrations.apply_migrations(self.db), [1])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "backups")))

    def test_tables_and_constraints(self):
        migrations.apply_migrations(self.db)
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO evaluaciones (comentario_general) VALUES ('x')")
        with self.assertRaises(sqlite3.IntegrityError):   # puntuación fuera de rango
            conn.execute("INSERT INTO evaluacion_categorias (evaluacion_id, categoria, puntuacion) VALUES (1,'caminos',6)")
        with self.assertRaises(sqlite3.IntegrityError):   # categoría inexistente (p. ej. deportiva)
            conn.execute("INSERT INTO evaluacion_categorias (evaluacion_id, categoria, puntuacion) VALUES (1,'deportes',3)")
        with self.assertRaises(sqlite3.IntegrityError):   # puntuación y «No sé» a la vez
            conn.execute("INSERT INTO evaluacion_categorias (evaluacion_id, categoria, puntuacion, no_sabe) VALUES (1,'caminos',3,1)")
        conn.close()


class SubmissionTests(Base):
    def test_public_pages_load(self):
        for url in ("/", "/reseñar"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        html = self.client.get("/reseñar").get_data(as_text=True)
        for c in plaza.CATEGORIES:
            self.assertIn(c["name"], html)
        self.assertEqual(html.count('name="rating_'), 8 * 5)
        self.assertIn("No sé / No puedo evaluarla", html)
        self.assertNotRegex(html.lower(), r"cancha|gimnasio|deport")

    def test_valid_submission_stores_all_eight_categories(self):
        data = form({"caminos": 4, "iluminacion": 2, "limpieza": 1}, nosabe=["juegos"],
                    comments={"iluminacion": "Hay luminarias apagadas", "bancos": "Faltan bancos"},
                    relacion="vivo_cerca", frecuencia="diaria", usos=["pasear", "ninos"],
                    mejora_prioritaria="Más luz", comentario_general="Lindo lugar")
        r = self.client.post("/review", data=data)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/gracias"))
        self.assertEqual(self.client.get("/gracias").status_code, 200)

        ev = self.q("SELECT * FROM evaluaciones")
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["relacion"], ev[0]["frecuencia"], ev[0]["usos"]), ("vivo_cerca", "diaria", "pasear,ninos"))
        self.assertEqual(ev[0]["mejora_prioritaria"], "Más luz")
        self.assertEqual(ev[0]["comentario_general"], "Lindo lugar")
        rows = {r["categoria"]: r for r in self.q("SELECT * FROM evaluacion_categorias")}
        self.assertEqual(set(rows), set(plaza.CATEGORY_KEYS))          # las 8
        self.assertEqual(rows["caminos"]["puntuacion"], 4)
        self.assertEqual(rows["iluminacion"]["comentario"], "Hay luminarias apagadas")
        self.assertIsNone(rows["bancos"]["puntuacion"])                # sin puntuar = NULL, no 0
        self.assertEqual(rows["bancos"]["comentario"], "Faltan bancos")
        self.assertIsNone(rows["juegos"]["puntuacion"])
        self.assertEqual(rows["juegos"]["no_sabe"], 1)
        self.assertEqual(self.q("SELECT COUNT(*) c FROM evaluacion_categorias WHERE puntuacion = 0")[0]["c"], 0)

    def test_invalid_submissions_rejected(self):
        bad = [
            form({}),                                               # vacío
            form({"caminos": 0}),                                   # fuera de rango
            form({"caminos": 6}),
            form({"caminos": "abc"}),
            form({"caminos": "3.5"}),
            form({"caminos": "３"}),                                 # dígito unicode: no debe aceptarse
            form({"caminos": 3}, nosabe=["caminos"]),               # conflicto puntuación + No sé
            form({"caminos": 3}, relacion="inventada"),
            form({"caminos": 3}, frecuencia="siempre"),
            form({"caminos": 3}, usos=["pasear", "hackeo"]),
            form({"caminos": 3}, comentario_general="x" * 1001),
            form({"caminos": 3}, comments={"caminos": "y" * 501}),
            form({}, comentario_general="corto"),                   # comentario no sustancial
            form({}, nosabe=["caminos", "juegos"]),                 # solo «No sé»: no alcanza
        ]
        for i, data in enumerate(bad):
            r = self.client.post("/review", data=data)
            self.assertEqual(r.status_code, 400, f"caso {i}: {data}")
        self.assertEqual(self.q("SELECT COUNT(*) c FROM evaluaciones")[0]["c"], 0)

    def test_error_page_keeps_user_input(self):
        r = self.client.post("/review", data=form({"caminos": 9}, comentario_general="Texto que no debe perderse"))
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Texto que no debe perderse", html)
        self.assertIn("no es válida", html)

    def test_only_substantial_comment_is_enough(self):
        r = self.client.post("/review", data=form({}, comentario_general="Falta mantenimiento en toda la plaza."))
        self.assertEqual(r.status_code, 302)
        rows = self.q("SELECT puntuacion FROM evaluacion_categorias")
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(r["puntuacion"] is None for r in rows))

    def test_sql_injection_text_stored_literally(self):
        evil = "'); DROP TABLE evaluaciones; --"
        r = self.client.post("/review", data=form({"caminos": 3}, comments={"caminos": evil}))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.q("SELECT comentario FROM evaluacion_categorias WHERE categoria='caminos'")[0]["comentario"], evil)

    def test_whitespace_only_comment_not_stored_as_comment(self):
        self.client.post("/review", data=form({"caminos": 3}, comments={"caminos": "   \n  "}, comentario_general="   "))
        self.assertEqual(self.q("SELECT comentario FROM evaluacion_categorias WHERE categoria='caminos'")[0]["comentario"], "")
        self.login()
        self.assertEqual(self.stats()["with_comments"], 0)


class DashboardTests(Base):
    def post(self, **kw):
        r = self.client.post("/review", data=form(**kw))
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])

    def test_auth_required(self):
        self.assertEqual(self.client.get("/analistas").status_code, 302)
        self.assertEqual(self.client.get("/api/stats").status_code, 401)
        self.assertEqual(self.client.get("/api/comments").status_code, 401)
        self.assertEqual(self.client.get("/analistas/export.csv").status_code, 302)
        bad = self.client.post("/analistas/login", data={"password": "incorrecta"})
        self.assertEqual(bad.status_code, 200)
        self.assertIn("Contraseña incorrecta", bad.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/stats").status_code, 401)
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.client.get("/analistas").status_code, 200)
        self.assertEqual(self.client.get("/api/stats").status_code, 200)
        self.client.get("/analistas/logout")
        self.assertEqual(self.client.get("/api/stats").status_code, 401)

    def test_empty_database(self):
        self.login()
        s = self.stats()
        self.assertEqual(s["total"], 0)
        self.assertIsNone(s["overall"]["avg"])
        self.assertIsNone(s["best"]); self.assertIsNone(s["worst"])
        self.assertEqual(s["ranking"], [])
        self.assertTrue(all(c["avg"] is None and c["n"] == 0 for c in s["categories"]))

    def test_averages_ignore_unrated_and_dont_know(self):
        """Los promedios ignoran categorías sin puntuar y «No sé» (no cuentan como 0)."""
        self.post(ratings={"caminos": 5, "iluminacion": 1})
        self.post(ratings={"caminos": 3}, nosabe=["iluminacion"])
        self.post(ratings={}, comentario_general="Solo comento, sin puntuar nada.")
        self.post(ratings={"iluminacion": 2}, nosabe=["caminos"])
        self.login()
        s = self.stats()
        cat = {c["key"]: c for c in s["categories"]}
        self.assertEqual(s["total"], 4)
        self.assertEqual(cat["caminos"]["n"], 2)
        self.assertEqual(cat["caminos"]["avg"], 4.0)                 # (5+3)/2, no (5+3+0+0)/4
        self.assertEqual(cat["caminos"]["no_sabe"], 1)
        self.assertEqual(cat["iluminacion"]["n"], 2)
        self.assertEqual(cat["iluminacion"]["avg"], 1.5)             # (1+2)/2
        self.assertEqual(cat["iluminacion"]["no_sabe"], 1)
        self.assertEqual(cat["iluminacion"]["dist"], {"1": 1, "2": 1, "3": 0, "4": 0, "5": 0})
        self.assertEqual(cat["caminos"]["sin_respuesta"], 1)         # la evaluación que solo comentó
        self.assertIsNone(cat["verde"]["avg"])                        # nadie la puntuó: no es 0
        self.assertEqual(s["overall"], {"avg": 2.75, "n": 4})         # (5+1+3+2)/4
        self.assertEqual(s["best"]["key"], "caminos")
        self.assertEqual(s["worst"]["key"], "iluminacion")
        self.assertEqual(cat["iluminacion"]["nivel"], "muy_malo")
        self.assertEqual(cat["caminos"]["nivel"], "bueno")
        self.assertTrue(cat["caminos"]["low_sample"])
        self.assertEqual(s["ranking"][:2], ["iluminacion", "caminos"])

    def test_level_thresholds(self):
        self.assertEqual(plaza.level_for(1.0)[0], "muy_malo")
        self.assertEqual(plaza.level_for(1.99)[0], "muy_malo")
        self.assertEqual(plaza.level_for(2.0)[0], "mejoras")
        self.assertEqual(plaza.level_for(2.99)[0], "mejoras")
        self.assertEqual(plaza.level_for(3.0)[0], "regular")
        self.assertEqual(plaza.level_for(3.99)[0], "regular")
        self.assertEqual(plaza.level_for(4.0)[0], "bueno")
        self.assertEqual(plaza.level_for(5.0)[0], "bueno")
        self.assertEqual(plaza.level_for(None), (None, None))

    def test_comments_appear_and_are_separated(self):
        self.post(ratings={"seguridad": 2}, comments={"seguridad": "Poca gente de noche", "verde": "Pasto seco"},
                  mejora_prioritaria="Más luminarias", comentario_general="Comentario general de prueba")
        self.post(ratings={"seguridad": 4})                         # sin comentarios
        self.login()
        s = self.stats()
        self.assertEqual(s["with_comments"], 1)
        seg = next(c for c in s["categories"] if c["key"] == "seguridad")
        self.assertEqual([m["texto"] for m in seg["comments"]], ["Poca gente de noche"])
        self.assertEqual(seg["n_comentarios"], 1)

        allc = self.client.get("/api/comments").get_json()
        self.assertEqual(allc["total"], 4)
        by_tipo = {}
        for it in allc["items"]:
            by_tipo.setdefault(it["tipo"], []).append(it["texto"])
        self.assertEqual(sorted(by_tipo["categoria"]), ["Pasto seco", "Poca gente de noche"])
        self.assertEqual(by_tipo["general"], ["Comentario general de prueba"])
        self.assertEqual(by_tipo["mejora"], ["Más luminarias"])
        self.assertTrue(all(it["texto"].strip() for it in allc["items"]))   # nada vacío

        gen = self.client.get("/api/comments", query_string={"tipo": "general"}).get_json()
        self.assertEqual({i["tipo"] for i in gen["items"]}, {"general", "mejora"})
        cat = self.client.get("/api/comments", query_string={"tipo": "categoria"}).get_json()
        self.assertEqual({i["tipo"] for i in cat["items"]}, {"categoria"})
        # filtro por categoría: solo sus comentarios, sin generales
        fc = self.client.get("/api/comments", query_string={"categoria": "verde"}).get_json()
        self.assertEqual([i["texto"] for i in fc["items"]], ["Pasto seco"])
        # filtro por puntuación
        fp = self.client.get("/api/comments", query_string={"puntuacion": "2"}).get_json()
        self.assertEqual([i["texto"] for i in fp["items"]], ["Poca gente de noche"])
        fp = self.client.get("/api/comments", query_string={"puntuacion": "5"}).get_json()
        self.assertEqual(fp["total"], 0)

    def test_filters_frecuencia_and_dates(self):
        self.post(ratings={"caminos": 5}, frecuencia="diaria")
        self.post(ratings={"caminos": 1}, frecuencia="casi_nunca")
        self.login()
        self.assertEqual(self.stats(frecuencia="diaria")["categories"][0]["avg"], 5.0)
        self.assertEqual(self.stats(frecuencia="casi_nunca")["categories"][0]["avg"], 1.0)
        self.assertEqual(self.stats()["categories"][0]["avg"], 3.0)
        # fechas: fuerza created_at conocido (UTC) y verifica el desplazamiento horario local (-3)
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE evaluaciones SET created_at='2026-10-01 02:30:00' WHERE id=1")   # 30/9 23:30 hora local
        conn.execute("UPDATE evaluaciones SET created_at='2026-10-05 12:00:00' WHERE id=2")
        conn.commit(); conn.close()
        self.assertEqual(self.stats(desde="2026-10-01")["total"], 1)
        self.assertEqual(self.stats(hasta="2026-09-30")["total"], 1)
        self.assertEqual(self.stats(desde="2026-09-30", hasta="2026-10-05")["total"], 2)
        self.assertEqual(self.stats(desde="2026-10-06")["total"], 0)

    def test_invalid_filters_rejected(self):
        self.login()
        for qs in ({"categoria": "deportes"}, {"desde": "31/12/2026"}, {"puntuacion": "7"}, {"frecuencia": "x"},
                   {"desde": "2026-10-05", "hasta": "2026-10-01"}):
            self.assertEqual(self.client.get("/api/stats", query_string=qs).status_code, 400, qs)
            self.assertEqual(self.client.get("/api/comments", query_string=qs).status_code, 400, qs)
        self.assertEqual(self.client.get("/api/comments", query_string={"limit": "abc"}).status_code, 400)
        self.assertEqual(self.client.get("/api/comments", query_string={"tipo": "x"}).status_code, 400)

    def test_csv_export(self):
        self.post(ratings={"caminos": 4, "juegos": 2}, nosabe=["seguridad"], comments={"juegos": "=HYPERLINK(\"x\")"},
                  relacion="paso", frecuencia="semanal", usos=["pasear"], comentario_general="General, con coma\ny salto")
        self.post(ratings={}, comentario_general="Solo un comentario de más de veinte caracteres")
        self.login()
        r = self.client.get("/analistas/export.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.content_type)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        text = r.get_data(as_text=True)
        self.assertTrue(text.startswith("\ufeff"))
        rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
        self.assertEqual(len(rows), 2)
        a, b = rows
        for k in plaza.CATEGORY_KEYS:
            self.assertIn(f"{k}_puntuacion", a)
        self.assertEqual((a["caminos_puntuacion"], a["juegos_puntuacion"]), ("4", "2"))
        self.assertEqual(a["seguridad_puntuacion"], "")              # vacío, no 0
        self.assertEqual(a["seguridad_no_sabe"], "sí")
        self.assertEqual(a["iluminacion_puntuacion"], "")
        self.assertTrue(a["juegos_comentario"].startswith("'="))     # sin inyección de fórmulas
        self.assertEqual(a["comentario_general"], "General, con coma\ny salto")
        self.assertEqual((a["relacion"], a["frecuencia"], a["usos"]), ("Paso por la zona seguido", "Una vez por semana", "Caminar o pasear"))
        self.assertTrue(all(b[f"{k}_puntuacion"] == "" for k in plaza.CATEGORY_KEYS))
        self.assertTrue(all(x["formulario"] == "actual" for x in rows))

    def test_xss_is_escaped_in_server_rendered_html(self):
        payload = "<script>alert(1)</script>"
        r = self.client.post("/review", data=form({"caminos": 9}, comentario_general=payload))
        self.assertEqual(r.status_code, 400)
        html = r.get_data(as_text=True)
        self.assertNotIn(payload, html)
        self.assertIn("&lt;script&gt;", html)


class LegacyTests(Base):
    def setup_db(self):
        make_legacy_db(self.db)

    def test_legacy_kept_separate_and_exported(self):
        self.login()
        s = self.stats()
        self.assertEqual(s["total"], 0)                               # no se mezclan con las evaluaciones nuevas
        self.assertIsNone(s["overall"]["avg"])
        self.assertTrue(all(c["n"] == 0 for c in s["categories"]))
        L = s["legacy"]
        self.assertEqual(L["total"], 5)
        self.assertEqual(L["averages"]["infraestructura"], 2.6)       # (3+1+1+5+3)/5
        texts = [c["texto"] for c in L["comments"]]
        self.assertIn("Quiero juegos para niños", texts)
        self.assertIn("Peligroso de noche", texts)
        self.assertEqual(self.stats(desde="2026-10-15")["legacy"]["total"], 3)
        self.assertFalse(self.stats(frecuencia="diaria")["legacy"]["applicable"])

        csv_text = self.client.get("/analistas/export.csv").get_data(as_text=True).lstrip("\ufeff")
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["formulario"] == "anterior" for r in rows))
        self.assertTrue(all(r[f"{k}_puntuacion"] == "" for r in rows for k in plaza.CATEGORY_KEYS))
        self.assertEqual(rows[0]["anterior_seguridad_puntuacion"], "1")
        self.assertEqual(rows[0]["anterior_sugerencia"], "Quiero juegos para niños")

    def test_new_and_legacy_coexist(self):
        self.client.post("/review", data=form({"caminos": 4}))
        self.login()
        s = self.stats()
        self.assertEqual(s["total"], 1)
        self.assertEqual(s["legacy"]["total"], 5)
        self.assertEqual(len(self.q("SELECT * FROM reviews")), 5)
        csv_text = self.client.get("/analistas/export.csv").get_data(as_text=True).lstrip("\ufeff")
        self.assertEqual(len(list(csv.DictReader(io.StringIO(csv_text)))), 6)


class RealDatabaseUntouchedTests(unittest.TestCase):
    def test_tests_did_not_modify_real_db(self):
        if REAL_DB_HASH is None:
            self.skipTest("No hay plaza_reviews.db en la carpeta del proyecto")
        self.assertEqual(_sha(REAL_DB), REAL_DB_HASH)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "backups")))


if __name__ == "__main__":
    unittest.main()
