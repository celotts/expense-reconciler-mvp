# Expense Reconciler MVP

## Visión del Producto

**Una alternativa simple, directa y de demanda real**

En lugar de crear un sistema operativo corporativo o una gran plataforma de IA, pensemos en una herramienta utilitaria de nicho. Algo que las personas y los pequeños negocios ya pagan por resolver, pero que hoy funciona mal o es muy costoso.

Un ejemplo claro es un **Automatizador Local de Facturas y Control de Gastos para Pequeños Negocios y Freelancers**:

**El problema real:** Cualquier persona que trabaje de forma independiente o tenga un pequeño comercio (como una taquería, una tienda local o un prestador de servicios) recibe decenas de comprobantes fiscales, tickets o PDFs por correo o WhatsApp. Ordenarlos, sumarlos y pasarlos a una hoja de cálculo o enviárselos al contador es un dolor de cabeza mensual.

**Cómo funciona la solución:** Es una aplicación donde arrastras tus archivos (PDFs o fotos de tickets), y la herramienta de forma automática extrae los datos clave (fecha, monto, concepto), los organiza, los concilia contra movimientos bancarios y te genera un reporte limpio listo para impuestos o para tu contador. Todo corriendo de forma **privada y local** en tu computadora.

**Cómo se monetiza:** Licencia única de pago único o suscripción muy accesible (mensual/anual) dirigida a profesionales independientes o pequeños comercios que prefieren pagar una pequeña cantidad antes de pasar horas haciendo cuentas a mano.

---

## Stack Tecnológico

- **API**: FastAPI (Python 3.11+)
- **Base de datos**: PostgreSQL 16 + pgvector
- **ORM**: SQLAlchemy 2.0 (async/asyncpg)
- **Validación**: Pydantic v2
- **Procesamiento**: pandas (CSV), pdfplumber (PDF), openpyxl (Excel)
- **Contenedores**: Docker Compose
- **Tests**: pytest + pytest-asyncio + httpx

---

## Estructura del Proyecto

```
expense-reconciler-mvp/
├── app/
│   ├── main.py                 # FastAPI app + routers
│   ├── core/
│   │   ├── config.py           # Settings (pydantic-settings)
│   │   ├── database.py         # Async engine + session
│   │   ├── security.py         # JWT HS256 + scrypt (stdlib only)
│   │   ├── rate_limit.py       # Login: 5 intentos/60s por (cuenta, IP)
│   │   ├── deps.py             # get_current_user (re-consulta BD)
│   │   ├── subida.py           # Límite subida: ticket 10MB / CSV 25MB
│   │   └── texto.py            # Defensa fórmula XLSX/CSV (forzar texto)
│   ├── models/                 # SQLAlchemy ORM models
│   │   ├── company.py
│   │   ├── ticket.py
│   │   ├── bank_transaction.py
│   │   ├── reconciliation.py
│   │   ├── accounting_mapping.py
│   │   └── user.py
│   ├── schemas/                # Pydantic v2 schemas
│   ├── services/               # Business logic
│   │   ├── parser_service.py   # CSV/PDF parsing (allowlist encoding)
│   │   ├── reconciliation_service.py  # Matching greedy + criterio
│   │   ├── export_service.py   # Excel/CONTPAQI (forzar_texto)
│   │   ├── accuracy_service.py # Exactitud 96% Wilson + muestreo
│   │   └── capture.py          # Pipeline extract → confidence → gate
│   └── api/                    # REST endpoints
│       ├── auth.py             # POST /login, GET /me
│       ├── companies.py
│       ├── tickets.py
│       ├── bank_transactions.py
│       ├── reconciliations.py
│       └── dashboard.py        # GET /dashboard (landing page)
├── db/
│   └── init.sql                # Schema + indexes (incl. users, lower(email))
├── tests/
│   ├── unit/                   # 453 tests
│   └── integration/            # 202 tests
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── pytest.ini
├── AGENTS.md                   # Contexto operativo para agentes (se carga solo)
├── memory.md                   # Project memory
├── skill.md                    # Reusable patterns
└── README.md                   # This file
```

