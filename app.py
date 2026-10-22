import csv
import hmac
import io
import os
import re
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (Flask, Response, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)

from werkzeug.datastructures import MultiDict

import migrations

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# Catálogo del formulario (un único lugar para editar textos y opciones)
# --------------------------------------------------------------------------
CATEGORIES = [
    {"key": "caminos", "name": "Caminos y accesibilidad",
     "desc": "Estado de veredas, senderos y rampas; desniveles o superficies deterioradas; y qué tan fácil es circular, incluso con movilidad reducida."},
    {"key": "iluminacion", "name": "Iluminación",
     "desc": "Si las luminarias funcionan, si hay zonas oscuras, cómo se distribuye la luz y qué tan bien se ve de noche."},
    {"key": "bancos", "name": "Bancos y espacios de descanso",
     "desc": "Cantidad, comodidad y estado de conservación de bancos, mesas y otros lugares para sentarse y descansar."},
    {"key": "limpieza", "name": "Limpieza y residuos",
     "desc": "Limpieza general, presencia de basura, estado y cantidad de cestos, residuos acumulados y mantenimiento del espacio."},
    {"key": "juegos", "name": "Juegos infantiles",
     "desc": "Estado, mantenimiento, seguridad y accesibilidad de los juegos para chicos."},
    {"key": "verde", "name": "Espacios verdes y arbolado",
     "desc": "Estado del césped, los árboles y las plantas; la sombra disponible y el mantenimiento de los espacios verdes."},
    {"key": "seguridad", "name": "Seguridad",
     "desc": "Cómo te sentís en la plaza, qué sectores te generan preocupación y qué condiciones del entorno afectan tu tranquilidad."},
    {"key": "equipamiento", "name": "Equipamiento y servicios",
     "desc": "Estado y disponibilidad de bebederos, baños públicos, bicicleteros y otros servicios o equipamientos."},
]
CATEGORY_KEYS = [c["key"] for c in CATEGORIES]
CATEGORY_NAMES = {c["key"]: c["name"] for c in CATEGORIES}
assert CATEGORY_KEYS == list(migrations.CATEGORY_KEYS)

RATING_LABELS = {1: "Muy malo", 2: "Malo", 3: "Regular", 4: "Bueno", 5: "Muy bueno"}

RELACIONES = {
    "vivo_cerca": "Vivo cerca de la plaza",
    "trabajo_estudio": "Trabajo o estudio cerca",
    "paso": "Paso por la zona seguido",
    "visito": "La visito de vez en cuando",
    "otra": "Otra relación",
}
FRECUENCIAS = {
    "diaria": "Todos los días",
    "semanal_varias": "Varias veces por semana",
    "semanal": "Una vez por semana",
    "mensual": "Algunas veces al mes",
    "casi_nunca": "Casi nunca",
    "primera_vez": "Es la primera vez que voy",
}
USOS = {
    "pasear": "Caminar o pasear",
    "descansar": "Descansar o tomar mate",
    "ninos": "Llevar a chicos a jugar",
    "mascotas": "Pasear a mi mascota",
    "encuentro": "Encontrarme con otras personas",
    "transito": "Atravesarla para ir a otro lugar",
    "otro": "Otro motivo",
}
LEGACY_LABELS = {"infraestructura": "Infraestructura", "seguridad": "Seguridad",
                 "limpieza": "Limpieza", "juegos": "Juegos"}

MIN_SUBSTANTIAL_COMMENT = 20     # caracteres para considerar "sustancial" un comentario general
MAX_CATEGORY_COMMENT = 500
MAX_MEJORA = 500
MAX_GENERAL = 1000
LOW_SAMPLE = 5                   # por debajo de esta cantidad de puntuaciones se avisa "pocas respuestas"

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class FilterError(ValueError):
    pass


def level_for(avg):
    """Clasificación orientativa según el promedio ya redondeado a 2 decimales."""
    if avg is None:
        return None, None
    a = round(avg, 2)
    if a < 2:
        return "muy_malo", "Estado muy malo"
    if a < 3:
        return "mejoras", "Necesita mejoras importantes"
    if a < 4:
        return "regular", "Estado regular"
    return "bueno", "Buen estado"


def rnd(x):
    return None if x is None else round(x, 2)


def iso(ts):
    return ts.replace(" ", "T") + "Z" if ts else None


