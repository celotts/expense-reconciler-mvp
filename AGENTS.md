# AGENTS.md — Contexto operativo para agentes

Este archivo se carga automáticamente. Es la fuente de verdad sobre **cómo está el proyecto hoy**.
Si algo aquí contradice al código, el código gana: avisa y actualiza este archivo.

Documentos hermanos: `README.md` es para humanos, `docs/known-issues.md` es el backlog de
defectos verificados, `docs/captura.md` explica la ruta de lectura de comprobantes y
`docs/testing.md` cómo se verifica el código. `agent.md` se eliminó: su contenido vive aquí.

---

## Qué es

Expense Reconciler MVP. Herramienta **local y privada** para Freelancers y pequeños comercios
mexicanos: se suben comprobantes (PDF/foto), se extraen los datos, se concilian contra el
extracto bancario y se exporta a Excel/CONTPAQI.

No es una plataforma corporativa ni un producto de IA genérico. El valor está en
**que el automatismo no afirme más de lo que puede sostener** (ver *Gate de confianza*).

---

## Estado real (verificado contra el código)

| Capa | Ubicación |
|---|---|
| API | `app/api/` — 7 routers bajo `/api/v1` |
| Captura | `app/services/capture.py` → cascada de 4 escalones |
| Veredicto | `app/services/confidence_gate.py` → checks + umbrales |
| Persistencia | `app/api/tickets.py:_persist_extracted` + `app/services/document_service.py` |
| IA | `app/services/ai_client.py` (Ollama / OpenAI / Azure / local) |
| Auth | `app/core/security.py` (scrypt + JWT HS256 escritos a mano, **sin PyJWT**) |
| Esquema | `db/init.sql` + migraciones numeradas en `db/migrations/` |
| Front | `front/src/` — React 18 + Vite + TS + Tailwind, 8 páginas |
| Tests | **655** — 453 unit, 202 integration |

### Rutas que existen
`/auth` · `/dashboard` · `/categorias` · `/companies` · `/tickets` · `/bank-transactions` · `/reconciliations`
Sin auth: solo `GET /health` y `POST /api/v1/auth/login`.

### Dos máquinas de estado — no las confundas

El ticket pasa por **dos** máquinas de estado independientes. Los nombres se parecen
y no tienen nada que ver: una juzga **cómo se leyó el papel**, la otra **si cuadra con el banco**.

**1. `ExtractionStatus` — el veredicto de la captura** (`app/core/enums.py:11`)
`AUTO_APROBADO` · `REQUIERE_REVISION` · `PENDIENTE` · `APROBADO` · `RECHAZADO`
Lo decide el *confidence gate* leyendo el archivo. `AUTO_APROBADO` significa que el
automatismo se responsabiliza de la lectura y no hace falta revisarla a mano; por eso
esas lecturas entran al muestreo del 5%, que es lo que permite **medir** la exactitud
en vez de afirmarla.

**2. `MatchStatus` — el veredicto de la conciliación** (`app/core/enums.py:100`)
`PERFECT` · `MANUAL` · `DISCREPANCY`
Lo decide el *matching engine*, comparando contra el banco:
- `PERFECT` — monto dentro de tolerancia **y** fecha dentro de tolerancia
- `MANUAL` — uno de los dos cuadra, el otro no
- `DISCREPANCY` — hay movimiento en la fecha correcta pero con otro monto

Solo se exporta lo que está en `SETTLED_STATUSES` (`enums.py:94-97`) = `AUTO_APROBADO`,
`APROBADO`. Un ticket en `PENDIENTE` no se concilia ni se exporta.
`DISCREPANCY` no cuenta como conciliado, a propósito.

---

## Comandos

```bash
make up          # reconstruye imágenes y levanta (SIEMPRE usa este, no `docker compose up`)
make logs        # seguir logs
make stats       # RAM/CPU vs cuota por contenedor
make clean       # ⚠️ borra volúmenes (BD y modelos)
make prune       # limpia imágenes/cache, conserva datos

python3 -m pytest tests/ -q                          # todo
python3 -m pytest tests/unit -q                      # 453
python3 -m pytest tests/integration -q               # 202
```

### Verificación por mutación — correr tras tocar defensa
Cada defensa de seguridad tiene un test que **muere si quitas la defensa**. Si tocas una,
corre su verificador:

```bash
python3 scripts/verify_capture_mutations.py     # 26 mutaciones: ruta de captura
python3 scripts/verify_export_mutations.py      # inyección de fórmulas
python3 scripts/verify_auth_mutations.py        # auth + forja de veredictos
python3 scripts/verify_reconciliation_mutations.py
python3 scripts/verify_spot_check_mutations.py
```

