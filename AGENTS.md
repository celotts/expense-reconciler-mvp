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
| API | `app/api/` — 8 routers bajo `/api/v1` |
| Captura | `app/services/capture.py` → cascada de 5 escalones |
| OCR local | `app/services/ocr.py` → Tesseract, lazy |
| Escáner | `app/services/scan_service.py` → recorre `TICKETS_INPUT_DIR` |
| Veredicto | `app/services/confidence_gate.py` → checks + umbrales |
| Persistencia | `app/services/ticket_persistence.py` (la llama `app/api/tickets.py:_persist_extracted`) + `document_service.py` |
| IA | `app/services/ai_client.py` (Ollama / OpenAI / Azure / local) |
| Auth | `app/core/security.py` (scrypt N=2\*\*17 + JWT HS256 escritos a mano, **sin PyJWT**) |
| Esquema | `db/init.sql` + migraciones numeradas en `db/migrations/` |
| Front | `front/src/` — React 18 + Vite + TS + Tailwind, 8 páginas |
| Tests | **960** — 717 unit + escáner, 243 integration, 1 skip |

### Rutas que existen
`/auth` · `/dashboard` · `/categorias` · `/companies` · `/tickets` · `/bank-transactions` · `/reconciliations` · `/scan`
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

python3 -m pytest tests/ -q           # 960
python3 -m pytest tests/unit -q
python3 -m pytest tests/integration -q
```

### Verificación por mutación — correr tras tocar defensa
Cada defensa de seguridad tiene un test que **muere si quitas la defensa**. Si tocas una,
corre su verificador:

```bash
python3 scripts/verify_scan_mutations.py            # 15 mutaciones: escaner de carpeta
python3 scripts/verify_capture_mutations.py         # 26 mutaciones: ruta de captura
python3 scripts/verify_export_mutations.py          # 5 mutaciones: fórmulas
python3 scripts/verify_auth_mutations.py            # 42 mutaciones: auth y coste del hash
python3 scripts/verify_reconciliation_mutations.py  # 18 mutaciones
python3 scripts/verify_spot_check_mutations.py      # 20 mutaciones: muestreo
python3 scripts/verify_vscode_mutations.py          # 18 mutaciones: superficie de confianza
```

### Medir la exactitud de las fotos
El muestreo del 5% entra **solo** sobre tickets `AUTO_APROBADO`, y un ticket de OCR nunca
llega ahí, así que `GET /tickets/accuracy` no puede decir nada sobre las fotos (medido: las
filas `confidence_source='ocr'` tienen `spot_check_status = NULL`). Para eso está:

```bash
# La verdad se rotula a mano mirando el papel, y vive FUERA del repo.
python3 scripts/medir_precision_ocr.py --init ~/Documents/Tickets_app
python3 scripts/medir_precision_ocr.py /tickets --min-exactitud-total 0.985
```

Corre **dentro del contenedor** (necesita Tesseract): `docker compose exec expense-api python
/app/scripts/medir_precision_ocr.py /tickets`. Estado medido sobre las tres fotos reales:
**33.3% del total** contra el 98.5% pedido. Los cuatro caminos que se probaron para
subirlo (resolución, preprocesado, voto entre lecturas, re-OCR del recorte) están
**descartados con medición** en `docs/known-issues.md` §21: no los vuelvas a intentar sin una
razón nueva. Lo que queda es un segundo motor (EasyOCR, ya soportado en el código y no
instalado) y muchas más fotos rotuladas.

> **Si refactorizas código que estos scripts mutan, actualiza la ruta.**
> `verify_capture_mutations.py` mutaba `app/api/tickets.py`; al mover la
> persistencia a `app/services/ticket_persistence.py` hay que reapuntarlo. Si no,
> el script cuenta la defensa como no verificada, y lo hace **en silencio**: un
> test que no corre parece un test que pasa.

### El `.vscode/` se versiona, y no es inocuo
`settings.json` y `tasks.json` están en el repo; el resto de la carpeta sigue ignorado.
**Un `settings.json` versionado ejecuta cosas:** `terminal.integrated.profiles` redefine la
terminal, `python.analysis.extraPaths` secuestra la resolución de un módulo, `http.proxy`
desvía el tráfico y `security.workspace.trust.enabled: false` apaga la pregunta de confianza.
Son *declaraciones*, no scripts, así que un review se las pasa por alto.

Lo que se calla son **dos reglas, y solo dos**: `reportMissingImports` (las dependencias viven
en la imagen de Docker) y `reportArgumentType` (los 164 avisos son el artefacto `Column[X]`
de SQLAlchemy 2.0). La segunda deja de cazar un `float` donde va un `Decimal`, que es el bug
que estas mismas reglas prohiben; la red que lo cubre es el criterio A6 del contrato, un test
que corre en los 673 y no solo con el editor abierto. **No añadas una tercera sin medirla.**

Dos detalles que no son detalles:
- **Si `git status` no lista `.vscode/settings.json`, no lo arregles con `git add -f`.** Eso
  funciona una vez y el siguiente `git add .` lo pierde. La causa es `~/.gitignore_global` con
  `.vscode/` (la carpeta) en vez de `.vscode/*` (el contenido): git no baja a un directorio
  excluido, así que la `!` del repo es letra muerta. `test_git_no_lo_ignora` lo detecta.
- **Toda regla silenciada necesita su comentario pegado a la línea.** Sin eso, callar una regla
  es un cambio de una línea que nadie cuestiona.

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

3. **Un PDF con texto no toca el modelo.** Y ahora además: **el `file_type` del
   cliente no elige la ruta de lectura.** El formato sale de los bytes
   (`app/core/archivo_real.py`, lista cerrada de firmas) y lo declarado solo se
   acepta si coincide. Antes, mandar `file_type=image` con un PDF lo mandaba a
   visión y devolvía `confidence_source=llm`, que es el origen con el que agrupa
   el reporte de exactitud. **No lo vuelvas a usar como selector de cascada.**
   `docs/known-issues.md` §17 tiene la medición.
   Si las reglas devuelven algo útil (`provider_name` real y `total > 0`), se devuelve eso y no
   se llama al LLM. Mutación: `verify_capture_mutations.py:36-48`.

4. **El OCR tiene su propia tabla de confianza, más baja.** `CONFIANZA_POR_CAMPOS_OCR`.
    El OCR cambia caracteres sin avisar: `1,100.00` sale `l,l00.00`, y para un
    regex `1,1OO.OO` es un total tan válido como el otro. Solo la combinación
    completa (RFC **y** subtotal **y** fecha) auto-aprueba, y solo si
    `subtotal + IVA == total` cuadra. **`ConfidenceSource.OCR` es un valor
    aparte, no una variante de `LLM`**: el reporte agrupa por origen, y mezclar
    dos readers da un número que no describe a ninguno.

5. **La ruta de la carpeta se configura; no se pide.** `TICKETS_INPUT_DIR` es la
    única ruta que el escáner conoce. No hay endpoint, ni parámetro, ni campo en
    el body que acepte una carpeta, y no debe haberlos nunca:
    `POST /scan {"folder_path": "/"}` sería lectura arbitraria del disco. La
    contención se comprueba con `is_relative_to` **después** de `resolve()`,
    porque un symlink a `/etc` no tiene un solo `..` en el texto.

6. **La idempotencia del escáner usa `scan_files`, no `tickets.source_hash`.**
    `source_hash` deduplica por contenido y no puede contestar "¿este archivo ya
    se miró?". Sin `relative_path` + `content_hash` + `attempts`, cada corrida
    vuelve a gastar OCR en los mismos archivos.

7. **Un ticket con `reviewed_at` no se sobreescribe automáticamente.** Ni con
    `reprocesar=True`. El único camino que lo hace es
    `POST /scan/files/{id}/reprocess`, que es explícito. Perder trabajo humano en
    silencio es peor que no reprocesar.

8. **El idempotente es `(company_id, source_hash)`, no `source_hash`.**
   El SHA-256 no lleva empresa dentro: el mismo comprobante en dos empresas es legítimo.
   Índice único parcial `WHERE source_hash IS NOT NULL`.

9. **El usuario se re-consulta en la BD en cada request.**
   `get_current_user` no confía en los claims. Desactivar una cuenta surte efecto inmediato,
   no en 8 horas cuando caduca el token.

10. **El rate limit va *antes* de gastar scrypt.**
   Si no, el bloqueo se vuelve amplificador de DoS.

11. **En XLSX solo `forzar_texto`; el apóstrofo es para CSV.**
   `neutralizar_formula` mete `'` que Excel consume pero **se ve** en la celda. En XLSX
   `forzar_texto` pone `data_type = "s"` y nada más.

12. **Allowlist de encodings, no denylist.**
   `ENCODINGS_PERMITIDOS = {utf-8, latin-1, cp1252, iso-8859-1}`. utf-7 → XSS, zlib/bz2 → bomba.

13. **El `content_type` que se sirve no es el que declaró el cliente.**
   `content_type_servible()` aplica lista cerrada de 7 tipos; el resto es
   `application/octet-stream`. `text/html` y `image/svg+xml` excluidos a propósito.

14. **La aritmética del documento se comprueba antes que RFC y fecha.**
    Es el único check que no depende de nada externo; si va después, un total malformado
    cortocircuita la validación.

15. **Una relectura escribe lo que leyó, también cuando no leyó nada.**
    `_actualizar_ticket` ponía la fecha solo `if extracted.expense_date is not None`, y
    eso dejaba una fila que se contradice: `date_missing` en `validation_errors` con
    `2021-08-15` de una lectura vieja. Ahora usa la misma convención provisional del
    alta (`extracted.expense_date or date.today()`), con su `date_missing` al lado. No se
    puede conservar la fecha anterior: si la hubiera puesto una persona, el ticket
    tendría `reviewed_at` y no habría llegado aquí (regla 7).

16. **`SECRET_KEY` con entropía inventada: >= 32 caracteres, y sin comodines.**
    Además, `CORS_ORIGINS` **no admite `"*"`**: con `allow_credentials=True`, Starlette
    refleja cualquier origen y eso deja que cualquier sitio web lea la API con la
    sesión del navegador (`config.py:_revisa_el_cors` aborta el arranque).
    Lo que **no** hay es revocación de tokens: uno robado vive 8 horas.

17. **scrypt con los parámetros de OWASP, y los del hash, no los del módulo.**
    `N=2**17, r=8, p=1`. El `2**14` que había antes **con `p=1` no estaba** en la lista
    de la guía (2\*\*14 solo aparece con p=5). El formato lleva sus propios parámetros y
    `_VERSION_HASH` no sube al cambiar N, o los hashes viejos dejan de validar y todos
    los usuarios reciben "contraseña incorrecta" por una contraseña correcta.
    `maxmem` se calcula con `_maxmem_para(n, r)`: con una constante, subir N rompe todos
    los logins a la vez.

18. **La fecha del comprobante se lee junto al folio, no en cualquier línea.**
    `_fecha_del_comprobante`. Una fecha suelta puede ser el vencimiento de un cupón
    (`TUS PUNTOS VENCEN: 31/10/2026`), y una fecha equivocada que pasa los checks
    (`date_in_future` no objectiona a octubre siendo septiembre) mete el gasto en otro
    mes. Sin marca de comprobante (folio, nota, `#`, `No:`) no se lee. OJO: esto **no**
    defiende contra los años mal leídos — eso es `date_in_future` del gate —; defiende
    contra que la fecha del documento sea la de otra cosa.

---

## Trampas verificadas

Estas no son opiniones: se comprobaron leyendo el código. Morar en ellas cuesta tiempo real.

- **Orden de rutas en FastAPI importa y el primer match gana.**
  En `tickets.py` las rutas literales (`/review-queue`, `/spot-check`, `/accuracy`) van
  **antes** de `/{ticket_id}`. En `reconciliations.py`, `GET /{reconciliation_id}` va al
  **final**, después de `/mappings` y `/export/*`, con un comentario que lo explica.
  En `app/api/scans.py` las literales (`/stats`, `/config`, `/ocr`) van antes de
  `/files/{file_id}`, y `test_stats_no_choquea_con_files_por_id` lo comprueba con
  una llamada real, no leyendo el código.
  Al añadir una ruta literal nueva, declárala **antes** de cualquier placeholder.
  Solo colisionan las de **un solo segmento** (`/mappings`), no las de dos o más
  (`/export/excel`, `/mappings/{id}`). Ya se rompió una vez: `docs/known-issues.md` §1.

- **El texto del OCR tiene que conservar las líneas.** `image_to_data` devuelve los
  números de bloque/párrafo/línea; si se ignoran y se une cada palabra con un salto
  de línea, el parser no encuentra nada (los regex están anclados a `label: valor` en
  una línea) y el ticket sale **vacío con la confianza más alta de la cascada**. Es
  el fallo más caro de este trabajo y no se manifiesta como error.

- **El piso de caracteres del OCR es 60, no 120.** Un comprobante mínimo real
  (proveedor, RFC, fecha, subtotal, IVA, total) son 112 caracteres; con 120 se
  rechazaban tickets válidos y cada foto iba a vision a pagar un modelo.

- **Ninguna fecha `AAAA/MM/DD` se leía.** El parser reconocía `DD/MM/YYYY` y
  `AAAA-MM-DD`, no `AAAA/MM/DD`, que es como los tickets de autocomERCio en México
  imprimen la fecha. Corregido en `parser_service.py`.

- **`pytesseract` no es el binario.** Es el envoltorio de Python. Sin
  `tesseract-ocr` del sistema, `OCR_ENABLED=true` falla por cada foto en vez de al
  arrancar. El Dockerfile instala el binario **y** `tesseract-ocr-spa` en la misma
  capa, y `GET /scan/ocr` reporta el motivo exacto.

- **Nada de OCR se importa al arrancar.** `easyocr` arrastra torch (~2 GB). La app
  tiene que poder arrancar para poder *decir* que no hay Tesseract.

- **El router del escáner es `app/api/scans.py`, no `scan.py`.** El repo nombra
  los routers en plural y los schemas en singular; con `scan.py` en los dos, era el
  único nombre que no seguía la convención. `app/core/archivo_real.py` lo escribió
  `feature/informe-cierre-mensual` y lo amplió esta rama: hay **un solo** archivo y
  **un solo** test, no dos copias.

- **La persistencia vive en `services/ticket_persistence.py`,** y
  `app/api/tickets.py:_persist_extracted` es una delegación. El escáner la necesita
  y no puede importar de un router sin invertir la capa.

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

- **Hay cuatro `.env` y sólo uno se versiona.** `.env.example` es la plantilla (versionada);
  `.env` lleva `POSTGRES_PASSWORD` y lo lee `docker compose`; `.env.dev` lleva la configuración
  de la app y **no lleva secretos**; `.env.local` lleva la `SECRET_KEY` y **gana** sobre
  `.env.dev`. `config.py` lee los dos últimos en ese orden, el mismo de `docker-compose.yml`:
  antes leía solo `.env.dev` y por eso `uvicorn` local firmaba con una clave distinta a la del
  contenedor. Los dos con secretos van en `chmod 600`. Regla: **`.env.dev` no lleva
  contraseña ni clave**; la `DATABASE_URL` de ahí va sin contraseña porque Docker la
  sobrescribe. Un test (`TestLaPlantillaDeLosEnv`) lo hace cumplir, porque `.gitignore` y
  `git status` no delatan nada de esto.

- **El puerto de Ollama publicado es 11435.** En esta máquina hay un Ollama nativo en
  `127.0.0.1:11434` y publicar el mismo puerto en loopback no arranca. La app habla con
  Ollama por la red interna (`OLLAMA_BASE_URL=http://ollama:11434`).

- **Los puertos van en `127.0.0.1:`.** Sin el prefijo, Docker publica en `0.0.0.0` y la base
  de datos con su contraseña queda al alcance de la red local.

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
