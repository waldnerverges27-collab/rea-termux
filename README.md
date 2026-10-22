# rea-termux

Agente para **Termux** que clona la **funcionalidad** de cualquier app Android en un proyecto **totalmente nuevo**, con interfaz parecida (o mejor), y que luego **sigue trabajando contigo en la misma conversación sin olvidar lo hecho**.

```
APK ──► REA CLI (JADX) ──► evidencia ──► agente (MCP + IA) ──► ANALYSIS.md
                                                    │
teléfono ──► adb screencap + uiautomator ──► ui/*.png|svg|json ──► DESIGN.md
                                                    │
                                          entrevista ▼
                                     CLONE_PLAN.md + app/{stack}/ ──► chat persistente
```

## Las tres piezas que pediste

### 1. Clonar la funcionalidad de cualquier app
Se puede analizar y reimplementar **cualquier** aplicación. Lo que el programa **no** hace, y aplica técnicamente:

| Se clona | No se copia |
|---|---|
| flujos, pantallas, navegación | código del APK (nunca se extrae ni reempaqueta) |
| reglas de negocio deducidas | nombre, logo, iconos, tipografías, textos, sonidos |
| estructura de datos y endpoints | la marca (si el paquete es de una marca vigilada, `build` sustituye nombre y package id por otros propios) |
| estilo visual como **referencia** de jerarquía y contraste | assets gráficos, estilos ni recursos del original |
| — | DRM, paywalls, licencias o comprobaciones anticopia (nunca se eluden) |

Se pide una atestación una vez (`declare --clone`) y queda registrada en `DECLARATION.md`.

### 2. Interfaz parecida o mejor
`rea-termux ui` captura el teléfono por adb y reconstruye la UI con datos reales:

- `screencap` → PNG + **paleta dominante** propia (decodificador PNG interno, sin PIL)
- `uiautomator dump` → jerarquía de vistas → **wireframe SVG** legible
- nodos normalizados: clase semántica (`button`, `input`, `list`, `image`, `nav`, `toggle`…), texto, bounds
- `DESIGN.md` con tokens (color, tipografía, radios, rejilla de 8dp) **y una lista de mejoras detectadas**: objetivos táctiles < 48dp, botones compitiendo, campos sin etiqueta, imágenes sin `contentDescription`, falta de estados vacío/carga/error, modo oscuro
- `build` genera el código de cada pantalla desde esa estructura (Flutter / Compose / React)

Análisis visual multimodal opcional (envía las capturas al proveedor configurado):

```bash
./rea-termux ui ~/rea-workspace/miapp --screens login,home --vision
```

`--vision` intenta primero `capture_adb_screen` de REA; si no está disponible o
no devuelve PNG, usa `adb screencap`. El análisis multimodal se hace solo con
proveedores que tengan `supports_vision = true` en `rea-termux.toml`; viene
apagado por defecto y hay que habilitarlo únicamente si el modelo configurado
acepta imágenes. El resultado JSON validado se guarda dentro de cada
`ui/<pantalla>.json` y se incorpora a `DESIGN.md`. Sin proveedor visual, la
captura local y el wireframe siguen funcionando. No envíes pantallas con datos
personales o sensibles.

`screen_plan()` también infiere una estructura geométrica conservadora a partir
de `bounds`: filas, columnas/listas, grids, tarjetas, app bar, navegación
inferior y FAB. El árbol se refleja en `DESIGN.md` y orienta los generadores
Flutter, Compose y React; si faltan coordenadas, se conserva el orden vertical y
se anota la limitación en lugar de inventar posiciones.

### 3. Que no olvide lo que hizo
Tres capas, todas en disco:

1. **Memoria del proyecto** (`.rea/memory.json`): decisiones, hallazgos, inventario de archivos con `sha256`, tareas, glosario, tokens de UI, línea de tiempo.
2. **Digest inyectado en cada turno**: aunque el historial se recorte, el prompt de sistema siempre lleva el ESTADO DEL PROYECTO (y avisa si un archivo cambió fuera del agente).
3. **Sesión persistente** (`.rea/sessions/<id>.json`): `chat` reanuda la última conversación; el agente escribe en memoria con `remember` y gestiona tareas con `add_todo` / `complete_todo`, y esas mismas notas aparecen en `PROJECT_STATUS.md`.

---

## Requisitos en Termux

