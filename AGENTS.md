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
| API | `app/api/` — 10 routers bajo `/api/v1` |
| Captura | `app/services/capture.py` → cascada de 5 escalones |
| OCR local | `app/services/ocr.py` → Tesseract, lazy |
| Escáner | `app/services/scan_service.py` → recorre `TICKETS_INPUT_DIR` |
| Veredicto | `app/services/confidence_gate.py` → checks + umbrales |
| Persistencia | `app/services/ticket_persistence.py` (la llama `app/api/tickets.py:_persist_extracted`) + `document_service.py` |
| IA | `app/services/ai_client.py` (Ollama / OpenAI / Azure / local) |
| Auth | `app/core/security.py` (scrypt N=2\*\*17 + JWT HS256 escritos a mano, **sin PyJWT**) |
| Esquema | `db/init.sql` + migraciones numeradas en `db/migrations/` |
| Inventario | `app/services/inventario_service.py` → compras, kardex y stock |
| Informe | `app/services/reporte_cierre.py` → cierre mensual (JSON + PDF) |
| Front | `front/src/` — React 18 + Vite + TS + Tailwind, 8 páginas |
| Tests | **1400** — 1014 unit + escáner, 386 integration, 2 skip |

### Rutas que existen
`/auth` · `/dashboard` · `/categorias` · `/companies` · `/tickets` · `/bank-transactions` · `/reconciliations` · `/scan` · `/inventario` · `/usuarios` · `/reports`
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

python3 -m pytest tests/ -q           # 1400
python3 -m pytest tests/unit -q
python3 -m pytest tests/integration -q
```

> **`--import-mode=importlib` en `pytest.ini`, y por qué está.** Hay dos archivos
> llamados `test_reporte_cierre.py`, uno en `unit/` y otro en `integration/`: los
> dos nombres los fija `docs/contrato-producto.md` §5.3, así que no se pueden
> renombrar. Con el modo `prepend` de python dos módulos con el mismo nombre base
> colisionan y pytest aborta el collection entero. La alternativa clásica
> (`__init__.py` en cada carpeta) cambiaría el layout de todo el árbol de tests por
> un problema de un solo par de archivos.

### El informe de cierre mensual — el documento que faltaba
`app/services/reporte_cierre.py` + `app/api/reports.py` (`/api/v1/reports`).
**Es la Fase 1 del contrato** y es el eslabón que cierra la cadena de custodia
(«hoy existe entera salvo el último eslabón: el papel»).

**Un objeto, dos salidas.** `construir_reporte()` devuelve un
`ReporteCierreMensual`; el JSON y el PDF salen de ÉL. Si el PDF calculara su
propia exactitud, un día diría 94% y el tablero 92%, y el contador vería dos
números en la misma pantalla. En el PDF **no se recalcula nada**.

**El informe NO tiene `emitido_en`.** Tiene `fecha_referencia`, derivado del
periodo. Es lo que hace posible **R4** (mismo periodo + misma base → PDF
byte-idéntico): `fpdf2` sella `datetime.now()` en cada archivo, y sin
`set_creation_date` dos descargas del mismo mes difieren SIEMPRE. Un documento
cuyos bytes cambian no sirve como evidencia de nada.

**`puede_cerrarse` es un `@computed_field`, no un campo.** Se deriva de
`pendientes.hay_pendientes` porque era un campo aparte y se desincronizaba: el PDF
imprimía literalmente `El periodo NO se puede declarar cerrado: None.` Esa fila es
un dato inventado en un documento firmado. Un campo derivado no puede mentir
sobre sus propias entradas.

**El cuarto estado del periodo: `CERRADO_CON_PENDIENTES`.** `cerrado` (el hecho,
de `cierres_periodo`) y `puede_cerrarse` (lo que R3 permite afirmar hoy) pueden
discrepar: alguien cerró enero y después se metió un ticket de enero. Con tres
estados eso se reportaba como `NO_CIERRA`, que es **falso** —enero sí está
cerrado en el registro— y tapaba el hallazgo más útil del informe.

**`DISCREPANCY` y el ticket nunca conciliado bloquean el cierre.** Un gasto del
mes sin fila en `reconciliations` está *sin conciliar* aunque tenga categoría: si
nadie lo cruzó contra el banco no se sabe que el dinero salió.

**El «sin categoría» del informe cuenta SÓLO el periodo**, y es lo contrario de
`analitica.pendientes_de_clasificar`, que cuenta todo el histórico porque su
pregunta es «cuánto trabajo tengo pendiente». Aquí la pregunta es «puedo declarar
cerrado ESTE mes»: si no, ningún periodo cerraría nunca. Los pendientes de enero
salen en el informe de enero.

**Un comparativo contra un mes en curso NO es concluyente, y lo dice.** Con el
corte al mismo día (`contrato-producto.md` §5.1) comparar 6 días contra 31
produce una caída que no existe. `ComparativoMes.conclusivo=False` + `nota` en la
primera página.

**Las fuentes core de PDF son latin-1.** `fpdf2` con Helvetica — sin fuente
Unicode embebida, para que R4 no dependa de la versión del `.ttf` que haya en
cada máquina. El precio es `_latin1()`: una razón social con un guion largo tumba
la generación con `FPDFUnicodeEncodingException`. Un `?` es preferible a un 500.

**Y `pdf.output()` devuelve `bytearray` en fpdf2 2.8**, no `bytes`:
`bytes(pdf.output())` o starlette revienta al codificar, en una ruta cuyo JSON sí
funciona.

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
python3 scripts/verify_reporte_mutations.py          # 17 mutaciones: informe de cierre + DDL
python3 scripts/verify_vscode_mutations.py          # 18 mutaciones: superficie de confianza
python3 scripts/verify_postgres_inventario.py       # 17 defensas: kardex, firma, índices, signo
#   Las 3 del signo se pueden romper a proposito: revirtiendo `IF NEW.tipo = 'SALIDA'`
#   a `IF NEW.tipo = 'ENTRADA'` en `db/migrations/0013_compras_rechazables.sql`, el
#   script tiene que salir con 1. Se comprobó. Ver `docs/known-issues.md` §26.
```

