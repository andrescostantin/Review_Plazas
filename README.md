# Plaza Reviews — evaluación ciudadana de una plaza

Aplicación web (Flask + SQLite) para que los vecinos evalúen una plaza pública y para que los analistas consulten los resultados en un panel privado.

## Qué incluye

- **Formulario público** (`/reseñar`) con 8 categorías: Caminos y accesibilidad, Iluminación, Bancos y espacios de descanso, Limpieza y residuos, Juegos infantiles, Espacios verdes y arbolado, Seguridad, Equipamiento y servicios.
  - Cada categoría: 1 a 5 estrellas (1 Muy malo · 2 Malo · 3 Regular · 4 Bueno · 5 Muy bueno), explicación de qué se evalúa, comentario opcional y la opción «No sé / No puedo evaluarla».
  - «No sé» y las categorías sin puntuar se guardan como **NULL** (nunca como 0) y no entran en ningún promedio.
  - Se puede enviar sin puntuar todo, pero hace falta **al menos una puntuación válida** o un comentario de **20 o más caracteres** en «Lo primero que mejorarías» / «Comentarios adicionales» (constante `MIN_SUBSTANTIAL_COMMENT` en `app.py`).
  - Preguntas generales (todas opcionales): relación con la plaza, frecuencia de uso, para qué la usa, qué es lo primero que mejoraría y comentarios adicionales.
- **Panel de analistas** (`/analistas`, protegido por contraseña): indicadores generales, comparación entre categorías, identificación de problemas, análisis por categoría (promedio, n, distribución 1–5, «No sé», comentarios), comentarios con filtros y exportación CSV.
- **Acceso de analistas** con contraseña por variable de entorno (sin cambios de concepto respecto de la versión anterior).

## Instalación y ejecución

Requiere Python 3.10 o superior. No hay dependencias nuevas: solo Flask.

### Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:ANALYST_PASSWORD = "una-clave-segura"
$env:SECRET_KEY = "otra-clave-larga-y-aleatoria"
python app.py
```

Si PowerShell bloquea la activación del entorno: `Set-ExecutionPolicy -Scope Process RemoteSigned`.
En `cmd.exe` usá `set ANALYST_PASSWORD=...` y `.venv\Scripts\activate.bat`.

### Linux / macOS

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANALYST_PASSWORD="una-clave-segura" SECRET_KEY="otra-clave-larga-y-aleatoria"
python app.py
```

Direcciones: inicio `http://127.0.0.1:5000/` · formulario `/reseñar` · panel `/analistas`.

### Variables de entorno

| Variable | Por defecto | Uso |
|---|---|---|
| `ANALYST_PASSWORD` | `admin123` (**solo desarrollo**, la app avisa por consola) | Contraseña del panel |
| `SECRET_KEY` | clave de desarrollo (la app avisa) | Firma de la sesión |
| `DB_PATH` | `plaza_reviews.db` junto a `app.py` | Ruta de la base SQLite |
| `HOST` | `127.0.0.1` | Interfaz donde escucha. Para la red local: `0.0.0.0` o tu IP (antes estaba fija en `10.0.1.118`) |
| `PORT` | `5000` | Puerto |
| `FLASK_DEBUG` | `0` | `1` activa el modo debug (no usar fuera de tu máquina) |
| `LOCAL_UTC_OFFSET_HOURS` | `-3` | Huso horario para los filtros por fecha del panel (las fechas se guardan en UTC) |

## Migración de la base de datos

La migración corre **automáticamente al iniciar la app** y también a mano:

```
python migrations.py [ruta/a/plaza_reviews.db]
```

- Es **idempotente**: se registra en la tabla `schema_migrations` y volver a ejecutarla no hace nada.
- Es **segura**: corre en una transacción y verifica que la cantidad de reseñas históricas no cambie; si algo falla, se revierte.
- Antes de migrar una base con datos se crea una **copia de seguridad** (`backups/plaza_reviews-AAAAMMDD-HHMMSS-pre-migracion-v1.db`).
- **No modifica ni borra la tabla `reviews`** original: las reseñas anteriores (formulario de 4 categorías) siguen ahí, tal cual, y no se les inventan puntuaciones nuevas.

### Tablas