---

## Quick Start

### Con Docker (Recomendado)
```bash
make up
```

`make up` reconstruye las imágenes antes de levantar. Sin ese paso, `docker compose up` reutiliza la
imagen anterior aunque `requirements.txt` haya cambiado, y la API arranca con imports rotos.

Otros objetivos: `make logs`, `make ps`, `make stats` (RAM/CPU vs cuota), `make disk`,
`make prune` (limpia imágenes/cache sin tocar datos), `make down`, `make clean` (⚠️ borra volúmenes).

La API estará en: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`
- Frontend: `http://localhost:3000`

### Recursos en una laptop

El presupuesto está pensado para la VM de Docker de 7.7 GB (host de 18 GB / 11 CPUs).
Cada servicio tiene `mem_limit` y `cpus` en `docker-compose.yml`: si uno se pasa, Docker lo mata
en vez de dejar que la máquina entre en swap.

| Servicio | Cuota RAM | CPUs | En reposo |
|---|---|---|---|
| `expense-api` | 1.75 GB | 4 | ~430 MB |
| `ollama` | 4 GB | 4 | ~2.4 GB con ambos modelos cargados |
| `postgres-reconciler` | 768 MB | 1 | ~25 MB |
| `expense-front` | 256 MB | 0.5 | ~7 MB |

**Torch es CPU-only a propósito.** El `Dockerfile` lo instala desde
`https://download.pytorch.org/whl/cpu` *antes* que el resto de dependencias. Sin eso, pip baja
~4.1 GB de paquetes `nvidia-*` y `triton` que son inservibles en linux/arm64 y que nunca se usan:
`app/services/ai_client.py` ya resuelve el device con `torch.cuda.is_available()`. La imagen de la
API queda en **1.86 GB** en vez de 6.76 GB.

**Modelos de Ollama** (ligeros a propósito, y por qué):

| Modelo | Tamaño | Rol |
|---|---|---|
| `qwen2.5:3b` | 1.9 GB | Texto: extracción de datos de facturas |
| `moondream` | 1.7 GB | Visión: lectura de tickets |

Los dos suman 3.6 GB y caben juntos en la cuota de 4 GB. Si cambias alguno por uno más grande,
ajusta `mem_limit` de `ollama` y el de `expense-api` en `docker-compose.yml` para que el total
siga cabiendo en la VM.

> Si configuras un modelo en `.env.dev` que no esté descargado, Ollama devuelve 404 y el pipeline
> degrada en silencio a "guarda lo parcial" — ves tickets guardados con importes en `0.00` y sin
> error visible. Después de cambiar un modelo: `make up` y verifica con
> `docker compose exec ollama ollama list`.

### Archivos de entorno

Hay **cuatro** archivos y solo uno se versiona. Están repartidos así a propósito: un archivo
con la clave de firma y la configuración de IA mezclados tiene que subirse para compartir la
parte de IA, y entonces la clave sale con él.

| Archivo | Versionado | Qué lleva | Quién lo lee |
|---|---|---|---|
| `.env.example` | **sí** | la plantilla | tú |
| `.env` | no | solo `POSTGRES_PASSWORD` | `docker compose` (interpola `${POSTGRES_PASSWORD}`) |
| `.env.dev` | no | configuración de la app: IA, carpeta de tickets, OCR. **Sin secretos** | `docker compose` y `app/core/config.py` |
| `.env.local` | no | solo `SECRET_KEY` | `docker compose` y `app/core/config.py`, **gana** sobre `.env.dev` |

```bash
cp .env.example .env.dev
cp .env.example .env
chmod 600 .env .env.dev     # con 644 cualquiera con una sesión en la máquina los lee
```