| Pieza | Por qué | Cómo |
|---|---|---|
| Node.js 22.19+/24.11+/26+ | REA lo exige | `pkg install nodejs` (si no cuadra: proot Debian) |
| JDK **completo** 17+ | JADX/REA y Android Build Tools | `pkg install openjdk-21` (17+ mínimo) |
| `jadx-headless-mcp` | motor de APKs de REA | `scripts/termux-bootstrap.sh --jadx` (verifica sha256) |
| `procps` | REA rastrea el proceso JADX | `pkg install procps` |
| `android-tools` | capturas, conexión al dispositivo (`adb`) | `pkg install android-tools` |
| APKTool + Android Build Tools | manifest/resources, badging, firma, alineación | `pkg install apktool aapt apksigner` |
| Gradle (opcional) | compilación de proyectos Android nuevos | `pkg install gradle` |
| Python 3.10+ | este CLI | `pkg install python` |

Ghidra, IDA y Hopper son binarios x86: no corren en el teléfono. Para APKs no hacen falta (REA usa JADX).

## Instalación

```bash
pkg install git python nodejs npm android-tools aapt apktool apksigner gradle
pip install httpx
git clone <tu-repo>/rea-termux && cd rea-termux
bash scripts/termux-bootstrap.sh
# solo herramientas Android (alternativa):
bash scripts/termux-bootstrap.sh --android-tools
./rea-termux init && ./rea-termux doctor
```

## Proveedores (9router / Omniroute)

```bash
export NINEROUTER_API_KEY="sk-..."
export OMNIROUTE_API_KEY="sk-..."
```

```toml
[[providers]]
name = "9router"
base_url = "https://<host>/v1"   # ← tu endpoint real
model = "<id-del-modelo>"
api_key_env = "NINEROUTER_API_KEY"
priority = 10

[[providers]]
name = "omniroute"
base_url = "https://<host>/v1"
model = "<id-del-modelo>"
api_key_env = "OMNIROUTE_API_KEY"
priority = 20
```

`./rea-termux models` valida credenciales y lista modelos. El router cae al siguiente proveedor ante 429/5xx/timeout y reintenta sin `tools` si el modelo no las soporta.

## Validar la instalación (probe)

Antes del primer análisis, comprueba que tus supuestos y los del programa coinciden
con el REA que tienes instalado:

```bash
./rea-termux probe --apk MiApp.apk
```

Verifica: Node, que `rea-agents@<v>` arranca, los comandos del CLI (descubre alias
reales), el handshake MCP y las herramientas, el jar de JADX, la forma del JSON y
que el manifest se parsea. Guarda el informe en `~/.local/share/rea-termux/probe.json`
y los alias que descubre los usa el resto del programa automáticamente.

Si algo falla, imprime la acción concreta. Ejemplo real (Java 11):

```
✗ REA no pudo descompilar el APK:
✗   capability_unavailable · unsupported_provider · Selected Java runtime must be JDK 17+
✗   -> Termux:  pkg install openjdk-17
✗   -> Verifica:  java -version && java --list-modules | grep jdk.compiler
```

### Herramientas Android / APK

El bootstrap instala las herramientas nativas de Termux (`apktool`, `aapt`,
`apksigner`, `android-tools` y Gradle opcional); `doctor` informa cuáles están
disponibles. `analyze` las usa automáticamente cuando existen:

- `aapt dump badging`: package y versión como evidencia adicional.
- `apksigner verify --verbose --print-certs`: verifica la firma; no firma el APK.
- `zipalign -c -v 4`: comprueba la alineación; no modifica el APK.
- `apktool d --no-src`: decodifica manifest/recursos para análisis; no decompila
  fuentes ni instala/ejecuta la app. `--no-apktool` lo omite si se desea.
- REA/JADX sigue siendo el analizador de código; Apktool complementa recursos y
  manifest. Si REA no entrega el XML, se intenta usar el manifest de Apktool.

El informe queda en `analysis/ANDROID_TOOLS.md/.json`; las herramientas que no
estén instaladas son opcionales y se omiten sin bloquear el análisis. El shim
mínimo `aapt` solo se crea si no hay un binario real; no sobrescribe `aapt` del
SDK/Termux.

`--android-tools` instala los utilitarios nativos de Termux; **no descarga el
Android SDK platform completo**. `doctor` busca un platform (`platforms/android-*/android.jar`):
inspeccionar APKs no lo requiere, pero compilar una app Android sí. Si ya tienes
el SDK, exporta `ANDROID_HOME` o `ANDROID_SDK_ROOT` a su raíz. El análisis usa
Build/Platform Tools portadas a Termux, no asume que los binarios x86_64 de
escritorio funcionen en Android.

### Analisis estatico local y comparacion

`analyze` ejecuta además un escaneo local del contenedor APK (sin iniciar la app),
independiente del agente REA. Produce `analysis/STATIC_ANALYSIS.md/.json` y
`analysis/NOTICE.md` con URLs/hosts, deep links del manifest, huellas de SDK,
Firebase, referencias textuales de permisos declarados/usados y posibles licencias
copyleft. Los valores de posibles secretos nunca se guardan: solo se informa el
tipo, ubicación, longitud y nivel de confianza; las query strings de URLs se
redactan. Las detecciones son indicios estáticos, no prueba de uso efectivo.