### Inventario: la compra que suma stock exige una persona
Cuatro tablas nuevas (`0010`): `productos`, `compras`, `compra_items`, `movimientos_inventario`.

**Los cuatro estados son `EstadoCompra`, NO `ExtractionStatus`.** Son dos máquinas distintas:
`ExtractionStatus` responde *"cómo se leyó el papel"*; `EstadoCompra` responde *"el inventario ya
contó esto"*. Un ticket puede estar `AUTO_APROBADO` y su compra en `EN_REVISION`, porque que la
lectura del encabezado sea buena no dice nada de las líneas. **El gate nunca midió la confianza
de una línea** — `confianza_por_campos` solo mira los campos del encabezado.

```
PROCESAR  → EN_REVISION  → PROCESADO   ← solo este suma stock
                 ↓
            RECHAZADO  ← "esto no es una compra", y se puede deshacer
```

### La compra se puede rechazar, y `PROCESADO` no se puede deshacer
Cuatro reglas con test que muere si las quitas:

1. **`RECHAZADO` es un estado, no un borrado.** `compras.ticket_id` es UNIQUE: borrar
   deja el ticket libre y el próximo reescaneo vuelve a crear la compra desde
   `registrar_compra`. El rechazo se deshacía solo cada vez que se tocaba el comprobante.
2. **`confirmar_compra` respeta RECHAZADO.** Escribir el estado no basta: lo que lo hace
   una decisión es que la única operación que mueve stock lo mire. Sin ese `if`, el
   rechazo se deshacía llamando a `confirmar`.
3. **Una compra PROCESADA no se rechaza ni se reabre.** Ya sumó stock y el kardex es
   append-only por trigger: `UPDATE` nunca, `DELETE` nunca. El 409 dice que la vía es el
   `AJUSTE`, porque sin ese camino en el mensaje quien lo lea busca una pantalla que no
   existe.
4. **`rechazar` es idempotente.** Dos veces no es error: es la misma operación, y un
   cliente que reintenta tras un timeout debe poder hacerlo sin recibir un 409 que no
   sabe leer.

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

### El signo del kardex es una sola pregunta, y el SQL tiene que decir lo mismo
`TipoMovimiento.suma_stock` es la fuente de verdad: **`SALIDA` resta, todo lo demás suma.**
`stock_de()` la consulta y el trigger de Postgres la replica en SQL con
`CASE WHEN tipo = 'SALIDA' THEN -cantidad ELSE cantidad END`.

Antes **no coincidían**, y era el defecto más caro que quedaba en pie:
- el trigger hacía `IF tipo = 'ENTRADA' THEN suma ELSE resta`, o sea que **`AJUSTE` restaba**
  mientras `stock_de()` lo sumaba;
- y su `SUM` de filas previas ignoraba el signo, así que con `ENTRADA 10` y `SALIDA 4`
  creía que había 14 en vez de 6.