# --------------------------------------------------------------------------
# Base de datos
# --------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(current_app.config["DB_PATH"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def init_db(db_path):
    """Crea/actualiza el esquema sin perder datos (ver migrations.py)."""
    return migrations.apply_migrations(db_path)


# --------------------------------------------------------------------------
# Validación del formulario
# --------------------------------------------------------------------------
def clean_text(raw, max_len, label, errors):
    text = _CTRL.sub("", (raw or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    if len(text) > max_len:
        errors.append(f"«{label}» es demasiado largo (máximo {max_len} caracteres; escribiste {len(text)}).")
    return text


def parse_submission(form):
    """Valida en el servidor, independientemente del JavaScript. Devuelve (datos, errores)."""
    errors = []

    relacion = (form.get("relacion") or "").strip()
    if relacion and relacion not in RELACIONES:
        errors.append("La opción elegida en «Relación con la plaza» no es válida.")
        relacion = ""
    frecuencia = (form.get("frecuencia") or "").strip()
    if frecuencia and frecuencia not in FRECUENCIAS:
        errors.append("La opción elegida en «Frecuencia de uso» no es válida.")
        frecuencia = ""
    usos = form.getlist("usos")
    if any(u not in USOS for u in usos):
        errors.append("Alguna opción elegida en «¿Para qué la usás?» no es válida.")
    usos = [u for u in USOS if u in usos]  # orden canónico, sin duplicados

    categorias = {}
    valid_scores = 0
    for c in CATEGORIES:
        key, name = c["key"], c["name"]
        raw = (form.get(f"rating_{key}") or "").strip()
        if raw == "":
            score = None
        elif raw in ("1", "2", "3", "4", "5"):
            score = int(raw)
        else:
            errors.append(f"La puntuación de «{name}» no es válida: tiene que ser un número de 1 a 5.")
            score = None
        no_sabe = form.get(f"nosabe_{key}") == "1"
        if no_sabe and score is not None:
            errors.append(f"En «{name}» elegiste una puntuación y también «No sé / No puedo evaluarla». Elegí solo una de las dos.")
            no_sabe = False
        comentario = clean_text(form.get(f"comentario_{key}"), MAX_CATEGORY_COMMENT, f"Comentario de {name}", errors)
        if score is not None:
            valid_scores += 1
        categorias[key] = {"puntuacion": score, "no_sabe": int(no_sabe), "comentario": comentario}

    mejora = clean_text(form.get("mejora_prioritaria"), MAX_MEJORA, "Lo primero que mejorarías", errors)
    general = clean_text(form.get("comentario_general"), MAX_GENERAL, "Comentarios o sugerencias adicionales", errors)

    if valid_scores == 0 and len(general) < MIN_SUBSTANTIAL_COMMENT and len(mejora) < MIN_SUBSTANTIAL_COMMENT:
        errors.append(
            f"Para enviar tu opinión puntuá al menos un aspecto de la plaza o escribí un comentario de al menos "
            f"{MIN_SUBSTANTIAL_COMMENT} caracteres en «Lo primero que mejorarías» o en «Comentarios o sugerencias adicionales».")

    data = {"relacion": relacion or None, "frecuencia": frecuencia or None, "usos": ",".join(usos),
            "mejora_prioritaria": mejora, "comentario_general": general, "categorias": categorias}
    return data, errors


# --------------------------------------------------------------------------
# Aplicación
# --------------------------------------------------------------------------
def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "cambiar-esta-clave-secreta"),
        DB_PATH=os.environ.get("DB_PATH", os.path.join(BASE_DIR, "plaza_reviews.db")),
        ANALYST_PASSWORD=os.environ.get("ANALYST_PASSWORD", "admin123"),
        LOCAL_UTC_OFFSET_HOURS=int(os.environ.get("LOCAL_UTC_OFFSET_HOURS", "-3")),  # Argentina
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=256 * 1024,
    )
    if overrides:
        app.config.update(overrides)

    if "ANALYST_PASSWORD" not in os.environ and "ANALYST_PASSWORD" not in (overrides or {}):
        print("AVISO: ANALYST_PASSWORD no está configurada; se usa la contraseña por defecto de desarrollo. "
              "Configurala antes de publicar.")
    if "SECRET_KEY" not in os.environ and "SECRET_KEY" not in (overrides or {}):
        print("AVISO: SECRET_KEY no está configurada; se usa una clave de desarrollo.")

    init_db(app.config["DB_PATH"])

    def analyst_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get("analyst"):
                if request.path.startswith("/api/"):
                    return jsonify({"error": "No autenticado"}), 401
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return wrapper

    @app.after_request
    def no_cache_private(resp):
        if request.path.startswith(("/analistas", "/api/")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---------------- páginas públicas ----------------
    @app.route("/")
    def index():
        return render_template("landing.html")

    def render_form(form=None, errors=None, status=200):
        return render_template(
            "index.html", categories=CATEGORIES, rating_labels=RATING_LABELS,
            relaciones=RELACIONES, frecuencias=FRECUENCIAS, usos=USOS,
            form=form if form is not None else MultiDict(), errors=errors or [],
            min_comment=MIN_SUBSTANTIAL_COMMENT, max_cat=MAX_CATEGORY_COMMENT,
            max_mejora=MAX_MEJORA, max_general=MAX_GENERAL,
        ), status

    @app.route("/reseñar")
    def reseñar():
        return render_form()

    @app.post("/review")
    def review():
        data, errors = parse_submission(request.form)
        if errors:
            return render_form(form=request.form, errors=errors, status=400)

        conn = get_db()
        try:
            with conn:  # una sola transacción: se guarda todo o nada
                cur = conn.execute(
                    """INSERT INTO evaluaciones (relacion, frecuencia, usos, mejora_prioritaria, comentario_general)
                       VALUES (?, ?, ?, ?, ?)""",
                    (data["relacion"], data["frecuencia"], data["usos"],
                     data["mejora_prioritaria"], data["comentario_general"]))
                eval_id = cur.lastrowid
                conn.executemany(
                    """INSERT INTO evaluacion_categorias (evaluacion_id, categoria, puntuacion, no_sabe, comentario)
                       VALUES (?, ?, ?, ?, ?)""",
                    [(eval_id, k, v["puntuacion"], v["no_sabe"], v["comentario"])
                     for k, v in data["categorias"].items()])
        finally:
            conn.close()
        return redirect(url_for("gracias"))

    @app.get("/gracias")
    def gracias():
        return render_template("thanks.html")

    # ---------------- acceso de analistas ----------------
    @app.route("/analistas/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            supplied = (request.form.get("password") or "").encode("utf-8")
            expected = app.config["ANALYST_PASSWORD"].encode("utf-8")
            if hmac.compare_digest(supplied, expected):
                session["analyst"] = True
                return redirect(url_for("dashboard"))
            flash("Contraseña incorrecta.")
        return render_template("login.html")

    @app.get("/analistas/logout")
    def logout():
        session.clear()
        return redirect(url_for("reseñar"))

    @app.get("/analistas")
    @analyst_required
    def dashboard():
        return render_template("dashboard.html", categories=CATEGORIES, frecuencias=FRECUENCIAS,
                               rating_labels=RATING_LABELS, low_sample=LOW_SAMPLE)

    # ---------------- filtros y consultas del panel ----------------
    def parse_filters(args):
        f = {k: (args.get(k) or "").strip() for k in ("categoria", "desde", "hasta", "frecuencia", "puntuacion")}
        if f["categoria"] and f["categoria"] not in CATEGORY_KEYS:
            raise FilterError("Categoría desconocida.")
        for k in ("desde", "hasta"):
            if f[k]:
                try:
                    datetime.strptime(f[k], "%Y-%m-%d")
                except ValueError:
                    raise FilterError(f"Fecha inválida en «{k}»: usá el formato AAAA-MM-DD.")
        if f["desde"] and f["hasta"] and f["desde"] > f["hasta"]:
            raise FilterError("La fecha «desde» es posterior a «hasta».")
        if f["frecuencia"] and f["frecuencia"] not in FRECUENCIAS:
            raise FilterError("Frecuencia desconocida.")
        if f["puntuacion"] and f["puntuacion"] not in ("1", "2", "3", "4", "5"):
            raise FilterError("La puntuación debe ser un número de 1 a 5.")
        return f

    def tz_modifier():
        return f"{int(app.config['LOCAL_UTC_OFFSET_HOURS']):+d} hours"

    def eval_where(f, alias="e"):
        """Filtros a nivel de evaluación (fecha local y frecuencia). Todo parametrizado."""
        clauses, params = ["1=1"], []
        if f["desde"]:
            clauses.append(f"date({alias}.created_at, ?) >= ?")
            params += [tz_modifier(), f["desde"]]
        if f["hasta"]:
            clauses.append(f"date({alias}.created_at, ?) <= ?")
            params += [tz_modifier(), f["hasta"]]
        if f["frecuencia"]:
            clauses.append(f"{alias}.frecuencia = ?")
            params.append(f["frecuencia"])
        return " AND ".join(clauses), params

    def legacy_stats(conn, f):
        """Reseñas del formulario anterior (4 categorías). Se muestran aparte: no equivalen a las 8 actuales."""
        if not _table_exists(conn, "reviews"):
            return {"exists": False, "total": 0}
        if f["frecuencia"]:   # el formulario anterior no tenía este dato
            return {"exists": True, "applicable": False, "total": 0}
        clauses, params = ["1=1"], []
        if f["desde"]:
            clauses.append("date(created_at, ?) >= ?")
            params += [tz_modifier(), f["desde"]]
        if f["hasta"]:
            clauses.append("date(created_at, ?) <= ?")
            params += [tz_modifier(), f["hasta"]]
        where = " AND ".join(clauses)
        row = conn.execute(
            f"""SELECT COUNT(*) AS n, AVG(infraestructura) AS infraestructura, AVG(seguridad) AS seguridad,
                       AVG(limpieza) AS limpieza, AVG(juegos) AS juegos FROM reviews WHERE {where}""", params).fetchone()
        comments = []
        for r in conn.execute(f"SELECT * FROM reviews WHERE {where} ORDER BY created_at DESC, id DESC LIMIT 100", params):
            for k, label in LEGACY_LABELS.items():
                txt = (r[f"{k}_text"] or "").strip()
                if txt:
                    comments.append({"fecha": iso(r["created_at"]), "origen": label, "puntuacion": r[k], "texto": txt})
            sug = (r["sugerencia"] or "").strip()
            if sug:
                comments.append({"fecha": iso(r["created_at"]), "origen": "Sugerencia", "puntuacion": None, "texto": sug})
        return {"exists": True, "applicable": True, "total": row["n"],
                "averages": {k: rnd(row[k]) if row["n"] else None for k in LEGACY_LABELS},
                "labels": LEGACY_LABELS, "comments": comments}

    @app.get("/api/stats")
    @analyst_required
    def stats():
        try:
            f = parse_filters(request.args)
        except FilterError as e:
            return jsonify({"error": str(e)}), 400
        where, params = eval_where(f)
        conn = get_db()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM evaluaciones e WHERE {where}", params).fetchone()[0]
            pooled = conn.execute(
                f"""SELECT COUNT(c.puntuacion) AS n, AVG(c.puntuacion) AS avg
                    FROM evaluacion_categorias c JOIN evaluaciones e ON e.id = c.evaluacion_id WHERE {where}""",
                params).fetchone()
            with_comments = conn.execute(
                f"""SELECT COUNT(*) FROM evaluaciones e WHERE {where} AND (
                        e.comentario_general <> '' OR e.mejora_prioritaria <> '' OR EXISTS (
                            SELECT 1 FROM evaluacion_categorias c
                            WHERE c.evaluacion_id = e.id AND c.comentario <> ''))""", params).fetchone()[0]
            cat_rows = {r["categoria"]: r for r in conn.execute(
                f"""SELECT c.categoria,
                           COUNT(c.puntuacion) AS n,
                           AVG(c.puntuacion) AS avg,
                           SUM(CASE WHEN c.puntuacion = 1 THEN 1 ELSE 0 END) AS d1,
                           SUM(CASE WHEN c.puntuacion = 2 THEN 1 ELSE 0 END) AS d2,
                           SUM(CASE WHEN c.puntuacion = 3 THEN 1 ELSE 0 END) AS d3,
                           SUM(CASE WHEN c.puntuacion = 4 THEN 1 ELSE 0 END) AS d4,
                           SUM(CASE WHEN c.puntuacion = 5 THEN 1 ELSE 0 END) AS d5,
                           SUM(c.no_sabe) AS no_sabe,
                           SUM(CASE WHEN c.comentario <> '' THEN 1 ELSE 0 END) AS ncom
                    FROM evaluacion_categorias c JOIN evaluaciones e ON e.id = c.evaluacion_id
                    WHERE {where} GROUP BY c.categoria""", params)}
            comments_by_cat = {k: [] for k in CATEGORY_KEYS}
            for r in conn.execute(
                f"""SELECT * FROM (
                        SELECT c.categoria, c.puntuacion, c.comentario, e.created_at, e.id AS eid,
                               ROW_NUMBER() OVER (PARTITION BY c.categoria ORDER BY e.created_at DESC, e.id DESC) AS rn
                        FROM evaluacion_categorias c JOIN evaluaciones e ON e.id = c.evaluacion_id
                        WHERE {where} AND c.comentario <> '') WHERE rn <= 15
                    ORDER BY categoria, rn""", params):
                comments_by_cat[r["categoria"]].append(
                    {"fecha": iso(r["created_at"]), "puntuacion": r["puntuacion"], "texto": r["comentario"]})
            legacy = legacy_stats(conn, f)
        finally:
            conn.close()

        categories = []
        for c in CATEGORIES:
            r = cat_rows.get(c["key"])
            n = r["n"] if r else 0
            avg = rnd(r["avg"]) if r and n else None
            nivel, nivel_label = level_for(avg)
            no_sabe = (r["no_sabe"] or 0) if r else 0
            categories.append({
                "key": c["key"], "name": c["name"], "avg": avg, "n": n,
                "dist": {str(i): ((r[f"d{i}"] or 0) if r else 0) for i in range(1, 6)},
                "no_sabe": no_sabe, "sin_respuesta": max(total - n - no_sabe, 0),
                "nivel": nivel, "nivel_label": nivel_label, "low_sample": 0 < n < LOW_SAMPLE,
                "n_comentarios": (r["ncom"] or 0) if r else 0,
                "comments": comments_by_cat[c["key"]],
            })

        rated = [(i, c) for i, c in enumerate(categories) if c["n"] > 0]
        best = worst = None
        if len(rated) >= 2:
            def pack(c, same_avg):
                return {"key": c["key"], "name": c["name"], "avg": c["avg"], "n": c["n"], "tie": same_avg > 1}
            b = min(rated, key=lambda t: (-t[1]["avg"], -t[1]["n"], t[0]))[1]
            w = min(rated, key=lambda t: (t[1]["avg"], -t[1]["n"], t[0]))[1]
            best = pack(b, sum(1 for _, c in rated if c["avg"] == b["avg"]))
            worst = pack(w, sum(1 for _, c in rated if c["avg"] == w["avg"]))
        ranking = [c["key"] for _, c in sorted(rated, key=lambda t: (t[1]["avg"], -t[1]["n"], t[0]))]

        return jsonify({
            "filters": f, "total": total,
            "overall": {"avg": rnd(pooled["avg"]) if pooled["n"] else None, "n": pooled["n"]},
            "best": best, "worst": worst, "comparable": len(rated) >= 2,
            "with_comments": with_comments,
            "categories": categories, "ranking": ranking, "legacy": legacy,
        })

    @app.get("/api/comments")
    @analyst_required
    def comments():
        try:
            f = parse_filters(request.args)
            limit = min(max(int(request.args.get("limit", 30)), 1), 200)
            offset = max(int(request.args.get("offset", 0)), 0)
        except FilterError as e:
            return jsonify({"error": str(e)}), 400
        except ValueError:
            return jsonify({"error": "Parámetros de paginación inválidos."}), 400
        tipo = (request.args.get("tipo") or "todos").strip()
        if tipo not in ("todos", "categoria", "general"):
            return jsonify({"error": "Tipo de comentario desconocido."}), 400

        where, wparams = eval_where(f)
        parts, params = [], []
        if tipo in ("todos", "categoria"):
            sql = f"""SELECT 'categoria' AS tipo, c.categoria AS categoria, c.puntuacion AS puntuacion,
                             c.comentario AS texto, e.created_at AS created_at, e.frecuencia AS frecuencia, e.id AS eid
                      FROM evaluacion_categorias c JOIN evaluaciones e ON e.id = c.evaluacion_id
                      WHERE {where} AND c.comentario <> ''"""
            p = list(wparams)
            if f["categoria"]:
                sql += " AND c.categoria = ?"
                p.append(f["categoria"])
            if f["puntuacion"]:
                sql += " AND c.puntuacion = ?"
                p.append(int(f["puntuacion"]))
            parts.append(sql)
            params += p
        # Los comentarios generales no pertenecen a una categoría ni tienen puntuación:
        # solo se listan cuando no se filtra por categoría ni por puntuación.
        if tipo in ("todos", "general") and not f["categoria"] and not f["puntuacion"]:
            for col, label in (("comentario_general", "general"), ("mejora_prioritaria", "mejora")):
                parts.append(f"""SELECT '{label}' AS tipo, NULL AS categoria, NULL AS puntuacion, e.{col} AS texto,
                                        e.created_at AS created_at, e.frecuencia AS frecuencia, e.id AS eid
                                 FROM evaluaciones e WHERE {where} AND e.{col} <> ''""")
                params += list(wparams)
        if not parts:
            return jsonify({"total": 0, "items": []})

        union = " UNION ALL ".join(parts)
        conn = get_db()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM ({union})", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM ({union}) ORDER BY created_at DESC, eid DESC LIMIT ? OFFSET ?",
                params + [limit, offset]).fetchall()
        finally:
            conn.close()
        return jsonify({"total": total, "items": [{
            "tipo": r["tipo"], "categoria": r["categoria"],
            "categoria_nombre": CATEGORY_NAMES.get(r["categoria"]),
            "puntuacion": r["puntuacion"], "texto": r["texto"], "fecha": iso(r["created_at"]),
            "frecuencia": FRECUENCIAS.get(r["frecuencia"]),
        } for r in rows]})

    # ---------------- exportación CSV ----------------
    def csv_safe(value):
        """Evita inyección de fórmulas al abrir el CSV en Excel/LibreOffice."""
        if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
            return "'" + value
        return value

    @app.get("/analistas/export.csv")
    @analyst_required
    def export_csv():
        legacy_keys = list(LEGACY_LABELS)
        header = ["id", "formulario", "fecha_utc", "relacion", "frecuencia", "usos"]
        for k in CATEGORY_KEYS:
            header += [f"{k}_puntuacion", f"{k}_no_sabe", f"{k}_comentario"]
        header += ["mejora_prioritaria", "comentario_general"]
        for k in legacy_keys:
            header += [f"anterior_{k}_puntuacion", f"anterior_{k}_comentario"]
        header += ["anterior_sugerencia"]

        conn = get_db()
        try:
            rows = []
            cats = {}
            for r in conn.execute("SELECT * FROM evaluacion_categorias"):
                cats.setdefault(r["evaluacion_id"], {})[r["categoria"]] = r
            for e in conn.execute("SELECT * FROM evaluaciones"):
                row = [e["id"], "actual", e["created_at"],
                       RELACIONES.get(e["relacion"], ""), FRECUENCIAS.get(e["frecuencia"], ""),
                       "; ".join(USOS[u] for u in e["usos"].split(",") if u in USOS)]
                for k in CATEGORY_KEYS:
                    c = cats.get(e["id"], {}).get(k)
                    if c is None:
                        row += ["", "", ""]
                    else:
                        row += ["" if c["puntuacion"] is None else c["puntuacion"],
                                "sí" if c["no_sabe"] else "no", c["comentario"]]
                row += [e["mejora_prioritaria"], e["comentario_general"]]
                row += [""] * (len(legacy_keys) * 2 + 1)
                rows.append((e["created_at"], 1, e["id"], row))
            if _table_exists(conn, "reviews"):
                for r in conn.execute("SELECT * FROM reviews"):
                    row = [r["id"], "anterior", r["created_at"], "", "", ""]
                    row += [""] * (len(CATEGORY_KEYS) * 3 + 2)
                    for k in legacy_keys:
                        row += [r[k], r[f"{k}_text"] or ""]
                    row += [r["sugerencia"] or ""]
                    rows.append((r["created_at"], 0, r["id"], row))
        finally:
            conn.close()
        rows.sort(key=lambda t: (t[0] or "", t[1], t[2]))

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        for _, _, _, row in rows:
            w.writerow([csv_safe(v) for v in row])
        body = "\ufeff" + buf.getvalue()  # BOM para que Excel respete los acentos
        fname = f"plaza_reviews_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        return Response(body, mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG", "0") == "1")
