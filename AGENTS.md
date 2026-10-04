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
| API | `app/api/` — 9 routers bajo `/api/v1` |
| Captura | `app/services/capture.py` → cascada de 5 escalones |
| OCR local | `app/services/ocr.py` → Tesseract, lazy |
| Escáner | `app/services/scan_service.py` → recorre `TICKETS_INPUT_DIR` |
| Veredicto | `app/services/confidence_gate.py` → checks + umbrales |
| Persistencia | `app/services/ticket_persistence.py` (la llama `app/api/tickets.py:_persist_extracted`) + `document_service.py` |
| IA | `app/services/ai_client.py` (Ollama / OpenAI / Azure / local) |
| Auth | `app/core/security.py` (scrypt N=2\*\*17 + JWT HS256 escritos a mano, **sin PyJWT**) |
| Esquema | `db/init.sql` + migraciones numeradas en `db/migrations/` |
| Inventario | `app/services/inventario_service.py` → compras, kardex y stock |
| Front | `front/src/` — React 18 + Vite + TS + Tailwind, 8 páginas |
| Tests | **1068** — 796 unit + escáner, 256 integration, 1 skip |

### Rutas que existen
`/auth` · `/dashboard` · `/categorias` · `/companies` · `/tickets` · `/bank-transactions` · `/reconciliations` · `/scan` · `/inventario`
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

