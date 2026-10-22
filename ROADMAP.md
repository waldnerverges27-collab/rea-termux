# ROADMAP — qué mejorar y qué añadir

Evaluación honesta del estado actual y plan priorizado por **valor / esfuerzo**.
Referencias: `archivo:línea` aproximada y módulo afectado.

---

## Estado

- **Tier 0 — HECHO y validado contra REA 6.7.0 real y el APK `org.fdroid.fdroid` 1.21.1.**
  Ver "Hallazgos reales" al final del Tier 0.
- **Tier 1.1–1.7 — HECHOS**: autocorrección de build, versionado, streaming, salidas estructuradas, visión, layout y análisis estático de APK.
- **Siguiente: Tier 1.8 — navegación real e internacionalización.**

## Tier 0 — Validar antes de seguir añadiendo (riesgo alto, coste bajo)

Todo el end-to-end se probó con dobles (`tests/fake_mcp_server.py`, `adb`/`npx`
simulados, proveedor local). Los nombres de comandos y flags del CLI de REA
(`rea_cli.py`) y los nombres de herramientas MCP vienen de documentación, no de
una ejecución real. Si algo no cuadra, el programa falla en el primer paso.

| # | Qué | Cómo | Esfuerzo |
|---|---|---|---|
| 0.1 | ✅ **`rea-termux probe`**: ejecuta el REA real, valida flags, forma del JSON, tools MCP y el jar de JADX. | `probe.py`, `cli.py cmd_probe`, `doctor --probe-rea` | hecho |
| 0.2 | ✅ **Descubrimiento de capacidades**: `rea --help` → 155 comandos; tabla de alias (`inspect_android_package`, `inspect_apk_signature`, `mcp`) usada por `rea_cli`. | `probe.COMMAND_ALIASES`, `rea_cli.command_for()` | hecho |
| 0.3 | ⏳ Tests de contrato con un APK real como fixture (hoy: prueba manual con F-Droid). | `tests/fixtures/` + `pytest -m contract` | 2 h |
| 0.4 | ✅ **Telemetría local de fallos** en `<workspace>/analysis/failures.jsonl` (argv, rc, duración, stderr). | `rea_cli.set_fail_log()` | hecho |
| 0.5 | ✅ **Sobres de error**: REA devuelve `{code, category, message, remediation, details}` **con exit 0**; ahora se detectan, se traduce a acciones y `analyze` aborta con exit 3. | `rea_cli.detect_error_envelope`, `probe.suggest_from_error` | hecho |
| 0.6 | ✅ **Parser de manifest real**: `normalized_result.manifest.text` es el XML decodificado → actividades, servicios, receivers, providers, `exported`, intent-filters, deep links, features, clase Application. | nuevo `manifest.py` | hecho |

> Recomendación: hacer 0.1 y 0.4 antes que cualquier funcionalidad nueva.

---

## Tier 1 — Lo que más multiplica el valor

### 1.1 Bucle de auto-reparación de compilación (self-healing build) ⭐
Hoy el agente escribe código pero **nadie verifica que compile**. Añadir:
`write → `flutter analyze`/`gradle compileDebugKotlin` → parsear errores → corregir → repetir (máx. N) → registrar en memoria`.
- Módulo nuevo `verify.py` con *adaptadores* por stack y parser de errores a `{file, line, message}`.
- Integrar en `session.turn()` y en un comando `rea-termux verify <ws>`.
- Valor: alto (convierte "código plausible" en "código que compila"). Esfuerzo: 1 día.

### 1.2 ✅ Git en el workspace: undo, diff y revisión de cambios
El agente edita archivos reales sin red de seguridad.
- `git init` + commit inicial en `analyze`/`build`; **commit automático por turno** con mensaje = resumen del turno.
- `/undo` y `/diff` en el chat; `rea-termux log|restore`.
- Modo `--dry-run`: el agente propone cambios y se aplican tras aprobación (hoy `write_file` escribe directo).
- Hecho: `vcs.py` (init/log/diff/undo/restore/snapshot), commit por turno con el
  resumen del agente y el pedido del usuario en el cuerpo, `/undo /diff /log
  /pending /apply /help` en el REPL, `--dry-run` con diffs unificados y
  `rea-termux vcs ...`, guardas de 5 MB, y no toca repos ajenos sin `--adopt`.