### Verificación contra Postgres real
SQLite no es Postgres: ignora FKs por omisión, acepta `BYTEA` de 12 MB sin quejarse,
e ignora los índices `postgresql_where`. Un test verde sobre SQLite no dice nada de esas tres cosas.

```bash
python3 scripts/verify_postgres_capture.py
python3 scripts/verify_postgres_documentos.py
python3 scripts/verify_postgres_auth.py
python3 scripts/verify_postgres_gate.py
python3 scripts/verify_postgres_reconciliation.py
python3 scripts/verify_postgres_spot_check.py
```

---

## Reglas que NO se rompen

Cada una tiene test que muere si la quitas. Están aquí por una razón concreta, no por
costumbre — el porqué está en el comentario junto al código.

1. **La confianza alta nunca compensa un check roto.**
   `decide_status()` evalúa los checks *antes* que la confianza (`confidence_gate.py:170-188`).
   Un 0.99 sobre aritmética imposible va a revisión, no a auto-aprobado.

2. **El gate escribe después del `model_dump()`.**
   En `create_ticket` / `_persist_extracted`, los campos del veredicto van **explícitos después
   de `**data.model_dump()`**. Además `TicketBase` usa `extra='ignore'`.
   Las dos capas son necesarias: `extra='ignore'` evita el `TypeError` por argumento duplicado,
   el orden es la barrera conceptual. Cliente puede mandar `confidence`/`extraction_status`: se ignoran.

3. **Un PDF con texto no toca el modelo.**
   Si las reglas devuelven algo útil (`provider_name` real y `total > 0`), se devuelve eso y no
   se llama al LLM. Mutación: `verify_capture_mutations.py:36-48`.

4. **El idempotente es `(company_id, source_hash)`, no `source_hash`.**
   El SHA-256 no lleva empresa dentro: el mismo comprobante en dos empresas es legítimo.
   Índice único parcial `WHERE source_hash IS NOT NULL`.

5. **El usuario se re-consulta en la BD en cada request.**
   `get_current_user` no confía en los claims. Desactivar una cuenta surte efecto inmediato,
   no en 8 horas cuando caduca el token.

6. **El rate limit va *antes* de gastar scrypt.**
   Si no, el bloqueo se vuelve amplificador de DoS.

7. **En XLSX solo `forzar_texto`; el apóstrofo es para CSV.**
   `neutralizar_formula` mete `'` que Excel consume pero **se ve** en la celda. En XLSX
   `forzar_texto` pone `data_type = "s"` y nada más.

8. **Allowlist de encodings, no denylist.**
   `ENCODINGS_PERMITIDOS = {utf-8, latin-1, cp1252, iso-8859-1}`. utf-7 → XSS, zlib/bz2 → bomba.

9. **El `content_type` que se sirve no es el que declaró el cliente.**
   `content_type_servible()` aplica lista cerrada de 7 tipos; el resto es
   `application/octet-stream`. `text/html` y `image/svg+xml` excluidos a propósito.

10. **La aritmética del documento se comprueba antes que RFC y fecha.**
    Es el único check que no depende de nada externo; si va después, un total malformado
    cortocircuita la validación.

---

## Trampas verificadas

Estas no son opiniones: se comprobaron leyendo el código. Morar en ellas cuesta tiempo real.

- **Orden de rutas en FastAPI importa y el primer match gana.**
  En `tickets.py` las rutas literales (`/review-queue`, `/spot-check`, `/accuracy`) van
  **antes** de `/{ticket_id}`. En `reconciliations.py`, `GET /{reconciliation_id}` va al
  **final**, después de `/mappings` y `/export/*`, con un comentario que lo explica.
  Al añadir una ruta literal nueva, declárala **antes** de cualquier placeholder.
  Solo colisionan las de **un solo segmento** (`/mappings`), no las de dos o más
  (`/export/excel`, `/mappings/{id}`). Ya se rompió una vez: `docs/known-issues.md` §1.