La app lee `.env.dev` y después `.env.local`, en ese orden y con el último ganando — el mismo
orden que usa `docker-compose.yml`. Correr `uvicorn` a mano firma los tokens con la misma clave
que el contenedor; antes no era así, y se veía como "la sesión se cae, pero solo cuando depuro
en local".

> **`SECRET_KEY` no tiene valor por defecto.** Sin ella, `config.py` genera una clave efímera y
> avisa: molesto (con `--reload`, cada cambio en un `.py` cierra las sesiones) pero no roto.
> Genera una real con `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` y ponla
> en `.env.local`. Con ella se pueden firmar tokens válidos sin pasar por el login, así que si
> crees que salió del equipo, **rotarla** es lo único que sirve.

> **`DATABASE_URL` en `.env.dev` va sin contraseña.** Dentro de Docker la sobrescribe
> `docker-compose.yml` con la que arma desde `POSTGRES_PASSWORD`, así que la que esté ahí no se
> usa. Dejarla con la contraseña puesta era tener el mismo secreto en dos archivos, uno de
> ellos legible por el grupo. Fuera de Docker sí se usa: ahí apunta a una base que exista de
> verdad y la rellenas tú.

> **`CORS_ORIGINS` no admite `"*"`.** La app se sirve con `allow_credentials=True`, y esa
> combinación hace que cualquier sitio web pueda leer la API con la sesión del navegador de
> quien la tiene abierta. El arranque falla si aparece un comodín, en vez de dejarlo pasar.

### Escaner de carpeta (OCR local, sin APIs de pago)

El escáner lee los comprobantes de una carpeta local y los extrae **sin llamar a
ninguna API de pago**. Las fotos pasan por Tesseract antes de por el modelo, y las
que Tesseract lee bien no cuestan nada.

La ruta se configura por variable de entorno y **no** se pide por API: no hay endpoint
ni parámetro que acepte una carpeta, a propósito, porque eso sería lectura arbitraria
del disco del servidor.

```env
TICKETS_INPUT_DIR=/Users/carloslott/Documents/Tickets_app
OCR_ENABLED=true
```

La carpeta se crea sola si no existe. En Docker el host la monta en solo lectura y
`TICKETS_HOST_DIR` dice cuál es:

```yaml
volumes:
  - ${TICKETS_HOST_DIR}:/tickets:ro
```

Dentro del contenedor la ruta es siempre `/tickets`: la del host no existe ahí, y sin el
override `docker-compose.yml` crearía `/Users/carloslott/...` como root dentro del contenedor.

> **El puerto publicado de Ollama es 11435, no 11434.** En esta máquina hay un Ollama nativo en
> `127.0.0.1:11434` y publicar el mismo puerto en loopback no arranca. La app habla con Ollama
> por la red interna (`OLLAMA_BASE_URL=http://ollama:11434`); ese mapeo es solo para mirar el
> contenedor desde la máquina.

#### Qué sale de leer una foto, medido en las tres de esta máquina

Las tres JPEG de `Tickets_app` se leen, pero ninguna auto-aprueba. Lo que decide el gate es
el mismo en las tres: a un comprobante leído por OCR le falta RFC **y** subtotal **y** fecha, y
sin las tres no se afirma que la lectura sea buena.

| Archivo | Origen | Qué se leyó bien | Qué falta |
|---|---|---|---|
| `IMG_4220.jpeg` | OCR | proveedor, total 97.56, **fecha 28/09/26** | RFC y subtotal → `REQUIERE_REVISION` |
| `1C2C52A2…jpeg` | OCR | proveedor | fecha (el OCR la perdió) → `PENDIENTE` |
| `IMG_4222.jpeg` | modelo | nada útil | foto arrugada y a contraluz; 90 s de moondream para un total en 0 |

> **El OCR cambia cifras sin avisar.** En la foto de Oxxo el papel dice `51.50` y Tesseract leyó
> `31.50`: un importe que pasa todos los checks del gate y es un error financiero. Está medido y
> documentado en `docs/known-issues.md` §21. Por eso un ticket de OCR nunca auto-aprueba y la
> revisión humana es la que confirma el importe.