### 1.3 ✅ Streaming de respuestas
`providers.py` usa `"stream": false`. En un teléfono, las esperas largas parecen cuelgues.
- SSE incremental + ensamblado de `tool_calls` fragmentados; fallback si el proveedor no streamea.
- Spinner con tokens/seg y tiempo por herramienta.
- Hecho: `stream.py` (`StreamAssembler` + `parse_sse`), `Router.stream`,
  `ProviderCfg.supports_stream`, `--no-stream`; si el proveedor devuelve JSON en
  vez de SSE o corta el stream, se degrada a petición normal y no reintenta.

### 1.4 ✅ Salidas estructuradas (JSON Schema)
El plan y los análisis dependen de prosa. Usar `response_format: {type: json_schema}` cuando el proveedor lo soporte para: plan de pantalla, lista de huecos, decisiones del turno.
- Validación con un esquema propio + reintento si no valida.
- `providers.py`: detectar soporte y degradar a "pide JSON en el prompt".
- Hecho: `schemas.py` (PLAN/UNKNOWNS/FLOW + `strictify` + `validate` + `coerce` +
  `extract_json`), `Router.structured()` con degradación json_schema → json_object
  → prompt y reintento con el error como feedback; `analyze` genera
  `analysis/unknowns.json` y `plan --structured` genera `PLAN_STRUCTURED.json`
  que se inserta en `CLONE_PLAN.md`.

### 1.5 Visión: leer la captura con un modelo multimodal ⭐ — ✅ hecho
`uicapture` depende de `uiautomator`, que devuelve **un solo nodo** en Flutter, WebView y parte de Compose. El modo opcional `ui --vision` complementa ese árbol con una lectura visual.
- Intenta `capture_adb_screen` de REA como fuente primaria; si falta, falla o no entrega PNG, usa `adb screencap`.
- Envía `ui/*.png` como `image_url` base64 solo a proveedores declarados `supports_vision = true`; por defecto está desactivado.
- `vision.py` valida tamaño y firma PNG, exige un layout JSON conforme a esquema y registra tipo de pantalla, componentes, posición, navegación, accesibilidad, mejoras y confianza.
- Persiste observaciones y proveedor/modelo en `ui/<pantalla>.json`; las incorpora a `DESIGN.md` sin reemplazar la evidencia local. La transferencia al proveedor es opt-in, por riesgo de datos visibles.
- Pruebas: captura MCP con imagen, fallback controlado, carga multimodal HTTP simulada, validación de esquema y gating de capacidad.

### 1.6 Inferencia de layout real — ✅ hecho
- Nuevo `layout.py`: análisis geométrico sin dependencias; clasifica orientación, app bar, navegación inferior, FAB, filas, columnas, listas, grids y tarjetas agrupadas por contención.
- `screen_plan()` conserva hijos semánticos dentro de tarjetas/listas y añade el árbol inferido; `DESIGN.md` expone el resultado.
- Los generadores Flutter, Compose y React ahora consumen filas/grids, tarjetas, navegación y FAB en vez de aplanar todos los elementos como una columna.
- Fallback explícito y conservador cuando faltan bounds: mantiene orden en columna y registra diagnóstico, sin afirmar detecciones geométricas.
- Pruebas con pantallas sintéticas 2×2, contenedores anidados, app bar, bottom nav, FAB y bounds ausentes.