| Tabla | Contenido |
|---|---|
| `reviews` (existente, sin cambios) | Reseñas del formulario anterior: `infraestructura`, `seguridad`, `limpieza`, `juegos` (+ `_text`), `sugerencia`, `created_at`. Solo lectura. |
| `evaluaciones` (nueva) | Una fila por envío: `id`, `created_at` (UTC), `relacion`, `frecuencia`, `usos`, `mejora_prioritaria`, `comentario_general`. |
| `evaluacion_categorias` (nueva) | Una fila por categoría y evaluación (siempre 8): `evaluacion_id`, `categoria`, `puntuacion` (1–5 o NULL), `no_sabe` (0/1), `comentario`. Restricciones: puntuación entre 1 y 5, categoría dentro de las 8 válidas, no puede haber puntuación y «No sé» a la vez, una fila por (evaluación, categoría). |
| `schema_migrations` (nueva) | Versiones de migración aplicadas. |

**Cómo se tratan las reseñas anteriores:** las 4 categorías viejas no equivalen a las 8 nuevas, así que no se mezclan con los indicadores ni los promedios. El panel las muestra en una sección aparte («Reseñas del formulario anterior») con sus promedios y comentarios, y el CSV las incluye con `formulario = anterior`.

Nota: el formulario anterior no tenía las preguntas de relación, frecuencia ni uso; esas preguntas son nuevas. El campo viejo `sugerencia` («¿Qué mejorarías?») se conserva en la tabla histórica y la pregunta equivalente actual es «Lo primero que mejorarías» (`mejora_prioritaria`). Las opciones de esas preguntas se editan en `RELACIONES`, `FRECUENCIAS` y `USOS` al comienzo de `app.py`.

## Panel de analistas

- **Indicadores:** total de evaluaciones, promedio general (promedio de **todas las puntuaciones válidas**, con su n), mejor y peor categoría (solo si hay al menos 2 categorías con puntuaciones; se avisa si hay empate), evaluaciones con comentarios.
- **Clasificación orientativa** (sobre el promedio redondeado a 2 decimales): 1,0–1,9 estado muy malo · 2,0–2,9 necesita mejoras importantes · 3,0–3,9 estado regular · 4,0–5,0 buen estado. Es una guía para interpretar la **percepción ciudadana**, no una inspección técnica. Con menos de 5 puntuaciones (`LOW_SAMPLE`) se marca «Pocas respuestas».
- **Filtros:** categoría, rango de fechas, frecuencia de uso y puntuación. Fechas y frecuencia afectan a todo el panel; categoría muestra solo ese análisis y filtra los comentarios; puntuación filtra solo la lista de comentarios.
- **Gráficos:** barras de comparación y distribución 1–5 dibujadas con HTML/CSS a partir de los datos reales. **No usan bibliotecas externas**, así que no dependen de ninguna CDN (se eliminó Chart.js).
- **Recursos externos que siguen existiendo:** las tipografías de Google Fonts (con tipografía de respaldo del sistema) y la foto de fondo de la portada (Unsplash). Sin conexión la app funciona igual, solo cambia el aspecto.

### Exportación CSV

Botón «Exportar CSV» (`/analistas/export.csv`, requiere sesión). Una fila por evaluación, columnas por categoría `*_puntuacion` / `*_no_sabe` / `*_comentario`, más datos generales, comentario general y columnas `anterior_*` para las reseñas viejas. Sin puntuación = celda vacía (nunca 0). UTF-8 con BOM (Excel muestra bien los acentos). Los textos que empiezan con `=`, `+`, `-` o `@` se prefijan con `'` para evitar inyección de fórmulas. Los `id` se repiten entre formularios: usá `id` + `formulario` como clave.

## Pruebas

```
python -m unittest discover -s tests -v
```

Usan bases de datos temporales (no tocan `plaza_reviews.db`). Cubren migración (reseñas conservadas, idempotencia, backup), validación del servidor, almacenamiento de las 8 categorías, promedios que ignoran categorías sin puntuar y «No sé», comentarios, filtros, autenticación, CSV y escape de HTML.

## Seguridad y producción

- Consultas SQL parametrizadas; texto de usuarios escapado (Jinja y `textContent`); comparación de contraseña en tiempo constante; respuestas privadas con `Cache-Control: no-store`; cookie de sesión `HttpOnly` + `SameSite=Lax`.
- No se guardan datos personales (ni IP ni nombre).
- **Antes de publicar:** definí `ANALYST_PASSWORD` y `SECRET_KEY` propias, no uses el servidor de desarrollo (usá Waitress en Windows: `pip install waitress` y `waitress-serve --listen=127.0.0.1:5000 app:app`, o Gunicorn en Linux) y servilo por HTTPS. No hay límite de envíos por persona: si se publica abiertamente, conviene agregar uno en el proxy o la aplicación.