El `Dockerfile` instala el binario de Tesseract con el paquete de español y falla el
build si falta. Para correrlo fuera de Docker en macOS:

```bash
brew install tesseract tesseract-lang
```

Verifica antes de escanear:

```bash
curl localhost:8000/api/v1/scan/ocr     # ¿qué motores funcionan y por qué no los otros?
curl localhost:8000/api/v1/scan/config  # ¿de qué carpeta va a leer?
```

| Método | Ruta | Qué hace |
|---|---|---|
| `POST` | `/api/v1/scan/` | Escanea. `company_id` es opcional: sin él inventaría y **no** crea tickets |
| `GET` | `/api/v1/scan/stats` | Conteos por estado, por motor de lectura y antigüedad |
| `GET` | `/api/v1/scan/files` | El registro de archivos, filtrable y paginado |
| `GET` | `/api/v1/scan/files/{id}` | Un archivo con todo su historial de cambios |
| `POST` | `/api/v1/scan/files/{id}/reprocess` | Relee un archivo; el único camino que pisa correcciones humanas |
| `GET` | `/api/v1/scan/config` | La configuración efectiva (carpeta, topes) |
| `GET` | `/api/v1/scan/ocr` | Qué motores de OCR funcionan y por qué no los otros |

Tres cosas que conviene saber antes de usarlo:

- **Escanear dos veces no crea dos tickets.** El registro por archivo (`scan_files`)
  compara el SHA-256 y salta lo que no cambió. Es lo que evita gastar OCR en los
  mismos 400 comprobantes en cada corrida.
- **Un ticket corregido a mano no se sobreescribe.** Aunque el archivo cambie, ni con
  `reprocesar=true`. Perder trabajo humano en silencio es peor que no reprocesar; el
  único camino que lo hace es `/reprocess`, que es explícito.
- **Un ticket leído por OCR se auto-aprueba solo con la evidencia completa** (RFC,
  subtotal y fecha) y si `subtotal + IVA == total` cuadra. Es más conservador que un
  PDF con texto, y a propósito: el OCR cambia caracteres sin avisar.

---

## Flujo de Trabajo Principal

### 1. Crear Empresa
```bash
POST /api/v1/companies/
{"name": "Mi Taquería", "tax_id": "MTA123456789"}
```

### 2. Registrar Tickets (manual o extracción automática)
```bash
# Manual
POST /api/v1/tickets/
{"company_id": "...", "provider_name": "PROVEEDOR", "total_amount": "150.00", "expense_date": "2025-01-15"}

# Extraer de PDF/imagen + crear
POST /api/v1/tickets/extract-and-create
multipart: file=@ticket.pdf, file_type=pdf, company_id=...
```

### 3. Importar Movimientos Bancarios (CSV)
```bash
POST /api/v1/bank-transactions/import-csv-and-create
multipart: file=@banco.csv, company_id=..., date_column=fecha, amount_column=importe, description_column=concepto
```

### 4. Ejecutar Conciliación Automática
```bash
POST /api/v1/reconciliations/run
{"company_id": "...", "amount_tolerance": "0.01", "date_tolerance_days": 3}
```

**Estados de match:**
- **PERFECT**: Monto exacto + fecha dentro de tolerancia
- **MANUAL**: Monto OK pero fecha fuera (o viceversa) - requiere revisión
- **DISCREPANCY**: Diferencia de monto > tolerancia

### 5. Exportar para Contabilidad
```bash
# Excel estándar
GET /api/v1/reconciliations/export/excel?company_id=...

# CONTPAQI (México)
GET /api/v1/reconciliations/export/contpaqi?company_id=...

# Personalizado
GET /api/v1/reconciliations/export/generic?company_id=...&columns=Fecha,Proveedor,Total,Estatus%20Conciliacion
```

---

## API Endpoints Summary