Para comparar con otro APK:

```bash
./rea-termux analyze nueva.apk --diff version-anterior.apk --out ~/rea-workspace/diff
```

Genera `analysis/STATIC_DIFF.md/.json`; si REA no puede decodificar el manifest
comparado, el diff omite permisos y deep links en vez de declararlos distintos.

## Flujo de trabajo

```bash
# 0) conecta el teléfono (USB o inalámbrico)
adb devices

# 1) analizar el APK (descompila + informe con evidencia)
./rea-termux analyze MiApp.apk --out ~/rea-workspace/miapp

# 2) atestación de uso responsable (una vez por workspace)
./rea-termux declare MiApp.apk --clone --out ~/rea-workspace/miapp

# 3) capturar pantallas del original (navega cuando lo pida)
./rea-termux ui ~/rea-workspace/miapp --screens login,home,perfil --wait

# 4) entrevista: qué mejorar, stack, backend, pantallas
./rea-termux plan ~/rea-workspace/miapp

# 5) generar proyecto + pantallas desde la UI capturada
./rea-termux build ~/rea-workspace/miapp --stack flutter

# 6) conversar para construir y mejorar (memoria persistente)
./rea-termux chat ~/rea-workspace/miapp
```

Todo en uno: `./rea-termux run MiApp.apk --screens login,home`.

### La entrevista

Nombre y package **nuevos**, propósito, **qué mejorar** (`;` separado), stack (`android-kotlin` · `flutter` · `react-native` · `web-pwa`), pantallas a replicar, backend (`mock` · `same-api` · `new-api`), offline, idioma, funciones a quitar, monetización, notas. Automatizable con `--answers answers.json`.

### Salidas estructuradas

Donde el formato importa, el modelo responde JSON validado contra un esquema
(`schemas.py`), no prosa que hay que parsear:

```bash
./rea-termux analyze app.apk      # genera analysis/unknowns.json (huecos)
./rea-termux plan <ws> --structured   # propone pantallas y modelo de datos
```

- Estrategia: `response_format: json_schema` (strict) → `json_object` → instrucciones
  en el prompt. Si el proveedor rechaza un modo, se marca y no se reintenta.
- Validación propia (sin dependencias), `coerce` para arreglos baratos
  (`"42"` → `42`, `"x"` → `["x"]`) y **reintento con el error concreto** como feedback.
- `extract_json` acepta JSON envuelto en ```` ```json ```` o con texto alrededor.
- Si todo falla, el flujo degrada a lo de antes (parseo del bloque `HUECOS:`).

### Respuestas en streaming

Las respuestas llegan token a token (SSE), incluidos los `tool_calls` partidos en
varios chunks, que se reensamblan antes de ejecutar la herramienta. Si el
proveedor no soporta streaming, devuelve JSON en vez de `text/event-stream` o
corta la conexión, el cliente **se degrada solo** y marca
`supports_stream = false` para no reintentarlo en cada turno.

- Desactivar por completo: `--no-stream` o `supports_stream = false` por proveedor.
- El `usage` final (`include_usage`) se sigue reportando aunque haya stream.

### Red de seguridad: git en el workspace

`analyze` crea un repo propio y **cada turno del chat se commitea** con el resumen
de lo que hizo el agente (y tu pedido en el cuerpo del commit).

```bash
./rea-termux chat ~/rea-workspace/miapp
tu> /log          # ultimos commits del workspace
tu> /diff         # que cambio sin commitear + ultimo commit
tu> /undo 1       # deshace el ultimo turno (reset --hard al anterior)
tu> /pending      # propuestas pendientes (modo --dry-run)
tu> /apply        # aplica las propuestas
```

Modo revisión: `./rea-termux chat <ws> --dry-run` — el agente **no escribe**:
devuelve el diff de cada archivo y las propuestas quedan en `.rea/pending.json`;
las aplicas luego con `rea-termux vcs <ws> apply`.

```bash
./rea-termux vcs <ws> log|diff --stat|undo --steps 2|restore --commit <sha> --paths f.kt
```

Detalles: nunca versiona binarios > 5 MB ni `*.apk`; si el workspace ya es un repo
tuyo, no lo toca salvo `vcs init --adopt`; si falta git, el chat sigue sin VCS.

### Verificación y auto-reparación

```bash
./rea-termux verify ~/rea-workspace/miapp --project app/flutter        # compila?
./rea-termux verify ~/rea-workspace/miapp --project app/flutter --fix  # y que lo arregle
```

Tres niveles, y siempre dice cuál usó:

| Nivel | Herramienta | Cuándo |
|---|---|---|
| toolchain | `flutter analyze`, `tsc --noEmit`, `gradle compileDebugKotlin` | si está instalado |
| compilador suelto | `dart analyze`, `kotlinc`, `node --check` | alternativa |
| estático propio | llaves/paréntesis, marcadores `__X__`, `package` vs ruta, vacíos | **siempre** |

Los errores se normalizan a `archivo:línea:col · severidad · mensaje · código` y el
bucle `--fix` se los pasa al agente para que corrija (con `read_file` antes de
editar), re-verifica, commitea cada ronda y deja registro en `.rea/verify.json` y
en la memoria del proyecto.

En el chat: `/verify` (informa) y `/verify --fix` (corrige). El agente además
tiene la herramienta `verify_build` para comprobar su propio trabajo.
Opcional: `[agent].verify_after_turn = true` avisa tras cada turno.

### El chat

```
tu> haz que el login valide el email y muestre error inline
  -> read_file
  -> edit_file
  -> remember
     ok