**Consecuencia medida:** con 6 de stock, una `SALIDA` de 7 (que deja **-1**) pasaba el
trigger. La defensa contra el stock negativo **no defendía**. Era inerte porque la única
vía que escribía en el kardex era `confirmar_compra`, y esa solo produce `ENTRADA`: el
`ELSE` del trigger no se ejecutaba nunca. `POST /inventario/movimientos` es el camino que
lo volvió vivo. Arreglado en `0013`.

**`AJUSTE` no tiene signo, y no se le da.** "Se contó de más" y "se contó de menos" son el
mismo `AJUSTE` para quien lee y signos opuestos para el stock, y `cantidad` es positiva por
`ck_movimientos_cantidad_positiva`. Por eso un ajuste se escribe **como `ENTRADA` o `SALIDA`
con `referencia_tipo='AJUSTE'`**, y el enum conserva `AJUSTE` solo como nombre de
`referencia_tipo`: de dónde viene la fila y qué le hace al stock son dos preguntas
distintas.

### `ENTRADA` sin compra se rechaza con 409, no con 422
`registrar_movimiento` no acepta una `ENTRADA` cuya `referencia_tipo` no sea `'AJUSTE'`.
Si existiera un camino que suma stock sin compra, sin línea y sin que nadie mirara el
papel, **`compras.ticket_id` UNIQUE dejaría de ser la garantía de que el inventario solo
refleja compras de verdad.** Con `es_ajuste=true` sí se acepta, y es lo que hace posible
una corrección que *suma*.

El stock insuficiente se comprueba **en Python, no solo en el trigger**, porque los tests
corren sobre SQLite y SQLite no tiene triggers: si la comprobación viviera solo en la
base, la suite pasaría y el fallo aparecería en producción como un `IntegrityError` sin
traducir. El 409 lleva los dos números —"hay 6 y pediste restar 10"— porque sin ellos no
se puede decidir si la cantidad estaba mal o el stock.

### `productos` se corrige, se da de baja, y **no se borra**
`PATCH /inventario/productos/{id}` es lo que hace falta para que la cola
(`?solo_sin_verificar=true`) se pueda **vaciar**: verificar, renombrar, precio, dar de baja.

Con test que muere si se quita:
- **Renombrar recalcula `nombre_normalizado`** por el `@validates` del modelo. Sin eso,
  limpiar la cola fusiona dos filas hoy y el siguiente reescaneo crea un tercero.
- **No hay `DELETE`.** `movimientos_inventario.producto_id` tiene `ON DELETE CASCADE`:
  borrar el producto borra su historial y `stock_de` deja de poder responder por él. Se
  da de baja con `activo=false`.
- **`company_id` y `origen` no son editables.** Mover un producto de empresa sacaría su
  kardex con él (el stock se contaría en las dos); y declarar `origen=MANUAL` sacaría un
  producto de OCR de la cola sin que nadie lo mirara.
- **`verificado=true` en un OCR sin código se rechaza.** Un código de barras es una
  identidad; una descripción leída es una opinión. Con código sí se puede.

`ProductoUpdate` usa `exclude_unset` en el router, no `exclude_none`: la diferencia entre
"no lo mandaron" y "lo mandaron en `null`". Con `exclude_none`, un `PATCH {"nombre": "x"}`
borraría el precio y el 200 llegaría igual.

**Las líneas sin producto NO crean un producto automáticamente.** Con OCR al 33%, un catálogo
armado solo se llena de variantes (`Reginen de` / `Regin de`) que el sistema contaría como tres
y entre las que el stock se repartiría. La cola es `compra_items WHERE producto_id IS NULL` — no
hay tabla de "pendientes", y por eso no se puede desincronizar.

### El impuesto es de la partida, y el gate lo tiene que saber
El gate comprobaba `subtotal + IVA == total`, lo que asume que un comprobante tiene **una**
tasa de impuesto. En México es falso, y medido sobre el ticket real de esta máquina:

```
SUBTOTAL       217.27
IVA 16.0%        8.14
IEPS 8.0%        8.59   <- no tenía dónde meterse
TOTAL          234.00    y 217.27 + 8.14 + 8.59 = 234.00 EXACTO
```

Con `MONEY_TOLERANCE` de un centimo, ese comprobante **no podía pasar** aunque los tres
números se leyeran perfecto. Y un gate que rechaza lecturas correctas es peor que un gate
flojo: hace que `subtotal_plus_tax_mismatch` deje de significar "leíste mal".