| Módulo | Prefijo | Endpoints |
|--------|---------|-----------|
| **Auth** | `/api/v1/auth` | `POST /login`, `GET /me` |
| **Dashboard** | `/api/v1/dashboard` | `GET /` — **Landing page**: KPIs por período (hoy, mes actual/anterior, año actual/anterior), cola de revisión, exactitud SLO 96% |
| **Companies** | `/api/v1/companies` | CRUD completo |
| **Tickets** | `/api/v1/tickets` | CRUD + `/extract` + `/extract-and-create` + `/review-queue` + `/spot-check` + `/accuracy` |
| **Bank Transactions** | `/api/v1/bank-transactions` | CRUD + `/import-csv` + `/import-csv-and-create` |
| **Reconciliations** | `/api/v1/reconciliations` | `/run`, CRUD, `/export/*`, `/mappings` |

> **El informe de cierre mensual aún no existe** (es la Fase 1 de
> `docs/contrato-producto.md` §4). Lo que sí está listo es el estado que lo hace
> posible: `cierres_periodo` registra qué periodo se cerró, quién lo marcó
> (`user_id` desde el token), qué había pendiente en ese momento y la huella del
> informe que vio. Sin ese estado, un periodo con pendientes no se puede cerrar,
> y eso no es un defecto: es la regla R3.

---

## Dashboard (Página Principal)

El endpoint `GET /api/v1/dashboard` es la **vista de aterrizaje** del frontend. Devuelve un resumen ejecutivo por períodos:

### Períodos calculados
| Período | Qué mide |
|---------|----------|
| `hoy` | Tickets creados hoy, monto, pendientes, movimientos bancarios, conciliaciones |
| `mes_actual` | Actividad del mes en curso |
| `mes_anterior` | Comparativa mes anterior (tendencia) |
| `ano_actual` | Visión anual |
| `ano_anterior` | Comparativa año anterior |
| `totales_acumulados` | Desde el inicio (epoch) |

### Métricas por período
```json
{
  "tickets_total": 42,
  "tickets_pendientes": 7,
  "tickets_aprobados": 30,
  "tickets_rechazados": 2,
  "tickets_auto_aprobados": 3,
  "monto_total": "125000.00",
  "monto_pendiente": "18500.00",
  "bank_transactions": 58,
  "reconciliations_perfect": 22,
  "reconciliations_manual": 8,
  "reconciliations_discrepancy": 3
}
```

### Cola de revisión (tiempo real)
```json
{ "PENDIENTE": 7, "RECHAZADO": 2 }
```

### Exactitud (SLO 96%) — solo si hay `company_id`
```json
{
  "objetivo": 0.96,
  "veredicto_global": "CUMPLE|NO_CUMPLE|INCONCLUYENTE|SIN_EVIDENCIA",
  "explicacion": "La evidencia alcanza para afirmar que el automatismo está por encima de 96% en cada vía de lectura.",
  "por_origen": [
    { "origen": "llm", "aciertos": 18, "incorrectos": 1, "pendientes": 3, "exactitud": 0.947, "intervalo_inferior": 0.74, "intervalo_superior": 0.99, "veredicto": "INCONCLUYENTE", "motivo_faltante": "Faltan 7 revisiones...", "campo_mas_fallido": "expense_date" }
  ]
}
```

### Frontend (React + Vite + Tailwind)
- **Página por defecto**: Dashboard (selector de empresa global/filtrado)
- Navegación lateral: Dashboard → Empresas → Tickets → Cola revisión → Muestreo → Banco → Conciliación
- Acciones rápidas: Nuevo ticket, Subir extracto, Conciliar, Revisar cola
- Build: `cd front && npm run build` (TypeScript + Vite)

### Tests de seguridad (nuevos)
- **Inyección fórmula** XLSX/CSV: `tests/integration/test_export_injection.py` (10 tests, mutación verificada)
- **DoS subida** archivos: `tests/unit/test_subida.py` (12 tests, 413 Payload Too Large)
- **Abuso codecs** (utf-7, zlib): `tests/unit/test_csv_security.py` (19 tests, allowlist + no reflection)
- **Forjar veredicto** (rompe SLO): `tests/integration/test_veredicto_forjado.py` (6 tests, extra='ignore' + orden gate)