- **El frontend ofrece los dos caminos, y se elige con un botón explícito.**
  "Aceptar y verificar con IA" → `extract-and-create`, que corre el gate de verdad y puede
  entrar al muestreo; "Revisar y corregir" → el formulario editable, que se guarda como
  `MANUAL` con `confidence=NULL`. Ver `Tickets.tsx:363-390` y `Tickets.tsx:400-406`.
  **No lo unifiques en un solo camino:** si una persona corrige a la IA, el resultado ya no
  es lectura automática, y contarlo como automatismo infla la exactitud medida con datos que
  el sistema no leyó solo.
  Se elige por **clic**, no comparando el formulario contra la extracción: un diff de
  strings se rompe por cosas que no son corrección (`"4094.80"` contra `"4094.8"`, una fecha
  con otro formato, un RFC con espacios).

- **`app/modules/expenses/` es código muerto y además roto.** Su router no está en
  `api_router.py`; `crud.py:136` referencia `SourceType.AUTO`, que no existe en el enum;
  `pipeline.py:224-229` usa `select`/`and_` sin importar. No lo montes sin arreglarlo primero:
  su `batch_upload` acepta un `folder_path` del cliente y hace `Path().glob()` — lectura
  arbitraria de directorios del servidor.

- **`analitica.py` y `hallazgos.py` son servicios, no routers.** No aparecer en
  `api_router.py` es lo correcto: se invocan desde `app/api/dashboard.py:52` y
  `dashboard.py:368`, y ese router sí está montado. Lo que sí conviene verificar es lo
  contrario — que un módulo de `services/` tenga a alguien que lo llame. `VendorNormalizer`
  es el contraejemplo: está instanciado en `ai_client.py:355` y su único caller es el
  `pipeline.py` muerto.

- **No hay multi-tenancy.** `get_current_user` no recibe `company_id` ni hay scoping por empresa:
  cualquier usuario autenticado lee y escribe empresas ajenas pasando un `company_id` arbitrario.
  Es correcto para "una máquina, un contador" y **no** lo es para multiusuario.

- **`REVIEW_CONFIDENCE = 0.60` es decorativo.** Las dos ramas producen `REQUIERE_REVISION`;
  solo cambia la etiqueta en `reasons`.

- **`text` no está en el enum `SourceType`.** `file_type=text` funciona en la cascada pero al
  persistir cae al fallback y se guarda como `source_type="image"`.

- **`VendorNormalizer` no se ejecuta.** Solo lo referencia el `pipeline.py` muerto.
  "Oxxo", "OXXO" y "OXXO EXPRESS" son tres proveedores distintos en el dashboard.

- **Se extraen 12 campos que se tiran.** `invoice_number`, `invoice_series`, `currency`,
  `exchange_rate`, `payment_method`, `items` + 6 más se piden al modelo, se parsean a `Decimal`
  y se descartan. Peor: el export CONTPAQI los manda con **valores inventados**
  (`export_service.py:310-327`): `"TipoCambio": "1.0000"`, `"MetodoPago": "PUE"`.
  Un comprobante en USD sale con tipo de cambio 1.0000 sin marca de error.

- **Campos con coste silencioso.** `invoice.raw_text` se recorta a 20 000 chars (12 000 cabeza
  + 8 000 cola, con marca `[… N caracteres omitidos …]` para que el truncado sea buscable).
  Al modelo se le mandan 8 000 chars. Imágenes se reescalan a 1600 px antes de visión.

- **El fallo al guardar el documento no tumba la captura.** `guardar_documento` usa un
  SAVEPOINT y devuelve `False`. El ticket sobrevive sin su comprobante.

- **`db/init.sql` y los modelos divergen en un punto.** `bank_transactions.company_id` es
  nullable en `init.sql:126` y `NOT NULL` en `models/bank_transaction.py:24`. Los tests corren
  contra SQLite construyendo desde los modelos, así que no lo detectan.

---

## Convenciones

- Comentarios y documentación en **español**, sin tildes en el código (sí en los .md).
- Todo importe es `Decimal`, nunca `float`.
- `model_dump()` no `dict()`; `ConfigDict` no clase `Config` interna.
- `UUID(as_uuid=True)` + `uuid.uuid4()` para IDs generados en Python.
- `back_populates` en los dos lados de toda relación.
- La lógica de negocio va en `app/services/`, los routers son adaptadores finos sin lógica.
- El `user_id` de quién firmó un veredicto se guarda. No lo elimines al refactorizar:
    es lo que hace auditable un muestreo.

---

## Antes de dar por terminada una tarea

```bash
python3 -m pytest tests/ -q
# y si tocaste una defensa:
python3 scripts/verify_<area>_mutations.py
```

Si cambiaste el esquema, la API, el pipeline o el gate, actualiza **este archivo** y
`docs/known-issues.md` en el mismo commit. La documentación que no se actualiza con el
código es peor que no tenerla.