**El IVA NO es una tasa del subtotal, y por eso no se puede "detectar la tasa que falta".**
En ese Walmart el IVA es 8.14 sobre un subtotal de 217.27, o sea 3.7%, no 16%: solo una parte
de las partidas está a 16% y el resto a 0% (el papel lo marca con una letra al final de cada
línea: T, C, A). El IEPS de 8.59 es 4.0% del subtotal, tampoco 8%.

Se probó un heurístico —"si `subtotal + IVA` no da el total, busca una tasa fiscal conocida que
explique la diferencia"— y **se descartó porque es falso**: en la práctica marcaba como "un
impuesto raro" a cualquier total mal leído que se desviara alrededor del 4%, que es justo el
caso que tiene que seguir fallando (la foto de DSW, donde el OCR leyó el precio ya
descontado). Un clasificador que hace pasar errores de lectura es peor que no clasificar. El
test que lo fija está en `test_impuestos.py:TestElHeuristicoQueSeDescarto`, y comprueba que el
comportamiento descartado no se reintroduzca por accidente.

Lo que quedó es el dato exacto: **`ieps_amount`**, en pesos, y la suma de los tres números que
el papel imprime. No adivina nada. Y `ValidationOutcome` sigue teniendo **dos** cubos, no
tres: se intentó agregar `warnings` para el caso "no me square pero no es error de lectura" y se
quitó junto con el heurístico, porque sin él nadie lo llenaba.