### 1.7 Analizador estático propio (independiente de REA) — ✅ hecho
- Nuevo `static_analysis.py`: inspecciona strings y paths del ZIP/APK con límites de tamaño/entradas; no ejecuta el paquete.
- `android_tools.py` + bootstrap/doctor: APKTool, aapt/aapt2, apksigner, zipalign, adb, Gradle y deteccion del SDK platform (`android.jar`); `analyze` ejecuta verificacion de firma/alineacion y decodificacion de recursos local si las herramientas existen.
- Detecta URLs/hosts (query strings redactadas), Firebase config, deep links del manifest, referencias literales de permisos, SDKs/frameworks conocidos y posibles secretos por patrones + entropía de Shannon.
- **Nunca persiste valores de secretos**: se guardan tipo, archivo, confianza, longitud y entropía opcional; candidatos de alta entropía se marcan para revisión y el umbral se calibró contra F-Droid real.
- Inventario de licencias copyleft y `analysis/NOTICE.md` con advertencias, sin copiar textos de licencias.
- `analyze --diff otro.apk` escribe `STATIC_DIFF.md/.json`; si falta el XML de manifest de cualquiera de los APK, no declara falsos cambios de permisos/deep links.
- Artefactos `STATIC_ANALYSIS.md/.json` se generan aun con `--no-agent`; REA aporta el XML del manifest cuando está disponible.
- Pruebas sobre APK ZIP sintéticos y prueba real de lectura de `org.fdroid.fdroid` 1.21.1; los hallazgos estáticos se presentan como indicios, no como comportamiento probado.

### 1.8 Navegación y recursos del proyecto generado
- `AppRoot.kt` usa un `var current` a mano: generar un **NavHost** real (Compose Navigation / go_router / react-router) con el grafo deducido de actividades + orden de capturas.
- **i18n**: extraer textos a `strings.xml` / ARB / JSON de traducciones en vez de hardcodearlos en el código.
- Valor: medio-alto (el esqueleto pasa a ser una app). Esfuerzo: 6 h.

---

## Tier 2 — Memoria, coste y fiabilidad

| # | Qué | Detalle | Esfuerzo |
|---|---|---|---|
| 2.1 | **Recuperación léxica (BM25)** en vez de "últimos 28 hechos" | `memory.digest(focus)` ya intenta filtrar por palabras; sustituirlo por BM25 sobre `facts` + rutas + glosario. Sin embeddings, barato en Termux. | 3 h |
| 2.2 | **Detección de contradicciones** | Si un hecho nuevo niega uno viejo ("ahora Riverpod, no Provider"), marcar el anterior como `superseded` y no inyectarlo. Campos `supersedes/superseded_by`. | 4 h |
| 2.3 | **Compactación con resumen** | En vez de truncar turnos viejos, pedir al modelo un resumen (1 llamada barata) y guardarlo como hecho de sesión. | 3 h |
| 2.4 | **`MEMORY.md` editable a mano** | Exportar memoria a Markdown con formato parseable y releerlo, para que el usuario corrija al agente y este lo respete. | 3 h |
| 2.5 | **Bloqueo de workspace** | `.rea/lock` (PID+host) para que dos sesiones no se pisen; `--force` para romperlo. | 1 h |
| 2.6 | **Presupuesto de tokens y coste** | Contar `usage` por turno/sesión, `rea-termux cost`, tope blando que avisa y duro que corta; tabla de precios por modelo en config. | 3 h |
| 2.7 | **Enrutado por tarea** | Modelo barato para extracción/clasificación, modelo fuerte para planificar; `[agent].routing = {plan="fuerte", extract="barato"}`. | 3 h |
| 2.8 | **Sanear JSON Schema de MCP** | Algunos proveedores rechazan `$ref`, `anyOf`, `default`, `examples` en `tools`. Función `sanitize_schema()` antes de `tool_schema_from_mcp`. **Quick win.** | 1–2 h |
| 2.9 | **Caché de capacidades del proveedor** | Sondear una vez (`tools`, `vision`, `json_schema`, `max_tokens`) y cachear en config; evita reintentos y 400 sorpresa. | 2 h |

---

## Tier 3 — Termux y entrega