Hecho: Login.kt valida con regex y pinta el error debajo del campo.
Siguiente: falta el estado de carga del botón.

memoria: 12 hechos · 3 tareas abiertas · 9 archivos
```

- `chat <ws>` reanuda la **última** sesión; `--session <id>` una concreta; `--message "..."` para modo no interactivo (repetible).
- `--no-mcp` si solo quieres que trabaje sobre archivos (sin servidor REA).
- `./rea-termux status <ws>` muestra y reescribe `PROJECT_STATUS.md`.

Herramientas que el agente tiene en el chat (además de las de REA):

| Herramienta | Para qué |
|---|---|
| `list_files`, `read_file` | inspeccionar el proyecto (enjaulado en el workspace) |
| `write_file`, `edit_file` | escribir/editar código (prefiere `edit_file` para cambios puntuales) |
| `run_command` | compilar/testear (`gradle`, `flutter`, `npm`, `adb`), con timeout |
| `remember`, `add_todo`, `complete_todo`, `define` | memoria del proyecto |
| `project_status` | releer su propio estado cuando lo necesite |
| `capture_screen` | capturar otra pantalla del original en mitad de la conversación |

## Qué se genera

```
~/rea-workspace/miapp/
├── DECLARATION.md            # atestación registrada
├── ANALYSIS.md               # manifest, permisos, endpoints, flujos, huecos
├── DESIGN.md                 # tokens + pantallas + mejoras propuestas
├── CLONE_PLAN.md             # el contrato de lo que vas a construir
├── PROJECT_STATUS.md         # estado vivo (lo escribe la memoria)
├── answers.json
├── analysis/                 # evidencia REA, notas y transcripts
├── ui/                       # login.png · login.xml · login.wireframe.svg · login.json
├── .rea/
│   ├── memory.json           # decisiones, tareas, inventario, glosario
│   ├── events.jsonl          # registro append-only
│   └── sessions/*.json       # conversaciones reanudables
└── app/{stack}/              # proyecto nuevo + pantallas generadas
```

## Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| `no devices` | activa depuración USB; inalámbrica: `adb pair ip:puerto` + `adb connect` |
| `uiautomator dump falló` | apps con WebView/Flutter a veces no exponen vistas → se guarda el PNG igualmente y la paleta sirve |
| `ps: unknown option -- o` | `pkg install procps` |
| REA no ve el APK | ruta absoluta; copia el APK a `~` si está en `/sdcard` |
| El agente "olvida" algo | revisa `./rea-termux status`; si un hecho falta, pídele `remember` explícito o edita `.rea/memory.json` |
| Contexto caro | baja `[agent].max_tool_chars` y `max_turns`; el digest de memoria ya evita re-explorar |
| Comando peligroso | `[agent].allow_command = false` desactiva `run_command` |

## Estado y pruebas

```bash
python -m pytest -q     # 50 pruebas
```

Cubren: cliente MCP contra servidor stdio falso, agente con proveedor OpenAI simulado y fallback entre proveedores, parser AXML, paleta PNG + tema con contraste, parser de `uiautomator`, wireframe, generación de código por stack, memoria (upsert, poda, digest), sesión persistente (memoria inyectada, reanudación, jail de rutas). Probado end-to-end con APK sintético, `adb`/`npx` simulados y proveedor local; falta validarlo contra un APK real y equipo real.

## Licencia

MIT. El uso que hagas del análisis y de la reimplementación es tu responsabilidad: reimplementa solo funcionalidad, con identidad y recursos propios, y respeta las condiciones de servicio de las apps y de tus proveedores de IA.