---

## Tests

```bash
# Todos (738 tests - incluye seguridad + regresión)
python3 -m pytest tests/ -q

# Unitarios
python3 -m pytest tests/unit/ -q

# Integración
python3 -m pytest tests/integration/ -q

# Solo seguridad (inyección fórmula, DoS subida, codecs, forjar veredicto)
python3 -m pytest tests/unit/test_subida.py tests/unit/test_csv_security.py tests/integration/test_export_injection.py tests/integration/test_veredicto_forjado.py -v

# El comprobante original (guardar, servir, y el content-type del cliente)
python3 -m pytest tests/integration/test_documentos.py tests/integration/test_documento_api.py -v
```

### Verificación por mutación (cada defensa tiene test que muere si se quita)
```bash
python3 scripts/verify_capture_mutations.py    # 26 mutaciones, la ruta de captura
python3 scripts/verify_export_mutations.py    # inyección de fórmulas
python3 scripts/verify_auth_mutations.py      # auth y forja de veredictos
```

### Verificación contra Postgres real
SQLite no es Postgres en tres cosas que importan: ignora las llaves foráneas
por omisión (un `ON DELETE CASCADE` no se ejecuta), acepta un `BYTEA` de 12 MB
sin quejarse (el TOAST de Postgres sí), e ignora los índices `postgresql_where`
del modelo. Un test en verde sobre SQLite no dice nada sobre esas tres.

```bash
python3 scripts/verify_postgres_capture.py     # idempotencia por empresa, tope de raw_text
python3 scripts/verify_postgres_documentos.py  # bytes intactos, CASCADE, UNIQUE
```

---

## Documentación Técnica

- **agent.md**: Definición del rol/agent
- **memory.md**: Decisiones técnicas, estructura, próximos pasos
- **skill.md**: Patrones reutilizables (10 patrones con código)

---

## Próximos Pasos

1. **Alembic** para migraciones de BD
2. ✅ **Auth/JWT** (HS256 + scrypt, sin deps nuevas, rate limit)
3. ✅ **Comprobante original guardado** (0006) — sin esto el muestreo de
   exactitud se hacía a ciegas: se contrastaba la transcripción del modelo
   contra sí misma
4. **OCR real** (AWS Textract, Azure Form Recognizer)
5. ✅ **Frontend** (React + Vite + Tailwind, hash router pendiente, Dashboard landing)
6. **Empaquetado** como app de escritorio (PyInstaller/Tauri)

### Lo que sigue faltando en la captura

Lo que está pendiente y **no** está resuelto por lo de arriba:

- **HEIC/TIFF de iPhone.** `subida.py` justifica los 10 MB con fotos de
  iPhone, pero PIL sin `pillow-heif` no abre un HEIC, y el front solo acepta
  `.pdf,.png,.jpg,.jpeg`.
- **PDF cifrado con contraseña**: cae a visión y sale como "ilegible", sin decir
  que el problema es la contraseña.
- **Varios archivos por subida**: `file: UploadFile` es uno solo.
- **Carga masiva.** `SourceType.BULK`/`DIRECTORY` existen en el enum y no hay
  endpoint. La idempotencia por hash ya está, pero nada la llama en lote.
- **Campos del CFDI que se extraen y se tiran** (`invoice_number`,
  `invoice_series`, `currency`, `exchange_rate`, `payment_method`, `items`): el
  modelo los lee y el export de CONTPAQI los manda vacíos con un tipo de cambio
  inventado (`export_service.py:321-325`).
- **`VendorNormalizer` sin usar**: "Oxxo", "OXXO" y "OXXO EXPRESS" son tres
  proveedores en la tabla, y el dashboard los cuenta por separado.