| # | Qué | Detalle |
|---|---|---|
| 3.1 | **wake-lock + notificación** | `termux-wake-lock` al empezar y `termux-notification` al terminar un análisis/captura larga (`termux-api`). |
| 3.2 | **Trabajos en segundo plano** | `rea-termux job start|status|log` para análisis largos sin bloquear la terminal. |
| 3.3 | **Integración `termux-api`** | Compartir el PNG al portapapeles, `termux-share` del informe, `termux-toast` en hitos. |
| 3.4 | **Paquete instalable** | `build-package.sh` para `pkg install ./rea-termux.deb` (evita depender del git clone). |
| 3.5 | **CI** | GitHub Actions: `pytest` + `ruff` + build del wheel; opcional job con emulador para el flujo `ui`. |
| 3.6 | **Galería de ejemplos** | 3 casos documentados (una app Compose, una Flutter, una con WebView) con capturas y diffs de antes/después. |

---

## Tier 4 — Integridad y cumplimiento

| # | Qué | Detalle |
|---|---|---|
| 4.1 | **Auditoría de similitud** ⭐ | Hash de strings/identificadores/recursos del APK y comprobación de que el código generado no contiene fragmentos literales largos ni los assets. `rea-termux audit-similarity <ws>` → informe. Refuerza la promesa de "reimplementación, no copia". |
| 4.2 | **Licencias de terceros** | Inventario de librerías embebidas con su licencia → `NOTICE.md` (necesario si publicas). |
| 4.3 | **Registro de decisiones (ADR)** | Cada decisión `remember` importante exportada como `docs/adr/NNN-titulo.md`; útil al crecer el proyecto. |
| 4.4 | **Screener de permisos/privacidad** | Tabla "permiso → función que lo usa" y aviso si el original pide permisos que tu clon no necesita (esto ya se pregunta en la entrevista; faltaría automatizarlo). |

---

### Hallazgos reales (REA 6.7.0 + org.fdroid.fdroid 1.21.1)

1. `npx rea-agents@6.7.0` funciona incluso con Node 20 (aunque la doc pida 22.19+).
2. **207 herramientas MCP**; las 5 de Android del flujo están presentes.
3. El comando de firma **no** es `inspect-apk-signature` sino `inspect-signature`, y
   **no acepta un APK** (rc=1): la firma se obtiene en
   `normalized_result.signature_verification` (`not_performed` en este APK).
4. La salida es `{ok, data:{…}, meta:{…}}` con `--full-output`, y **el nodo de datos
   directo** sin ese flag. `report.normalized()` acepta ambas.
5. **JADX exige JDK 17+ con `jdk.compiler`**; con Java 11 REA responde
   `capability_unavailable` **y exit code 0** (por eso hacía falta 0.5).
6. El manifest se entrega como **XML decodificado** (33 KB en F-Droid): 25 actividades,
   16 servicios, 15 receivers, 4 providers, 10 exportados, 2 launcher.
7. `limitations[]` de REA trae 5 avisos listos para la sección de huecos del informe.
8. El sha256 del jar de JADX 0.7.1 coincide con el fijado en `termux-bootstrap.sh`.

## Orden recomendado

1. **0.1 + 0.4** — validar contra REA real y dejar rastro de fallos. Sin esto, todo lo demás es teoría.
2. **1.2 (git/undo/diff)** — barato y evita desastres cuando el agente escribe código.
3. **1.1 (self-healing build)** — el mayor salto de calidad percibida.
4. **1.5 (visión)** — desbloquea las apps que hoy no se pueden inspeccionar.
5. **2.8 + 2.9 + 1.3 (quick wins de proveedor + streaming)** — fiabilidad y sensación de velocidad.
6. **1.6, 1.7, 1.8** — calidad del resultado (layout, análisis offline, navegación/i18n).

## Quick wins (< 2 h cada uno)

- Sanear JSON Schema de MCP (`2.8`).
- `--dry-run` en `chat`: mostrar diffs sin escribir.
- Reintento de `uiautomator dump --compressed` y, si falla, seguir solo con PNG (hoy ya continúa, pero sin avisar del todo).
- `termux-wake-lock` en `analyze`/`ui` (`3.1`).
- Marca `vision`/`tools` por proveedor en config + `doctor` que lo muestre (`2.9`).
- `--max-turns` y `--max-cost` como flags sueltos en `chat`.