python3 -m pytest tests/ -q           # 982
python3 -m pytest tests/unit -q
python3 -m pytest tests/integration -q
```

### Verificación por mutación — correr tras tocar defensa
Cada defensa de seguridad tiene un test que **muere si quitas la defensa**. Si tocas una,
corre su verificador:

```bash
python3 scripts/verify_scan_mutations.py            # 17 mutaciones: escaner de carpeta
python3 scripts/verify_capture_mutations.py         # 26 mutaciones: ruta de captura
python3 scripts/verify_export_mutations.py          # 5 mutaciones: fórmulas
python3 scripts/verify_auth_mutations.py            # 42 mutaciones: auth y coste del hash
python3 scripts/verify_documentos_mutations.py      # 11 mutaciones: el papel no se altera
python3 scripts/verify_reconciliation_mutations.py  # 18 mutaciones
python3 scripts/verify_spot_check_mutations.py      # 20 mutaciones: muestreo
python3 scripts/verify_vscode_mutations.py          # 18 mutaciones: superficie de confianza
python3 scripts/verify_postgres_inventario.py       # 10 defensas: kardex, firma, índices
```

### Inventario: la compra que suma stock exige una persona
Cuatro tablas nuevas (`0010`): `productos`, `compras`, `compra_items`, `movimientos_inventario`.

**Los tres estados son `EstadoCompra`, NO `ExtractionStatus`.** Son dos máquinas distintas:
`ExtractionStatus` responde *"cómo se leyó el papel"*; `EstadoCompra` responde *"el inventario ya
contó esto"*. Un ticket puede estar `AUTO_APROBADO` y su compra en `EN_REVISION`, porque que la
lectura del encabezado sea buena no dice nada de las líneas. **El gate nunca midió la confianza
de una línea** — `confianza_por_campos` solo mira los campos del encabezado.

```
PROCESAR  → EN_REVISION  → PROCESADO   ← solo este suma stock
```

**Por qué el movimiento es humano, y no prudencia genérica.** Todo esto está medido:
- El OCR sobre fotos reales da **33.3%** de exactitud (`AGENTS.md`; los cuatro caminos para
  mejorarlo están descartados con medición en `docs/known-issues.md` §21).
- Una línea es un tiro más que el total. En 15 líneas, que las 15 estén bien **no** tiene la
  misma probabilidad que el total esté bien: se multiplican.
- **La conciliación bancaria valida el MONTO, nunca la COMPOSICIÓN.** Un ticket puede dar
  `PERFECT` contra el banco y tener las líneas equivocadas.
- La deriva es **monotona**: una cantidad de más infla el stock para siempre, y las ventas las
  cuentas tú. La diferencia entre lo contado y lo que dice el sistema crece sin techo.

Cuatro reglas con test que muere si la quitas:

1. **`compras.ticket_id` es UNIQUE.** Es lo único que impide contar dos veces el mismo
   comprobante. `registrar_compra` también es idempotente, para que la segunda llamada devuelva
   la compra existente en vez de reventar.
2. **`ck_compras_confirmacion`: `PROCESADO` exige `confirmada_por` y `confirmada_at`; los demás
   estados los prohíben.** Sin ella, un `UPDATE` basta para que el inventario parezca autorizado.
3. **No se confirma con líneas sin producto.** Es un bloqueo, no un aviso: si se autorizara,
   esas líneas no entrarían al inventario y el stock quedaría incompleto sin rastro.
4. **`movimientos_inventario` es append-only por trigger**, no por convención: `UPDATE` nunca,
   `DELETE` nunca mientras el producto exista. Corregir es agregar un `AJUSTE`. Es lo que hace
   que `SELECT SUM(cantidad)` sea la definición del stock y no una opinión.

**El stock NO es una columna.** Es `SUM(movimientos_inventario)`; `stock_de()` lo calcula. Una
columna `stock` es una copia, y una copia se desincroniza siempre sin que nadie lo note.

**Las líneas sin producto NO crean un producto automáticamente.** Con OCR al 33%, un catálogo
armado solo se llena de variantes (`Reginen de` / `Regin de`) que el sistema contaría como tres
y entre las que el stock se repartiría. La cola es `compra_items WHERE producto_id IS NULL` — no
hay tabla de "pendientes", y por eso no se puede desincronizar.

### `items` se extraía y se tiraba (arreglado en `0010`)
El modelo ya pedía las líneas (`ai_extractor.ExtractedInvoice.items`) desde antes de que
existiera el inventario, y `capture.py:invoice_to_result` no las copiaba al
`TicketExtractionResult`: **la línea desaparecía antes de llegar al gate**. Ahora se persisten en
`tickets.items` como JSON crudo, y `compra_items` es la versión normalizada y revisada.

**Un `Decimal` en `items` no es JSON, y el ticket se perdía (arreglado).**
`ai_extractor.py:346-348` convierte a `Decimal` cantidad, precio e importe de cada línea —que es lo
correcto para dinero— y `tickets.items` es una columna JSON, así que `json.dumps` reventaba:
`TypeError: Object of type Decimal is not JSON serializable`. El INSERT del ticket moría y
`procesar_archivo` lo reportaba como `accion=ERROR` con el comprobante perdido.

Lo que lo hace caro es que **solo pasaba con facturas que tienen detalle de partidas**. Las que
no, se guardan bien, así que el camino del LLM se podía dar por bueno entero mientras no se le
pasara una con `items`. Se midió con un `ticket_mercado_simple.pdf` de 4 líneas.

La columna es `JSONConDecimal` (`app/core/json_decimal.py`), no `JSON` pelado: un `TypeDecorator`
que convierte `Decimal` a **texto**, recursivo, porque las líneas son `list[dict]` y un `Decimal`
vive dos niveles dentro. Y es texto y no `float` a propósito: `0.1 + 0.2` en binario no es `0.3`, y
aquí la aritmética decide si una compra cuadra con su total. Convertir a `float` "por
compatibilidad" se comería centavos en silencio.

No se define un `json_encoder` global: cambiaría el comportamiento de **todas** las columnas JSON
de la app sin que nadie lo pidiera, y el bug reaparecería en cualquier sitio nuevo sin que nadie lo
mirara. El arreglo es local a la columna que lo tiene.

**La ruta OCR no extrae líneas.** Solo el LLM lo hace. Un ticket leído con Tesseract llega con
`items = NULL`, y por eso `registrar_compra` devuelve `None` sin error: es el caso normal, no un
fallo. Para inventario por foto hay que resolverlo antes —ver la advertencia del punto 2.

### Las fotos no se veían: `content_type` NULL y el `<object>` fijo en PDF
Dos bugs juntos, y los dos hacen falta para que una foto no se pinte. **Medido: 7 de 9 documentos
guardados tenían `content_type=NULL`** y se servían como `application/octet-stream`, que el
navegador **descarga** en vez de pintar. Los otros 2 sí lo traían porque entraron por una subida
HTTP; ninguno de los dos había pasado por el escáner, que es el que no declara nada.

**El tipo se deduce de los BYTES, y se deduce al SERVIR.** `media_type_real()` en
`app/core/archivo_real.py` devuelve el medio real del archivo, y
`TicketDocumentModel.content_type_servible` lo usa cuando la columna viene en `NULL`.

Que sea al servir y no al guardar **no es un detalle de implementación, es que no se puede de otra
forma**: `ticket_documents` es append-only por trigger, y backfillear la columna pide un `UPDATE`
que Postgres rechaza a propósito. Medido:

```
ERROR:  ticket_documents es append-only: un documento no se actualiza, se agrega una version nueva
```

Un backfill habría necesitado un `ALTER` o apagar el trigger, es decir, saltarse la regla de que el
papel no se altera — y escribir sobre una columna que es un hecho del papel. El tipo que se **sirve**
es una decisión de ahora, no un atributo de lo que se subió en marzo.

Y es lo que arregla las fotos de verdad: **un backfill habría dejado las 7 viejas sirviendo como
descarga para siempre**, con el bug visible solo en las nuevas. Así se esconden estos fallos.
`guardar_documento` también deduce el tipo al escribir, pero eso es una mejora para lo que se suba
de aquí en adelante, **no la defensa**: si se quita, lo único que se pierde es la columna.

**El nombre del archivo tampoco decide, y hay el caso que lo prueba.** `IMG_4253 2.HEIC` está
guardado y **por sus bytes es un JPEG** — la cámara del teléfono lo nominó así. Keyear por extensión
lo habría servido como HEIC. La foto es la misma; lo que falla es la etiqueta.

**La lista cerrada sigue mandando.** Lo deducido no es lo que se sirve: un HEIC deduce
`image/heic`, cae fuera de `CONTENT_TYPES_SERVIBLES` y sale como `application/octet-stream`, que es
lo correcto porque ningún navegador de escritorio lo pinta. Las dos mitades están separadas a
propósito: `media_type_real` dice **qué es**, el allowlist decide **si se puede pintar**.

**En el front, `<object type="application/pdf">` estaba fijo para todos los documentos.**
`TicketDocumento.tsx` declaraba PDF a cualquier cosa, así que un JPEG nunca se pintaba aunque
llegara con el tipo correcto. Ahora el tipo sale del `Blob` (`blob.type`, que viene del
`Content-Type` de la respuesta) y una imagen va en `<img>`. `<img>` y no `<object>` porque
`<object>` vuelve a pedir el recurso **sin la cabecera `Authorization`** y sale 401: el `Blob` ya
viene autenticado.

### `POST /scan` devuelve los datos de cada ticket, no solo el metadata
Cada elemento de `detalles[]` trae un `datos` con lo que el lector afirmo que dice el papel:
proveedor, RFC, total, subtotal, IVA, fecha, categoría y líneas, **más el veredicto** del gate
(`confidence`, `confidence_source`, `extraction_status`, `validation_errors`, `reviewed_by/at`).

Tres decisiones, y las tres importan:

- **Los datos vienen de la fila de `tickets`, no de la lectura cruda.** Es la diferencia que hace
  que la respuesta sea citable: si el gate mandó la lectura a revisión, ahí `extraction_status` lo
  dice **al lado** de los números. Un JSON con los datos pero sin el veredicto deja a quien lo
  consume creyendo que el sistema respondió por ellos, que es justo lo que el gate se negó a
  afirmar. Por eso van en el MISMO objeto y no en campos sueltos que se puedan leer por separado.
- **`datos` se resuelve DESPUÉS del bucle** (`_resolver_datos_de_tickets`), en una sola consulta,
  y no dentro de `procesar_archivo`. Esa función tiene trece salidas y el dato depende de una fila
  que a veces todavía no existe: el DUPLICADO no crea ticket y apunta al de otro archivo, el
  OMITIDO por `company_id` no creó ninguno, y el ACTUALIZADO pudo encontrar el ticket ya borrado.
  Resolverlo al final, desde el `ticket_id` que cada rama ya dejó escrito, es lo único que cubre
  las trece sin trece copias de la misma asignación.
- **`datos=null` cuando no hay ticket, y no un objeto de campos vacíos.** Un objeto lleno de `None`
  es *peor* que un `null`: parece que el sistema leyó el comprobante y no encontró nada, cuando lo
  que pasó es que no hay ticket del que sacar datos. Son dos cosas que piden acciones distintas.

**`raw_text` no va en la respuesta.** Son hasta 20 000 caracteres por ticket y no es un dato: es la
evidencia. Quien la necesite la pide con `GET /tickets/{id}`, que además la sirve con el documento al
lado. Y `items` sale como `list[dict]` **crudo**, sin un modelo de línea tipado: las líneas se
persisten sin normalizar, e inventar `descripcion`/`cantidad`/`precio_unitario` afirmaría una
estructura que el sistema nunca verificó — con OCR al 33% esa afirmación sería falsa seguido. Lo
que normaliza es `inventario_service.interpretar_items`.

### El comprobante se mueve a `Ticket_Scan`, y el montaje ya no es `:ro`
`TICKETS_SCAN_OUTPUT_DIR` es una **carpeta hermana**, montada aparte, y no una subcarpeta de la
que se escanea: con `TICKETS_SCAN_RECURSIVO=True`, una subcarpeta haría que cada archivo movido
se volviera a leer en cada corrida (su `relative_path` cambia, el ledger no lo reconoce).

Se mueve **solo cuando la compra está `PROCESADO`**, nunca por veredicto de lectura — con 33.3%
de exactitud, mover también lo `PENDIENTE` habría enterrado dos de cada tres comprobantes en una
carpeta que dice "escaneados", sin que nadie los hubiera revisado. `archivado_service` **nunca
sobreescribe**: si el destino existe, agrega sufijo.

El `:ro` → `:rw` de `docker-compose.yml` es un cambio consciente, y el motivo está escrito en el
propio archivo. Lo que se necesita es **mover**, no borrar: los bytes no cambian.

**El movimiento es configurable en DOS ejes, y son decisiones distintas:**

| ajuste | qué hace |
|---|---|
| `TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR` | mueve en cuanto el sistema digitaliza (por omisión `true`) |
| `TICKETS_SCAN_ARCHIVAR_AL_CONFIRMAR` | mueve cuando alguien confirma la compra |

Con los dos en `true`, el segundo casi nunca dispara: el archivo ya se movió al escanear.
**La carpeta de escaneados NO significa "esto se leyó bien"** —significa "el sistema ya lo
intentó"— y por eso `POST /scan` devuelve `archivados_pendientes`, el número de los que se movieron
sin leerse bien. Con OCR al 33.3% ese número es alto y es **visible**, que es la diferencia entre
una decisión y un silencio.

**Usa `simular: true` antes de la primera corrida real.** La primera deja la carpeta de entrada
vacía, y conviene ver eso antes de que ocurra. No crea ni mueve nada; devuelve la ruta de destino
de cada archivo y si su lectura quedó pendiente.

**`TICKETS_SCAN_OUTPUT_DIR` vacía significa "hermano de la carpeta de entrada"**, no una ruta fija.
Es lo que hace que la omisión funcione fuera de Docker: una ruta de contenedor (`/tickets_scan`)
da `Read-only file system` en macOS. Y `docker-compose.yml` la fija en el `environment:` del
servicio — igual que `TICKETS_INPUT_DIR` — porque sin eso el archivado resuelve `/Tickets_Scan`
dentro del contenedor y los comprobantes desaparecen de la máquina. **Medido: pasó, y los
archivos hubo que devolverlos a mano.**

**No se archiva lo que no produjo ticket**, y eso incluye `ERROR`, `DUPLICADO` y `NO_SOPORTADO`: un
archivo que el sistema no pudo leer es justo el que alguien tiene que mirar. Y un ticket ya
atribuido a otra empresa **no se mueve**.

### La colección de Insomnia es un artefacto generado, no escrita a mano
`expense-reconciler-insomnia.json` sale de `scripts/generar_insomnia.py`, que lee el OpenAPI
**de la API viva**. No lo edites a mano: se pierde en la siguiente regeneración.

```bash
make insomnia         # regenerar (52 peticiones, 9 carpetas)
make insomnia-check   # sale 1 si está desfasado — para cuando añades una ruta
```

Por qué importa: la versión anterior estaba exportada del **2025-01-21** con 25 peticiones de
las 52 que expone hoy, y —esto es lo que la hacía inútil— **sin un solo header `Authorization`**.
Como `api_router.py` pone el token obligatorio a nivel de router, 51 de 52 rutas respondían 401.
Un archivo que no sabe que hay rutas nuevas no avisa; por eso es generado.

Dos cosas que el generador respeta y que no hay que romper al tocarlo:
- **`password` sale vacía.** El archivo está versionado con remoto en GitHub; una contraseña
  escrita ahí queda publicada. Mismo criterio que `.env.dev` (regla de *Reglas que no se rompen*).
- **`base_url` ya trae `/api/v1` y las rutas del spec también.** Sumar las dos da
  `/api/v1/api/v1/...`, que es un 404 limpio, no un error de sintaxis. Es la misma trampa que
  documenta `api_router.py` vista desde el cliente, y salió al verificar, no al leer.

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

18. **El comprobante digitalizado NO SE ALTERA: se apila.**
    `PUT /tickets/{id}/documento` hace `INSERT` de una versión nueva con
    `reemplaza_a` apuntando a la anterior, y **no borra nada**. El vigente es el
    de mayor `version`. `actor` y `motivo` son obligatorios desde la segunda
    versión, en el servicio **y en la base**
    (`ck_ticket_documents_*`). Un trigger de Postgres prohíbe `UPDATE` siempre y
    `DELETE` mientras el ticket exista: la regla es del motor, no del código, y
    por eso vale también para un `psql`. La excepción es la cascada —borrar el
    gasto borra su papel— y se comprueba mirando si el ticket padre sigue
    existiendo.
    Antes esto era `DELETE` + `INSERT` sin dejar ni hash anterior, ni autor, ni
    fecha: la evidencia se podía borrar en silencio.

19. **La fecha del comprobante se lee junto al folio, no en cualquier línea.**
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