**`tax_amount` es el IVA, y el prompt pedía "total de impuestos".** Eso no era cosmético:
`export_service` manda `tax_amount` bajo el encabezado **"IVA"** del archivo que ve el contador,
así que un comprobante con IVA 8.14 e IEPS 8.59 llegaba como **IVA 16.73**, sin marca de error.
El prompt ahora pide los dos separados y explica por qué (en `ai_extractor`, bloque "REGLAS DE
LOS IMPUESTOS"). El IEPS sale a **columna propia** en el Excel y en el CONTPAQI.

**El `Subtotal` del Excel dejó de ser un cálculo.** Era `total - IVA`, que con IEPS da 225.86
en vez de 217.27: la cuenta del archivo cuadraba (225.86 + 8.14 = 234.00) y el error quedaba
invisible. Ahora sale `tickets.subtotal`, y si no hay subtotal leído se deriva **solo cuando
sabemos que no hay IEPS**; con IEPS sin subtotal la celda queda vacía, que es lo que el resto
del archivo ya hace con `TipoCambio` y `MetodoPago`.

**El IEPS del comprobante NO va a `CAMPOS_VERIFICABLES`.** El muestreo pregunta "se leyó bien
este campo", y `ieps_amount` es `NULL` en la mayoría de los tickets porque casi nadie trae
IEPS: el que lo trae es un supermercado, y el resto del papeleo no lo tiene. Meterlo ahí
haría que el revisor marcara "incorrecto" en cada comprobante sin IEPS, y la exactitud medida
bajaría por la ausencia del campo y no por la calidad del
automatismo. Si algún día se agrega, hace falta primero una forma de decir "NULL era la
respuesta correcta".

**`TicketExtractionResult` NO está en `app/schemas/ticket.py`.** Está en `parser_service.py:427`,
que es donde uno lo busca por el nombre y no lo encuentra. Por eso el campo se agregó dos
veces a `TicketResponse` y ninguna a la clase que el escaner consume: el error seuprofen
manifestó como `AttributeError` en el escaner, tres capas más abajo y sin ninguna pista de
dónde venía. **Un test por capa habría pasado en los tres casos.**

**Cuatro vías, cuatro schemas.** `TicketUpdate` (el `PATCH /{id}`), `TicketReviewRequest` (la
cola de revisión), `TicketResponse` y `DatosTicketResponse` (el `datos` del escaneo). El campo
tenía que estar en **las cuatro** y en los dos sitios donde el gate se **vuelve a correr**
(`review_ticket` y `update_ticket`), porque en esos dos se releen los datos **guardados**: si
no, un ticket corregido no se puede aprobar nunca. `TicketReviewRequest` no aceptaba ni
`subtotal` ni `ieps_amount`; eso hacía que aprobar un ticket con IVA+IEPS desde la cola fuera
imposible, con un `422` que no decía qué número faltaba.

La defensa está en `tests/integration/test_ieps_extremo_a_extremo.py`, que prueba la
**recorrida** —del `ExtractedInvoice` del modelo hasta el JSON que ve el cliente— y no una
función. Tres mutaciones verificadas a mano y todas mueren:

| Mutación | Test que muere |
|---|---|
| `capture` no copia el `ieps_amount` | 4 de 6 |
| `ticket_persistence` no lo guarda en la tabla | 2 de 6 |
| `review_ticket` no lo relee al aprobar | el de aprobar |

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

**Las dos carpetas son HERMANAS bajo `~/Documents/Tickets/`** (`medido` al reorganizar):
`Tickets/Tickets_app` es la entrada y `Tickets/Tickets_Scan` la de los ya digitalizados. Esa
hermandad es lo que hace que el archivado funcione **sin configurarlo**:
`archivado_service.carpeta_de_escaneados()` resuelve `entrada.parent / "Tickets_Scan"` cuando la
salida está vacía.

El padre común es para que las dos carpetas se arrastren juntas y no se confundan con otra
carpeta de papel de `Documents`. **Lo que no se puede es anidarlas**: `Tickets_Scan` colgando de
`Tickets_app` hace que el escaneo recursivo vuelva a leer lo archivado cada corrida.
`test_entrada_y_escaneados_son_hermanas` fija esa relación: si alguien cambia el default de la
entrada a una ruta que no termina en `Tickets_app`, el archivado deja de caer donde se espera.

Moviendo las carpetas enteras **no se rompió el ledger**: los 8 archivos siguieron saliendo
`SIN_CAMBIOS` con los mismos totales. Eso es porque `relative_path` es relativo a la entrada, no
absoluto, y cambiar la carpeta de la máquina no lo cambia. Lo que **sí** lo rompería es mover los
archivos *dentro* de la entrada.

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
vacía, y conviene ver eso antes de que ocurra. Devuelve la ruta de destino de cada archivo y si su
lectura quedó pendiente.

**`simular` NO es un borrador en seco, y el repo lo decía mal hasta que un test lo medió.** Este
documento decía "no crea ni mueve nada". La mitad de "no crea" era **falsa**: `simular` apaga el
**archivado del disco**, pero el ticket **se escribe igual**. No hay debate posible sobre cuál de
los dos es el código: la función que borra se llama con `simular` (`_borrar_o_archivar`,
`scan_service.py:1654`), y la que persiste el ticket no lo consulta en ninguna parte.

**La consecuencia es práctica y es la que importa:** hacer una corrida de prueba con `simular`
**deja los tickets en la base**, mezclados con los de verdad, y se reconcilian por igual. Para ver
qué hay en la carpeta sin dejar rastro hay que apuntar a una **empresa de prueba**, o borrar
después. Un campo que se llama "simular" y que sí escribe no es una trampa de la documentación:
es el nombre, y por eso `test_simular_no_es_lo_que_dice_la_documentacion` fija el comportamiento
real. Si algún día se decide que `simular` no escriba nada, ese test es el que tiene que cambiar
con la decisión — no al revés.

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
make insomnia         # regenerar (74 peticiones, 10 carpetas)
make insomnia-check   # sale 1 si está desfasado — para cuando añades una ruta
```

Por qué importa: la versión anterior estaba exportada del **2025-01-21** con 25 peticiones de
las 52 que expone hoy, y —esto es lo que la hacía inútil— **sin un solo header `Authorization`**.
Como `api_router.py` pone el token obligatorio a nivel de router, 51 de 52 rutas respondían 401.
Un archivo que no sabe que hay rutas nuevas no avisa; por eso es generado.

Cuatro cosas que el generador respeta y que no hay que romper al tocarlo:
- **`password` y `contrasena_actual` salen vacías.** El archivo está versionado con remoto en
  GitHub; una contraseña escrita ahí queda publicada. Mismo criterio que `.env` (regla de
  *Reglas que no se rompen*), extendido al cliente de API.
- **`base_url` ya trae `/api/v1` y las rutas del spec también.** Sumar las dos da
  `/api/v1/api/v1/...`, que es un 404 limpio, no un error de sintaxis. Es la misma trampa que
  documenta `api_router.py` vista desde el cliente, y salió al verificar, no al leer.
- **`RUTAS_CON_CONTRASENA` es `(método, ruta)`, no solo la ruta.** `GET /usuarios/{id}` va con
  `UsuarioActual` a propósito —ver una cuenta no compromete a nadie— y ponerle la cabecera
  sería aplicar la regla de más, que es como una defensa se vuelve decorativa. La lista está
  escrita a mano porque el header es un parámetro de **dependencia**, y FastAPI no lo declara
  en la operación: aparece en la cadena de dependencias, que el spec no expone. **Consecuencia
  concreta: anadir una ruta ahí es manual, y si se olvida, la petición sale sin la cabecera.**
- **`usuario_id` no es la cuenta propia.** La última cuenta activa no se puede dar de baja
  (409), así que probar `PATCH /usuarios/{id}` sobre la única cuenta real no probaría nada.
  Hay que crear una cuenta de prueba y pegar su id.

### Medir la exactitud de las fotos
El muestreo del 5% entra **solo** sobre tickets `AUTO_APROBADO`, y un ticket de OCR nunca
llega ahí, así que `GET /tickets/accuracy` no puede decir nada sobre las fotos (medido: las
filas `confidence_source='ocr'` tienen `spot_check_status = NULL`). Para eso está:

```bash
# La verdad se rotula a mano mirando el papel, y vive FUERA del repo.
python3 scripts/medir_precision_ocr.py --init ~/Documents/Tickets/Tickets_app
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

**Y tampoco de los TRIGGERS.** SQLite no tiene triggers de `BEFORE INSERT` con lógica de
negocio, así que `trg_movimientos_no_negativo` —la defensa que impide que el inventario
quede en negativo— es invisible para la suite. Por eso `verify_postgres_inventario.py`
existe, y por eso la comprobación de stock insuficiente está **también** en Python
(`registrar_movimiento`): no como reemplazo del trigger, sino para que el 409 diga
"hay 6 y pediste restar 10" en vez de dejar que reviente el `INSERT`.

**El otro modo de fallo es al revés: una defensa presente y no probada.** El trigger de
stock negativo *funcionaba* y aun así no defendía, porque contaba `AJUSTE` como resta y su
`SUM` ignoraba el signo. Nadie lo notaba porque la única vía que escribía en el kardex era
`confirmar_compra`, y esa produce `ENTRADA` solamente: el `ELSE` del trigger no se
ejecutaba nunca. Documentado en `docs/known-issues.md` §26, con la medición.

```bash
python3 scripts/verify_postgres_capture.py
python3 scripts/verify_postgres_documentos.py
python3 scripts/verify_postgres_auth.py
python3 scripts/verify_postgres_gate.py
python3 scripts/verify_postgres_reconciliation.py
python3 scripts/verify_postgres_spot_check.py
python3 scripts/verify_postgres_inventario.py
```

Desde dentro del contenedor, que es donde está la red de Postgres:

```bash
docker compose exec expense-api python scripts/verify_postgres_inventario.py \
  --base-url "postgresql+asyncpg://postgres:${POSTGRES_PASSWORD}@postgres-reconciler:5432/expense_db"
```

El puerto `5432`, no `5434`: el `5434` es el mapeo al **host**, y desde el contenedor
Postgres se llama por su nombre de servicio. Por eso el script no trae la URL buena por
omisión y el mensaje de error lo dice.

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
    cortocircuita la validación. Y ahora con **dos** sumas posibles: `subtotal + IVA == total`
    y, si el comprobante trae IEPS, `subtotal + IVA + IEPS == total`. Ver *El impuesto es de la
    partida*.

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

20. **El signo del kardex es `SALIDA` resta y nada más.**
    `TipoMovimiento.suma_stock` es la única fuente de verdad; `stock_de()` la consulta y el
    trigger de Postgres la replica en SQL. Antes **no coincidían** —el trigger restaba
    `AJUSTE` y su `SUM` ignoraba el signo— y la consecuencia medida era que una `SALIDA`
    que dejaba el stock en **-1 pasaba el trigger**. Era inerte porque solo
    `confirmar_compra` escribía en el kardex, y esa produce `ENTRADA` solamente.
    Ver *El signo del kardex* arriba y `verify_postgres_inventario.py`.

21. **Una `ENTRADA` del kardex solo viene de `confirmar_compra`, o de un `AJUSTE`.**
    `registrar_movimiento` rechaza con 409 cualquier otra. Sin ese `if` existiría una vía
    que suma stock sin compra, sin línea y sin que nadie mirara el papel, y
    `compras.ticket_id` UNIQUE dejaría de garantizar que el inventario solo refleja
    compras de verdad.

22. **Un `PATCH` usa `exclude_unset`, nunca `exclude_none`.**
    Es la diferencia entre "no lo mandaron" y "lo mandaron en `null`". Con `exclude_none`,
    un `PATCH {"nombre": "x"}` sobre un producto **borraría su precio** y el 200
    llegaría igual: la respuesta dice que se guardó y el dato desapareció. En
    `ProductoUpdate`, `UsuarioUpdate`, `AccountingMappingUpdate`.

23. **Quien firma sale del token, nunca del cuerpo.**
    `confirmada_por`, `producto_asignado_por`, `revisado_por`, `actor` y `cerrado_por`
    salen de `UsuarioActual`. Aceptarlos en el body sería
    una puerta para firmar la entrada al inventario, la conciliación o el cierre de
    **otra** empresa — y `AGENTS.md` ya advierte que no hay multi-tenancy. Los schemas de
    escritura usan `extra="forbid"` para que un cliente que reenvíe la fila entera reciba
    un 422 con el nombre del campo en vez de un 200 que finge haberlo guardado.

24. **Dar de baja ≠ borrar, en `productos` y en `users`.**
    En los dos, el `DELETE` borraría evidencia: `productos` porque
    `movimientos_inventario.producto_id` tiene `ON DELETE CASCADE` (se lleva el historial
    y `stock_de` deja de poder responder por él), `users` porque `is_active` es lo que
    `get_current_user` comprueba **en cada petición** y una baja tiene que surtir efecto
    inmediato sin borrar la fila. La firma (`confirmada_por`, `revisado_por`,
    `cerrado_por`) es texto y no FK por lo mismo: tiene que sobrevivir a la baja.

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

- **`app/modules/expenses/` se borró.** Era código muerto **y** roto: su router no estaba en
  `api_router.py`, `crud.py:136` referenciaba `SourceType.AUTO` (que no existe en el enum) y
  `pipeline.py` usaba `select`/`and_` sin importar. Encima su `batch_upload` aceptaba un
  `folder_path` del cliente y hacía `Path().glob()` — lectura arbitraria de directorios.
  No lo resucites: la lectura por PDF con OCR y visión ya la hace `capture.capture_ticket`,
  que es la ruta montada y la que tiene las mutaciones que dies si algo se quita.
  La lección está en `docs/known-issues.md` §3: **un módulo muerto no se arregla, se borra o
  se declara muerto.** Arreglado, parece montable, y montarlo abre la puerta que por eso
  estaba muerto.

- **`analitica.py` y `hallazgos.py` son servicios, no routers.** No aparecer en
  `api_router.py` es lo correcto: se invocan desde `app/api/dashboard.py:52` y
  `dashboard.py:368`, y ese router sí está montado. Lo que sí conviene verificar es lo
  contrario — que un módulo de `services/` tenga a alguien que lo llame. `VendorNormalizer`
  es el contraejemplo: está instanciado en `ai_client.py:355` y su único caller es el
  `pipeline.py` muerto.

- **No hay multi-tenancy.** `get_current_user` no recibe `company_id` ni hay scoping por empresa:
  cualquier usuario autenticado lee y escribe empresas ajenas pasando un `company_id` arbitrario.
  Es correcto para "una máquina, un contador" y **no** lo es para multiusuario. Por eso
  cada servicio que toca datos de empresa compara el `company_id` **dentro del servicio**
  (`asignar_producto`, `registrar_movimiento`, y el `PATCH` de producto): es la última
  línea antes de que un dato de una acabe en el kardex de otra.

- **`/usuarios` es el primer router que compromete a OTRO usuario, y no hay roles.**
  `PATCH /usuarios/{id}` y `POST /usuarios/{id}/contrasena` existían solo como script de
  CLI hasta ahora. Sin `is_admin` ni scoping, **cualquier cuenta autenticada puede dar de
  baja a cualquier otra** — disponibilidad, no confidencialidad. Lo que sí acota el daño:
  exigen `X-Contrasena-Actual` (`deps.get_current_user_verificado`), y la última cuenta
  activa no se puede dar de baja (409). Lo que **no** se hizo, y sigue abierto: decidir
  *quién puede tocar a quién*. Eso necesita columnas de rol, y una comprobación de rol
  sin el modelo sería fingir que hay control de acceso donde solo hay autenticación.
  Ver `docs/known-issues.md` §28.

- **Cambiar la contraseña pide DOS contraseñas, y son distintas.** El header
  `X-Contrasena-Actual` es la de *quien llama* (prueba que no hay un token robado); el
  `contrasena_actual` del cuerpo es la de *la cuenta que se cambia* (prueba que sabes la
  de esa cuenta). Con una sola, un atacante con un token robado rotaría la clave de
  cualquiera y el dueño ya no podría recuperarla. Y cambiar la contraseña **no invalida los
  tokens**: no hay `jti` ni lista de revocación, así que un token emitido antes sigue
  vivo 8 horas (`SECRET_KEY`, regla 16).

- **`get_current_user_verificado` se declara con `Depends(get_current_user)`, no llamando
  a `_usuario_del_token`.** Los overrides de dependencia reemplazan por *dependencia*, no
  por nombre de función: si se llamara al helper directo, `tests/conftest.py` no podría
  alcanzar estos endpoints sin un token real, y serían los únicos no probables de todo el
  repo. Con `Depends`, el override llega y la comprobación de la contraseña sigue siendo
  real — el test puede decir "el token vale" sin decir "la contraseña vale".

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

- **`db/init.sql` y los modelos divergen, y el test verde no lo ve.** La suite
  construye el esquema **desde los modelos** (`conftest.py`), nunca desde
  `init.sql` — que solo corre una vez, con el volumen vacío. Los dos archivos
  pueden estar "bien" por separado.

  **Encontrado y cerrado:** `tickets.ieps_amount`, `compra_items.iva_linea` y
  `compra_items.ieps_linea` (de la migración `0012`) faltaban en `init.sql`, y
  `ticket_persistence.py:114` escribe `ieps_amount` en cada INSERT: base nueva →
  `column does not exist` → 500 por comprobante, ticket perdido.
  **Ya no puede volver en silencio:** `tests/unit/test_init_sql_espeja_los_modelos.py`.

  > Si añades una columna: **la migración Y el `init.sql`, o nada.** Y si
  > escribes un test que la verifique, verifica la **declaración** (`ADD COLUMN` o
  > la columna dentro de un `CREATE TABLE`), no el nombre. Los tres intentos
  > anteriores fallaron por esto y están escritos en el propio archivo.

- **Siguen abiertas dos divergencias del mismo tipo**: `bank_transactions.company_id`
  es nullable en `init.sql:126` y `NOT NULL` en `models/bank_transaction.py:24`;
  y el índice de `users.email` es funcional (`lower(email)`) en el DDL y de
  columna cruda en el modelo. La del email **es correcta**: el funcional es el que
  vale, porque el login normaliza a minúsculas antes de buscar.

- **Hay dos `.env` y sólo uno se versiona.** `.env.example` es la plantilla (versionada);
  `.env` lleva `POSTGRES_PASSWORD` **y toda la configuración** de la app; `.env.local` lleva la
  `SECRET_KEY` y **gana** sobre `.env`. `config.py` y `docker-compose.yml` leen los dos en ese
  mismo orden: antes `config.py` leía sólo el de configuración y `docker-compose` los dos, así
  que `uvicorn` local firmaba con una clave distinta a la del contenedor ("la sesión se cae,
  pero sólo cuando depuro en local"). Los dos con secretos van en `chmod 600`.

  **Eran cuatro y se fusionaron en dos** (`.env` + `.env.dev` → `.env`). Con cuatro archivos
  sin explicar cuál era cuál, la clave acababa en dos sitios y la contraseña en tres: no por
  descuido, sino porque nadie sabía quién ganaba. `.env.dev` ya no existe.

  Lo único que sigue fuera de `.env` es la `SECRET_KEY`, y por un motivo concreto: con ella se
  firman tokens válidos sin pasar por el login, así que si estuviera en `.env`, compartir la
  configuración de IA significaría compartir la clave.

  **La regla que sobrevive a la fusión, y es más estrecha: la contraseña no se duplica.** La
  `DATABASE_URL` de `.env` va **sin contraseña, aunque `POSTGRES_PASSWORD` esté treinta líneas
  más arriba en el mismo archivo**. La sola línea que arma una URL con contraseña es la de
  `docker-compose.yml`, por interpolación de `${POSTGRES_PASSWORD}`. Dentro de Docker la de
  `.env` no se usa: `environment:` pisa siempre a `env_file:`. Fuera de Docker sí se usa, y ahí
  la rellenas tú. Un test (`TestLaPlantillaDeLosEnv`) lo hace cumplir, porque `.gitignore` y
  `git status` no delatan nada de esto.

  Lo que **ya no** se puede afirmar es "el archivo de configuración no lleva secretos": con la
  fusión, su único secreto (`POSTGRES_PASSWORD`) está ahí a propósito. No reescribas la
  defensa en esa forma; defiéndela como "una sola URL con contraseña".

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
