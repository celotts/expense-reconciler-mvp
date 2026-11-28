# Known Issues — Backlog verificado

Defectos y trampas **confirmados leyendo el código**, con referencia `archivo:línea`.
Este no es un wishlist: cada punto se verificó. Cuando arregles uno, bórralo de aquí
y deja el commit hash en el changelog.

Severidad:
- 🔴 **Bug activo** — algo está roto hoy para el usuario
- 🟠 **Trampa** — funciona pero te va a morder al tocarlo
- 🟡 **Deuda** — gap conocido, no urgente

---

## 🔴 Bugs activos

### 0.e. El PDF del informe imprimía `None` como si fuera un dato — ✅ CORREGIDO (y la mutación lo cazó)

**Nació durante la construcción de la Fase 1 y no lo cazó ningún test hasta que la
mutación sobrevivió.** Es el argumento de por qué el script de mutaciones existe.

**Síntoma:** el informe salía con la fila

```
Motivo    El periodo NO se puede declarar cerrado: None.
```

en un documento que alguien firma y entrega a su cliente.

**Causa medida — dos, y las dos son la misma:** `puede_cerrarse` era un **campo**
(`bool = False`) del schema, separado de `pendientes`. Con cero pendientes,
`pendientes.motivo` es `None` y `puede_cerrarse` es… lo que el constructor puso.
El PDF imprimía la fila con `f"{motivo}"` sin mirar si el motivo existía:

```python
if not reporte.puede_cerrarse:            # el campo, no los pendientes
    fila("Motivo", f"...: {reporte.pendientes.motivo}.")   # → "None"
```

**Por qué un campo aparte era el error, y no el `f`.** Se podría haber arreglado
poniendo `if reporte.pendientes.motivo:` y ya. Se arregló **así y además** dejando
`puede_cerrarse` como `@computed_field` derivado de `pendientes.hay_pendientes`:
dos cubos con la misma verdad son dos que se pueden desincronizar, y ese era todo el
defecto. Un campo derivado no puede mentir sobre sus propias entradas.

**El `f"{motivo}"` sin condición también se corrigió**, por R6: un dato ausente
impreso como texto *es* un dato inventado, y en PDF nadie distingue la palabra `None`
de un valor.

**Lo que costó encontrarlo:** `TestR3LosEstadosDelPeriodo` comprobaba los cuatro
estados del periodo, y los cuatro eran correctos con `pendientes.motivo = None` —
porque `NO_CIERRA` es el estado *correcto* cuando no hay periodo cerrable. El test
afirmaba sobre el estado y no sobre el **texto**, que es lo que el contador lee.

> **La mutación `el PDF imprime el motivo aunque no exista` sobrevivió a la primera
> corrida del verificador.** Sobrevivió porque la clase de tests que la caza
> (`TestElDocumentoNoSeInventaDatos`) **no existía todavía**: se escribió después, al
> preguntar qué estaba mirando el test de R3. Y la primera vez que se escribió, la
> mutación **volvió a sobrevivir** —porque no la añadí a la lista de `objetivos` del
> script.
>
> Eso es la segunda mitad de la lección y no es menos importante: **la lista de
> objetivos de un script de mutaciones es código que se queda viejo en silencio.**
> Añadir un test no hace que el script lo sepa.

### 0.h. El PDF imprime «sin clasificar» en TODAS las categorías — ✅ **CORREGIDO**

El código ya usaba `cat.get("etiqueta")` (ver `reporte_cierre.py:800`). La documentación
estaba desactualizada. La clave `categoria` nunca existió en `analitica.por_categoria`,
que devuelve `{"clave", "etiqueta", "monto", "tickets", "porcentaje", "variable", "sin_clasificar"}`.

**MEDIDO al escribir `front/src/pages/Cierre.tsx`**, leyendo el JSON que devuelve la
API viva y comparándolo con lo que el PDF dibuja.

`reporte_cierre.py:720-721` pinta:

```python
nombre = cat.get("categoria") or "sin clasificar"
```

y `analitica.por_categoria` (`analitica.py:324-335`) devuelve estas claves:

```python
{"clave", "etiqueta", "monto", "tickets", "porcentaje", "variable", "sin_clasificar"}
```

**No hay clave `categoria`.** El `or "sin clasificar"` se come el `None` y el PDF sale
con todas las categorías del periodo bajo un mismo rótulo, aunque `sin_clasificar`
venga en `false` para cada una. La clave que sí lo distingue no se está usando.

El arreglo es una línea —`cat.get("etiqueta") or "sin clasificar"`— y hay que añadirle
el test que hoy no existe: un informe con dos categorías clasificadas tiene que salir
con dos rótulos distintos.

> **El front ya lee la clave correcta (`etiqueta`), así que hoy el preview y el PDF
> discrepan**: el preview muestra «Alimentación» y el PDF «sin clasificar». Es
> justamente el fallo que el objeto compartido y R4 existían para evitar, y aparece
> por el lado del PDF, no del front.

### 0.f. El frente del informe ya existe — ✅ CORREGIDO (y el verificador cazó tres tests que no cazaban)

`front/src/pages/Cierre.tsx`, con la ruta en el sidebar, el enlace desde `Dashboard.tsx`,
el selector de periodo y la descarga del PDF. **22 comprobaciones** en
`front/scripts/verificar_cierre_en_pantalla.tsx`, con **6 mutaciones verificadas a mano**.

#### EL PUNTO QUE NO ES NEGOCIABLE, Y CÓMO SE CUMPLE

El preview y el PDF salen del **mismo objeto**: la pantalla no suma, no calcula
porcentajes y no decide veredictos. Todo lo que se ve sale del JSON de
`GET /reports/cierre-mensual`, formateado por `utils/format.ts`, y los colores del
veredicto se reusan de `utils/spotcheck.ts` (`veredictoMeta`). La traducción vive
en `utils/cierre.ts`, que **no calcula nada del informe**.

Verificado mirando el PDF y el preview en el navegador: el mismo aviso, el mismo
`$0.00`, la misma fecha de referencia, y **ninguno de los dos dice `0%`** donde el
backend devolvió `null`.

#### LO QUE ESTE TRABAJO ENCONTRÓ DE VERDAD

**El PDF lee una clave que no existe.** `reporte_cierre.py:720` imprime
`cat.get("categoria")`, pero `analitica.por_categoria` devuelve `clave`/`etiqueta`
(`analitica.py:324-335`). Como `categoria` no está, el `or "sin clasificar"` deja
**todas las categorías del PDF como "sin clasificar"**. 🟠 **ABIERTO, es lo siguiente**:
el front lee `etiqueta`, que es la que sí viene.

> No lo introdujo este trabajo, pero se hizo visible: el front se escribió contra el
> JSON real de la API y no contra el código del PDF, y por eso se nota.

#### LOS TRES TESTS QUE NO CAZABAN, Y POR QUÉ

La primera versión del verificador comprobaba `lineaExactitud(r).titulo.includes('Exactitud')`:
miraba **el objeto que devuelve la función**, no lo que se pinta. Con la defensa quitada
daba **verde**. Tres de cuatro mutaciones pasaron sin que nadie las notara:

| Mutación | Por qué pasaba |
|---|---|
| `Exactitud: 0.0%` cuando `exactitud === null` | el objeto seguía teniendo "Exactitud" en el título |
| comparativo no concluyente pintado como concluyente | el test miraba el `tono` del objeto, no el texto |
| `periodoActual()` con `toISOString()` | el defecto solo aparece cerca de medianoche; el test corría a otra hora |

**Tres maneras de que un test de pantalla no sirva, y las tres corregidas aquí:**

1. **Mirar el retorno en vez del HTML.** Se renderiza la página entera con
   `renderToStaticMarkup` y se busca el texto. Por eso `Cierre` acepta
   `informeInicial`: `renderToStaticMarkup` no corre `useEffect`, así que sin ese
   parámetro la pantalla se queda en el spinner y no pinta ni una sección.
2. **Un caso que no se ejecuta es un caso sin test.** `exactitud === null` (objeto
   entero ausente) es distinto de `exactitud` presente con sus `exactitud` en `null`,
   y solo el segundo estaba cubierto. La rama que R7 protege **no se ejecutaba nunca**.
3. **Un defecto que depende de la hora no lo caza un test que compara "hoy" contra
   "hoy".** `periodoActual(fecha?)` acepta la fecha por eso: el test pasa el día 1 a
   las 20:30 hora local, que en UTC ya es el día 2. Falla siempre, no un día al mes.

Es el mismo error que el de `test_up_usa_wait` (leía los comentarios del `Makefile` en
vez de la receta): **un test que no ejecuta el código que protege pasa igual que uno
que sí lo ejecuta**.

### 0.g. No hay forma de MARCAR un periodo como cerrado — 🟠 **ABIERTO, por diseño por ahora**

`cierres_periodo` existe y el informe lo lee (`cerrado`, `cerrado_por`,
`estado_periodo`), pero **no hay endpoint que escriba la fila**. El informe dice si el
periodo *puede* cerrarse; cerrarlo sigue sin ser una operación.

Es el criterio D1 de la Fase 4 y es correcto que no exista todavía: cerrar es una
decisión, y la decisión la firma una persona. Lo que hay que evitar es que se lea como
un olvido — por eso está aquí.

### 0.i. El informe de cierre fechaba el periodo con la fecha UTC — ✅ **CORREGIDO** (P0 cerrado)

**Nació en la puerta verde del plan de desarrollo** (`docs/prompts-desarrollo-plus.md`,
Tarea 0.2). El informe tomaba `hoy` de `analitica.utcnow().date()` (UTC) y los tests del
periodo en curso usaban `date.today()` (local). Entre las 18:00 y las 24:00 de México
(UTC-6) el informe se fechaba un día distinto que el que fijan los tests: `TestA2ElTotalCuadra`
(integración) lo ponía en rojo.

**Arreglo:** el router pasa `hoy=date.today()` a `construir_reporte`. El servicio sigue
aceptando `hoy` inyectable por determinismo (R4); solo cambió quién lo fija en producción.

### 0.j. El sufijo legal escrito con puntos matcheaba un comercio real como basura — ✅ **CORREGIDO** (P0 cerrado)

`_tokens_de` (`capture.py`) ahora quita el sufijo legal (`S.A. DE C.V.`, `S.A.B. DE C.V.`,
`S. DE R.L.`, `S.C.`, `S. en C.`, `A.C.`) antes de tokenizar. Sin el strip, un sufijo con
puntos dejaba tokens de una sola letra (`s`, `a`, `c`, `v`) que las reglas de
`_nombre_parece_basura` leían como ruido: `OXXO S.A. DE C.V.` se declaraba "basura" y el
comprobante se perdía, mientras que `OXXO SA DE CV` (sin puntos) sí se aceptaba.

**Arreglo:** strip regex `_RE_SUFICIO_LEGAL` aplicado en `_tokens_de`. Tres casos reales
fijados con tests que mueren si se quita el strip; mutación verificada.

### 0.k. Un CFDI en XML se leia como foto o se perdia — ✅ **CORREGIDO** (P1 sub-paso 1)

**Nacio en el sub-paso 1 del plan de desarrollo** (`docs/prompts-desarrollo-plus.md`, fase
P1 — el CFDI como fuente). Antes de la firma XML, un comprobante en formato digital
(`<?xml ...?>`) no era un formato conocido: con `file_type=image` entraba a visión y en la
carpeta se detectaba como desconocido (`NO_SOPORTADO`).

**Síntoma:** un CFDI subido (o escaneado a un `.xml`) se leía como foto, o no se leía.

**Arreglo:** firma XML (`<?xml`, `<!DOCTYPE`) en `app/core/archivo_real.py` +
rama `xml` en la cascada de `capture` y del diagnóstico. Mutación verificada: sin la
firma, el test `test_xml_por_la_declaracion` muere.

### 0. `DELETE /companies/{id}` devuelve 500 para toda empresa con hijos — ✅ **CORREGIDO**

`app/api/companies.py:delete_company` ahora captura `IntegrityError` y devuelve 409 con un
mensaje que dice qué hacer: "La empresa tiene datos asociados (tickets, productos,
movimientos). Elimínelos primero o dé de baja la empresa."
**No lo introdujo el inventario.** Verificado con `git stash`: en `HEAD`, sin los modelos nuevos,
`db.delete(empresa)` con **un solo ticket** ya falla. Encontrado de paso porque el E2E del
inventario necesitaba limpiar su empresa de prueba.

**Síntoma:** `DELETE /api/v1/companies/{id}` responde `500` con
`NotNullViolationError: null value in column "company_id" of relation "tickets"`.

**Causa medida (con `echo=True` sobre el engine):**

```sql
UPDATE tickets SET company_id=$1::UUID WHERE tickets.id = $2::UUID     -- $1 = NULL
```

La FK declara `ON DELETE CASCADE`, y la cascada de Postgres sí está en el DDL. Pero la **cascada
por omisión de SQLAlchemy en un uno-a-muchos es `save-update, merge`, que NO borra**: desasocia,
y desasociar es un `UPDATE` que pone la FK en `NULL`. La columna es `NOT NULL`, así que el UPDATE
revienta **antes** de que la cascada de la base llegue a correr.

**Por qué `passive_deletes=True` en el muchos-a-uno NO lo arregla.** Se probó y no funciona, y es
el primer intento razonable, así que conviene saber por qué: `passive_deletes` gobierna la
relación **uno-a-muchos** (`CompanyModel.tickets`), no la **muchos-a-uno** (`TicketModel.company`).
Un `backref="tickets"` declarado desde el hijo crea la relación del padre **sin heredar
`passive_deletes`**. Ponerlo en `TicketModel.company` no tiene efecto — verificado, sigue fallando.

**Lo que sí lo arregla** es declarar la relación en `company.py`:

```python
tickets = relationship("TicketModel", back_populates="company", passive_deletes=True)
```

y cambiar `backref` por `back_populates` en el hijo. **No se ha aplicado**, porque toca cinco
modelos que hoy funcionan y es un cambio fuera de lo que pidió la feature de inventario. Manda
alguien que lo mida.

**Amplificado por el inventario:** las cuatro tablas nuevas (`productos`, `compras`,
`compra_items`, `movimientos_inventario`) son hijas de `companies`, así que ahora hay más tablas
en la misma situación. El `DELETE` por SQL directo **sí funciona** —deja que la cascada de
`postgres` corra—, y `scripts/probar_inventario_e2e.py` limpia por SQL por eso, con el motivo
escrito en el propio script.

---

### 0.b. La SALIDA por venta: origen por defecto y valoración — ✅ **DECIDIDO (Fase 4)**

El diseño tenía solo la mitad de la regla: la compra suma stock. `TipoMovimiento` ya traía
`SALIDA`, la constraint `referencia_tipo` aceptaba `'VENTA'` y `POST /inventario/movimientos`
ya escribía la fila —**pero la fila no traía evidencia**. `registrar_movimiento` hacía
`descripcion_origen=(descripcion_origen or producto.nombre)`: si el cliente no escribía nada, el
"origen" de la venta era el **nombre del producto**. Eso no es una fuente: es una etiqueta. Una
venta sin papel parecía tenerlo, y el kardex afirmaba algo que nadie había declarado.

#### Decisión 1: la fuente por defecto es la declaración humana en el punto de venta

`POST /inventario/movimientos` con `tipo=SALIDA`, `es_ajuste=false` es la vía. La evidencia
mínima es **`descripcion_origen` con el folio o número de nota de venta**, más `actor` del token y
`created_at` del servidor. Sin ese texto la petición se rechaza con 409: el sistema no baja stock
por una afirmación sin procedencia.

Por qué no se deriva de otra fuente, con la evidencia que hay en el repo:

| Fuente candidata | Por qué no |
|---|---|
| `tickets` | Son comprobantes de **gasto**. Un ticket no describe una venta. |
| Movimientos bancarios | Registran dinero que salió, no qué piezas salieron ni de qué lote. Un depósito no cuantifica unidades. |
| `compras` / `compra_items` | Solo documentan entradas. Son la fuente de la compra, no de la salida. |
| Un UUID de nota de venta nuevo | No existe tabla de ventas. Inventar un `referencia_id` apuntando a nada sería peor que `NULL`. |

Lo que la declaración **no** afirma: que la venta ocurrió. Afirma quién la declaró, cuándo y bajo
qué folio —la misma naturaleza que un `AJUSTE` de conteo o un cierre de caja—. `referencia_id`
permanece `NULL` hasta que exista un documento de venta digitalizado; cuando exista, se apunta
desde ahí **sin cambiar el esquema**. La evidencia pasa entonces de declarada a contrastada.

#### Decisión 2: valoración por promedio ponderado móvil, derivada del kardex

`costo_unitario` se congela en cada fila de `movimientos_inventario`:

- `ENTRADA` de `confirmar_compra`: `compra_items.costo_unitario` de la línea (si la línea no tiene
  costo, `NULL`, y la entrada se cuenta como **sin costo**, no como costo cero inventado).
- `SALIDA` de venta: promedio ponderado de las entradas del producto hasta ese instante, con el
  mismo signo que `stock_de`. Se guarda en la fila para que una compra posterior **no reescriba**
  el costo de una venta anterior.

El costo de ventas del periodo es `SUM(cantidad * costo_unitario)` sobre las SALIDAS. **No hay
columna de costo de ventas**: es una suma sobre el kardex, igual que el stock es una suma sobre el
kardex. Un campo calculado se desincronizaría con la primera corrección.

FIFO se descarta con una razón de datos, no de preferencia: el kardex no registra de qué compra
salió cada pieza. Asignar lotes sería inventar una procedencia que el papel no dice, y el
`promedio ponderado` es lo único que se puede derivar hoy sin añadir una tabla de lotes. Si algún
día se necesita FIFO, hace falta primero una columna de lote en la SALIDA, escrita por quien
declara la venta.

---

### 0.d. La ruta de escaneados es un path del contenedor si nadie la fija — ✅ CORREGIDO (y pasó)
`TICKETS_SCAN_OUTPUT_DIR` tiene que estar en el `environment:` de `docker-compose.yml`. Sin eso
resuelve "una carpeta hermana de `TICKETS_INPUT_DIR`" y acierta en `/Tickets_Scan`, que es un
directorio **del contenedor** y no el segundo montaje. Los comprobantes se mueven ahí y
desaparecen de la máquina.

**Medido, no supuesto:** 8 comprobantes se movieron a `/Tickets_Scan` y `~/Documents/Tickets/Tickets_Scan`
quedó vacío. Los archivos seguían existiendo y se devolvieron a la entrada con `mv`, sin pérdida de
bytes.

Es la misma trampa que `TICKETS_INPUT_DIR`, y por el mismo motivo: la ruta del host no existe
dentro del contenedor. El arreglo está en `docker-compose.yml`, junto al de `TICKETS_INPUT_DIR`, y
documentado en `.env.example`.

**Y el mismo defaults que no funcionaba fuera de Docker:** la omision ahora es *vacía* = "hermano
de la carpeta de entrada", no `/tickets_scan`. Con un default de contenedor, `uvicorn` directo y los
tests intentan `mkdir` en `/` y macOS responde "Read-only file system" (medido: 24 tests caídos).

---

### 0.c. El OCR no extrae líneas: el inventario por foto no tiene entrada — 🔴 **ABIERTO, es el techo**

La línea se pierde en `ocr.py` / `parser_service.py`: no hay código que arme `items` desde el
texto de Tesseract. Solo el LLM produce `items` (`ai_extractor.ExtractedInvoice.items`), y de los
3 comprobantes escaneados en la base, **2 se leyeron con `read_by = ocr`**.

Consecuencia: un comprobante leído por OCR entra al gasto con su total, y **no abre compra**. Es
el caso normal y no un error —`registrar_compra` devuelve `None` con un `logger.info`—, pero
significa que **la mayor parte de las fotos no genera líneas de inventario.**

**Por qué no se arregla aquí:** es el mismo techo de §21 (33.3% de exactitud), y las líneas son un
problema peor que los totales porque son más números por documento y se multiplican. Arreglarlo
exige un segundo motor de OCR o resolver antes el problema de fondo; agregar un regex de líneas
sobre texto de Tesseract al 33% produciría cantidades plausibles y equivocadas, que es peor que
no producir nada.

**Lo que sí funciona hoy:** PDF con texto (va al LLM, trae líneas) y la compra de esos sí llega a
`EN_REVISION`.

---

### 26. `Decimal` en `tickets.items` = ticket perdido en silencio — ✅ CORREGIDO
**Medido, no supuesto.** Un `ticket_mercado_simple.pdf` con 4 líneas de detalle volvió del escaneo
como `accion=ERROR`, `datos=null`, y sin ticket en la base.

**La causa.** `ai_extractor.py:346-348` convierte a `Decimal` `quantity`, `unit_price`, `total`,
`tax_rate` y `tax_amount` de cada línea —que es lo correcto para dinero—. Pero `tickets.items` es
una columna `JSON`, y `Decimal` no existe en JSON:

```
TypeError: Object of type Decimal is not JSON serializable
[SQL: INSERT INTO tickets (... items ...) VALUES (...)]
```

`procesar_archivo` lo capturaba, hacía un `rollback` y devolvía `ERROR`. El comprobante se perdía y
lo único que quedaba era `scan_files.last_error`, que además se iba con el rollback: la fila del
archivo ni siquiera se creaba, así que tampoco había forma de reintentar desde el registro.

**Por qué el test verde no lo veía — y esto es lo importante.** Solo falla cuando el lector
produce líneas. Un comprobante sin detalle viene con `items=[]`, que serializa bien. El camino del
LLM se podía dar por bueno entero —tests incluidos— mientras no se le pasara una factura con
partidas, que es exactamente el caso de uso del inventario. El fallo no era raro: era invisible
hasta que llegó un caso de uso de verdad.

**El arreglo** es `JSONConDecimal` en `app/core/json_decimal.py`: un `TypeDecorator` sobre la
columna que serializa `Decimal` como **texto**, recursivo, porque las líneas son `list[dict]` y el
`Decimal` vive dos niveles dentro —un recorrido de un solo nivel deja el bug entero, más estrecho y
más difícil de ver.

**Texto y no `float`, a propósito.** `float` "por compatibilidad" se comería centavos en la capa
que decide si una compra cuadra con su total: `0.1 + 0.2` en binario no es `0.3`. Con texto,
`"12.50"` vuelve a `Decimal("12.50")` sin haber pasado por binario, y `interpretar_items` ya
aceptaba strings porque el modelo devuelve `"3"` y `"12.50"` con frecuencia —este fix no le agrega
un caso, lo completa.

No se definió un `json_encoder` global a propósito: habría cambiado el comportamiento de todas las
columnas JSON de la app sin que nadie lo pidiera, y el bug reaparecería en cualquier sitio nuevo sin
que nadie lo estuviera mirando.

**Dos tests que mueren si se quita el arreglo** (`tests/unit/test_scan_datos_json.py`): el INSERT con
líneas, y el round-trip por `interpretar_items` que comprueba que `85.00 + 70.00` sigue dando
`155.00` exacto.

---

### 28. Las fotos no se veían en la UI: dos bugs que making falta uno al otro — ✅ CORREGIDO
**Medido.** 7 de 9 documentos guardados tenían `content_type=NULL` y se servían como
`application/octet-stream`. El navegador **descarga** eso en vez de pintarlo: la foto existía, se
podía bajar, y el revisor veía un recuadro vacío — la misma pantalla que muestra un ticket sin
comprobante, que es justo la confusión que este endpoint existe para evitar.

**Por qué no lo vio nadie antes.** Los 2 documentos que sí tenían tipo habían entrado por una subida
HTTP. El escáner —el otro camino, y el que produce los tickets de foto— no declara nada, así que sus
documentos nacieron con la columna en `NULL`. El camino de la API se veía bien y el del escáner no.

**Bug 2, en el front:** `TicketDocumento.tsx` ponía `<object type="application/pdf">` para **todos**
los documentos. Un JPEG declarado como PDF no se pinta ni con el `Content-Type` correcto. Los dos
bugs juntos son lo que producía el recuadro vacío.

**El arreglo va al SERVIR, no al guardar, y no por gusto.** `ticket_documents` es append-only por
trigger, así que backfillear la columna exige un `UPDATE` que Postgres rechaza:

```
ERROR:  ticket_documents es append-only: un documento no se actualiza, se agrega una version nueva
```

Un backfill habría pedido un `ALTER` o apagar el trigger — saltarse la regla de que el papel no se
altera— y escribir sobre una columna que es un hecho del papel. El tipo que se **sirve** es una
decisión de ahora, no un atributo de lo que se subió en marzo.

**Lo que decide esto es que también arregla las fotos viejas.** Un backfill habría dejado las 7
sirviendo como descarga para siempre, con el bug visible solo en las nuevas. Así se esconden estos
fallos, y por eso el test lee una fila **ya guardada** y le pone la columna en `NULL` a mano.

**El nombre del archivo tampoco decide — con el caso que lo prueba.** `IMG_4253 2.HEIC` está
guardado y por sus bytes es un **JPEG**: la cámara del teléfono lo nominó así. Keyear por extensión
lo habría servido como HEIC. La foto es la misma; lo que falla es la etiqueta.

**La lista cerrada sigue mandando.** `media_type_real()` dice **qué es**; el allowlist de
`content_type_servible()` decide **si se puede pintar**. Un HEIC deduce `image/heic`, cae fuera de
`CONTENT_TYPES_SERVIBLES` y sale como octet-stream, que es lo correcto porque ningún navegador de
escritorio lo pinta. Separar las dos mitades es lo que deja la lista cerrada siendo la única que
manda.

En el front, el tipo sale del `Blob` y una imagen va en `<img>`: `<object>` vuelve a pedir el
recurso **sin `Authorization`** y sale 401.

**Verificado en la API viva**, sobre los documentos que ya estaban en la base y sin tocar ninguno:
el PDF sigue saliendo `application/pdf`, las 7 fotos salen `image/jpeg` (incluida la del `.HEIC`),
y todas con `Content-Disposition: inline`. `tsc` y `vite build` en verde.

**Lo que NO se comprobó: el render en un navegador.** El `Content-Type` correcto y el `<img>` correcto
son la condición necesaria, no la suficiente. Nadie abrió la pantalla de revisión con una foto
mirándola —el navegador no estaba conectado en esta sesión—, así que la prueba de que la imagen se
**pinta** sigue pendiente de una mirada. Lo que sí está probado es que el backend entrega el tipo
bien y que el front ya no declara PDF a un JPEG.

---

### 27. `POST /scan` no devolvía los datos del ticket — ✅ CORREGIDO
`detalles[]` traía `relative_path`, `accion`, `confidence` y `status`, pero **no los datos del
comprobante**. Quien quisiera el JSON tenía que pedir un `GET /tickets/{id}` por cada archivo de la
corrida y pegar los campos a mano.

Ahora cada elemento trae `datos`, con los datos **y el veredicto juntos**, resuelto en una sola
consulta después del bucle (`_resolver_datos_de_tickets`). Tres decisiones que no son de estilo:

- Los datos salen de la **fila de `tickets`**, no de la lectura cruda. Si el gate mandó la lectura
  a revisión, `extraction_status` aparece al lado de los números. Un JSON con los datos sin el
  veredicto deja a quien lo consume creyendo que el sistema respondió por ellos.
- `datos` se resuelve al final y no dentro de `procesar_archivo`, que tiene trece salidas y cuyo
  ticket a veces todavía no existe (DUPLICADO apunta al de otro, OMITIDO por `company_id` no creó
  ninguno, ACTUALIZADO pudo encontrarlo borrado).
- `datos=null` sin ticket, **no** un objeto de campos vacíos: un objeto lleno de `None` parece una
  lectura que no encontró nada, y es otra cosa distinta.

`raw_text` sigue fuera a propósito: hasta 20 000 caracteres por ticket, y es evidencia, no un dato.

---

### 1. `GET /reconciliations/mappings` devolvía 422, no 200 — ✅ CORREGIDO
**Orden inverso de rutas.** `GET /{reconciliation_id}` se declaraba **antes** de `GET /mappings`.
Starlette resuelve en orden de declaración y gana el primer match, así que `/mappings`
llegaba al handler con un placeholder `UUID`, la palabra `"mappings"` no parseaba, y
la respuesta era 422 en vez de la lista de mapeos.

**Alcance real (medido, no supuesto):** solo affecta a rutas literales de **un solo
segmento**. `/export/excel`, `/export/contpaqi`, `/export/generic` y `/mappings/{id}`
funcionaban bien: son dos o más segmentos, y un placeholder de un segmento no compite
con ellos. Solo `GET /mappings` colisionaba. `POST /mappings` tampoco se veía afectado.

Peor: **un test verde lo tapaba.** `test_reconciliations_api.py:421` afirmaba
`len(list_resp.json()) == 1`, y el `{"detail": [...]}` de un 422 también cumple `len == 1`.

**Arreglo:** `GET /{reconciliation_id}` movido al final del router, con un comentario que
explica el porqué. El test ahora exige `status_code == 200` explícito antes de mirar el
cuerpo, y comprueba que el id de la lista sea el esperado. Verificado con mutación: al
revertir el orden, el test muere con `/mappings devolvio 422`.

### 2. El frontend nunca guardaba el comprobante original — ✅ CORREGIDO
La UI hacía `POST /tickets/extract` (**solo preview**, no persiste) y luego `POST /tickets/`,
que es captura **manual** (`tickets.py:110` fuerza `source_type=MANUAL`, `confidence=None`,
sin documento). El `File` se soltaba en el paso 1.

Consecuencia: por la UI los bytes originales **nunca llegaban a `ticket_documents`**. No
daba ningún error visible — la pantalla se veía igual — y el muestreo de exactitud
(`accuracy_service`) comparaba la transcripción del modelo contra sí misma, sin original
contra el cual contrastar.

**Arreglo:** `Tickets.tsx` retiene el `File` en `pendingFile` y, tras crear el ticket,
lo adjunta con `subirDocumento`. Se conserva el paso de revisión: el usuario confirma y
corrige lo que leyó la IA antes de que exista como registro contable. El fallo de la
subida **no** deshace el ticket (el backend usa un SAVEPOINT para justo eso) y se avisa
en ámbar, no en rojo: el ticket sí se guardó.

**Verificado end-to-end contra el stackdocker:** reproducido el bug (0 documentos), y
verificado el fix (`PUT` 200, 2594 bytes, y la descarga vuelve **idéntica** al original).

**Nota:** el ticket creado por esta vía sigue siendo `MANUAL` con `confidence=NULL`, así
que **no entra al muestreo del 5%**. Guardar el documento no es lo mismo que pasar por
el confidence gate. Ver el punto 14.

**Tests:** `tests/unit/test_contract_sync.py::TestElComprobanteOriginalLlegaAGuardado`
(5 tests). Verificado por mutación: quitar el `subirDocumento` los hace fallar.

### 29. `subtotal + IVA == total` rechazaba comprobantes correctos (arreglado)

**Qué pasaba.** El gate comprobaba que `subtotal + IVA == total`, con una tolerancia de un
centimo. Eso asume que un comprobante tiene **una** tasa de impuesto, y en México es falso.

Medido sobre el ticket real de esta máquina (`FC3CB9F8-5AB1-4546-B9A9-016C69A158C5`):

```
SUBTOTAL       217.27
IVA 16.0%        8.14
IEPS 8.0%        8.59
TOTAL          234.00        217.27 + 8.14 = 225.41   <- faltan 8.59
```

El comprobante era **correcto** y el gate lo mandaba a revisión. Con la tolerancia de un
centimo, no había forma de pasarlo aunque los tres números se leyeran perfecto.

**Por qué es peor que un check flojo.** `subtotal_plus_tax_mismatch` dejó de significar
"leíste mal" y pasó a significar "el modelo no tiene un campo para lo que dice el papel". Con
esa señal corrupta, el resto de los checks dejan de ser creíbles: si el único check aritmético
avisa por un motivo que no es un error de lectura, ¿cuál de los otros sí lo es?

**El error de fondo: el IVA no es una tasa del subtotal.** En ese ticket el IVA es 8.14 sobre
217.27, o sea **3.7%**, no 16%. Solo una parte de las partidas está a 16% y el resto a 0%: el
papel lo marca con una letra al final de cada línea (`T`, `C`, `A`). El IEPS de 8.59 es **4.0%**
del subtotal, tampoco 8%.

**El camino que se probó y se descartó.** Un heurístico: "si `subtotal + IVA` no da el total,
busca una tasa fiscal conocida que explique la diferencia". Se descartó porque es falso — la
diferencia no es una tasa del subtotal sino la **suma de impuestos de líneas con tasas
distintas**. En la práctica marcaba como "un impuesto raro" a cualquier total mal leído que se
desviara alrededor del 4%, que es justo el caso que tiene que seguir fallando: la foto de DSW,
donde el OCR leyó el precio ya descontado (`119.97` en vez de `279.93`).

Un clasificador que hace pasar errores de lectura es peor que no clasificar.

**Arreglo.** El dato exacto: `tickets.ieps_amount` y `compra_items.iva_linea` /
`ieps_linea` (migración `0012`). El gate prueba `subtotal + IVA + IEPS == total` cuando hay
IEPS, y sigue exigiendo que cuadre — no se relajó nada.

**Lo que salió en el camino, y es lo importante:**

1. **`tax_amount` es el IVA, y el prompt pedía "total de impuestos".** `export_service` manda
   `tax_amount` bajo el encabezado **"IVA"** del archivo que ve el contador. Un comprobante con
   IVA 8.14 e IEPS 8.59 llegaba al contador como **IVA 16.73**, sin marca de error. No era un
   dato inventado como `TipoCambio: 1.0000`: era un dato real con la etiqueta equivocada, que
   es peor porque no se ve.
2. **El `Subtotal` del Excel era `total - IVA`.** Con IEPS daba 225.86 en vez de 217.27, y la
   cuenta del archivo **cuadraba** (225.86 + 8.14 = 234.00), así que el error quedaba
   invisible. Ahora sale `tickets.subtotal`, y si no hay subtotal leído se deriva solo cuando
   se sabe que no hay IEPS; con IEPS sin subtotal la celda queda vacía.
3. **`TicketReviewRequest` no aceptaba `subtotal` ni `ieps_amount`.** Aprobar desde la cola de
   revisión era imposible para un ticket con IVA+IEPS, con un `422` que no decía qué número
   faltaba.

**Por qué el campo costó tres intentos.** `TicketExtractionResult` **no está en
`app/schemas/ticket.py`**: está en `parser_service.py:427`. El campo se agregó dos veces a
`TicketResponse` y ninguna a la clase que el escaner consume, y el error apareció como
`AttributeError` en `scan_service`, tres capas más abajo. Un test por capa habría pasado en
los tres casos — que es exactamente lo que ya había pasado con `items` y con el `Decimal` de
`items`.

**Verificado por mutación** (`tests/integration/test_ieps_extremo_a_extremo.py`, 6 tests):

| Mutación | Resultado |
|---|---|
| `capture` no copia el `ieps_amount` | mueren 4 de 6 |
| `ticket_persistence` no lo guarda en la fila | mueren 2 de 6 |
| `review_ticket` no lo relee al aprobar | muere el de aprobar |

**Lo que NO se arregló, y sigue pendiente:** tu ticket real tiene `IVA 0.00` — el OCR no leyó
mal el IVA, lo leyó **como cero**. El descuadro son 16.73 (IVA 8.14 + IEPS 8.59), así que el
campo `ieps_amount` solo no lo resuelve: necesita corrección humana mirando el papel.

---

---

## 🟠 Trampas

### 3. `app/modules/expenses/` — muerto **y** roto. — ✅ BORRADO (no lo montes)

**Se borró entero.** No era una decisión de limpieza: era código que no tenía a nadie detrás y
que, si se montaba, abría una puerta. Lo que tenía, medido:

- Su router **no estaba** en `api_router.py` → los 6 endpoints no existían.
- `crud.py:136` referenciaba `SourceType.AUTO`, que **no existe** en el enum.
- `pipeline.py:224-229` usaba `select`/`and_` sin importar.
- `batch_upload` (`router.py:41-45`) aceptaba un `folder_path: str` **del cliente** y hacía
  `Path(folder_path).glob()` → lectura arbitraria de directorios del servidor. Ninguno de sus
  6 endpoints pedía `get_current_user`.

**Lo que costaba dejarlo quieto:** 1 110 líneas y **14 errores de tipo** que nadie iba a
arreglar, porque no había a quién le importar. Un `mypy` del repo los reportaba todos y
nadie los leía, que es exactamente cómo se normaliza un linter que no sirve para nada.

**Lo que reemplaza su salida por el miedo correcto:** el camino de lectura por PDF con visión
y OCR ya está cubierto, con tests y con mutaciones que mueren si algo se quita
(`capture.py`, `verify_capture_mutations.py`). Ese camino —OCR por páginas del PDF, `vision`
como último escalón, y el respaldo del comprobante guardado con su hash— era el que
`pipeline.py` intentaba resolver y que hoy resuelve `capture.py`, que es el que está
montado. Si alguien necesita esa lectura, no hay que resucitar `pipeline.py`: hay que llamar
a `capture_ticket`.

Lo que queda de esta entrada es la regla: **un módulo muerto no se arregla, se borra o se
dice que existe.** Un módulo que parece arreglado invita a montarlo, y montarlo abre la
lectura arbitraria de directorios.

### 4. Scoping por empresa — 🟡 PARCIALMENTE CERRADO (era BOLA/IDOR abierto)

`get_current_user` sigue sin recibir `company_id`: la pertenencia se comprueba en la BD
en cada petición, no en el token. Antes, con un token válido,
`GET /tickets?company_id=<ajena>` leía y `PATCH`/`DELETE` escribían empresas ajenas.
Ya no:

- **La guardia global** (`guardia_de_permisos` en `app/core/deps.py`) compara el
  `company_id` declarado en query, JSON o form con la empresa activa. Decir otra
  empresa da **409**, y una cabecera inválida da **400** antes de gastar scrypt.
- **Los recursos se buscan dentro de la empresa** en `tickets` (`_ticket_en_empresa`),
  `bank_transactions` (`_movimiento_en_empresa`) y `reconciliations`
  (`_conciliacion_en_empresa`, `_mapeo_en_empresa`). Un id ajeno da **404**, no 403:
  responder 403 confirmaría que el id existe. Las listas, colas y el reporte de
  exactitud también filtran por la empresa activa; los conteos del tablero no
  prestan evidencia de otro dueño.
- Verificado con `tests/integration/test_alcance_por_empresa.py` (9 pruebas: 404
  ajeno, 200 propio, listas separadas, lote mixto) y
  `scripts/verify_alcance_por_empresa_mutations.py` (14 mutaciones, las 14 mueren).

**Lo que queda, y no es cosmético:** las cuentas **sin membresías** devuelven
`empresa_visible=None` y siguen sin filtrar, por compatibilidad con las bases
anteriores a `0022` (es el backfill, no un permiso nuevo). Los routers que no
piden `EmpresaVisible` —inventario, reportes, exportaciones— se apoyan en la
guardia global en vez de en una búsqueda por recurso: el `company_id` declarado
no puede ser ajeno, pero un id de producto o de periodo se resuelve fuera de esa
comparación. Para un despliegue multiusuario real hace falta cerrar esos dos
puntos, no relajar la guardia.

### 5. El export CONTPAQI inventaba datos — ✅ CORREGIDO (ver §18)

`export_service.py:355-383` ya no escribe literales en las ocho columnas que no
tienen fuente: `Serie`, `Folio`, `Moneda`, `TipoCambio`, `MetodoPago`,
`TipoComprobante`, `UsoCFDI` y `Cuenta` salen vacías. Antes un comprobante en USD
llegaba con `TipoCambio=1.0000` sin marca de error, y el contador lo subía a
CONTPAQI creyéndolo. La celda vacía dice «no lo sé»; el literal decía «lo sé» y
mentía. Cuando el contador quiere esos valores, el camino es `AccountingMapping`,
donde sí son una decisión suya.

**Sigue abierta la otra mitad:** los campos que el modelo sí lee y se tiran
(`invoice_number`, `invoice_series`, `currency`, `exchange_rate`,
`payment_method`, `tax_breakdown`, `items`, etc.). Ya no se inventa un tipo de
cambio para ocupar su lugar, pero el dato leído sigue sin llegar al archivo.

### 6. HEIC no soportado — ✅ CORREGIDO (el front sigue sin aceptarlo)
`pillow-heif` ya está en `requirements.txt` y el opener registrado en
`ai_extractor.py`, así que la foto de un iPhone entra y se decodifica. La wheel trae
la librería nativa: no hace falta `apt-get`, y verificado en aarch64.

Lo grave no era que no funcionara, sino **cómo fallaba**: el decode estaba en un
`except: pass` (`ai_extractor.py:140-151`), así que los bytes crudos de un HEIC iban
a `base64` **declarados `image/jpeg`** a un modelo que no los descodifica. Basura
que podía volver con apariencia de lectura.

Ahora `_a_jpeg()` devuelve `None` y sale `IMAGEN_ILEGIBLE`, que está en
`FALLOS_DEL_MODELO` (`capture.py:81`): el ticket va a la cola **con el motivo real**
en vez de con un `Unknown Provider` de pantalla. Sigue faltando que el front acepte
`.heic` en su `accept`.

### 7. PDF cifrado se pierde como "ilegible"
`pdfplumber` y `pypdfium2` fallan sin contraseña → `capture.py:381` devuelve
"PDF ilegible y no renderizable", **sin decir que el problema es la contraseña**. El usuario
no sabe qué hacer. `raw_text` y `validation_errors` tampoco lo mencionan.

### 8. Un archivo por subida; sin carga masiva — ✅ CORREGIDO (ver §45)

`POST /api/v1/expenses/upload-batch` acepta `files: list[UploadFile]` y
`POST /api/v1/expenses/digitalizar-directorio` lee una carpeta del servidor
(`app/routers/expenses.py:107` y `:176`). Los dos son adaptadores sobre
`procesar_archivo`: no hay segundo lector. Ambos deduplican por SHA-256 y
devuelven datos y veredicto juntos. `SourceType.BULK` distingue la carga
explícita del escaneo de carpeta; `CAMERA` sigue sin endpoint propio, y no lo
necesita: la cámara sube por `upload-batch`.

La afirmación vieja («tres endpoints con `UploadFile` en singular») describía un
router muerto; el vivo ya tiene las dos puertas, sus pruebas de integración y el
verificador del front (`npm run verify:lote`).

### 9. `init.sql` y los modelos divergen (el test verde no lo ve) — 🟡 PARCIALMENTE CERRADO

**La clase de fallo —columna presente en un sitio y ausente en el otro— está
cerrada.** Las divergencias de tipo que quedan son estas:

- `bank_transactions.company_id`: nullable en `init.sql:126`, `NOT NULL` en
  `models/bank_transaction.py:24`. **✅ CERRADA.** Se añadió `NOT NULL` al DDL.
- `accounting_mappings.company_id`: nullable en `init.sql:163`, `NOT NULL` en
  `models/accounting_mapping.py:15`. **Sigue abierta.** El test de espejo solo
  comprueba que la columna exista, no su nulabilidad; para cerrarla hace falta
  una migración, no un cambio silencioso en el DDL de bases nuevas.
- `users.email`: el modelo pide `unique+index` sobre la columna cruda
  (`models/user.py:26`); prod usa índice **funcional** `lower(email)` (`init.sql:75`).
  **Sigue abierta, y es correcta**: el índice funcional es el que vale, porque el
  login normaliza a minúsculas antes de buscar.

**Lo que se encontró y se cerró:** `tickets.ieps_amount`, `compra_items.iva_linea`
y `compra_items.ieps_linea` **no estaban en `init.sql`**, aunque los modelos las
declaran y `ticket_persistence.py:114` escribe `ieps_amount` en cada INSERT. Una
base creada desde cero —la única forma de que corra `init.sql`— reventaba con
`column "ieps_amount" of relation "tickets" does not exist`, o sea un 500 por
comprobante y el ticket perdido. Las tres son de la migración `0012`, replicadas
ahora. Verificado comparando los 13 modelos contra el DDL.

**Y ya no puede volver a pasar en silencio**, con `tests/unit/test_init_sql_espeja_los_modelos.py`
(20 tests): recorre las columnas de cada modelo y comprueba que el DDL las
**declare**. No verifica tipos ni nullability —para eso están los
`verify_postgres_*.py`— sino existencia, que es lo que se rompió.

**Los tests NO lo veían, y por eso el defecto era invisible:** la suite construye
el esquema desde los modelos (`conftest.py`), nunca desde `db/init.sql`. Ese
archivo solo corre una vez, con el volumen vacío. Los dos archivos pueden estar
"bien" por separado y el test sigue verde.

> **Y el test que lo cubre tuvo dos bugs antes de valer.** Ambos están escritos en
> el propio archivo, porque son la parte que nadie adivina:
> 1. Buscaba el nombre de la columna en el archivo entero. Los **comentarios** de
>    `init.sql` mencionan `ieps_amount` larguísimo, así que daba verde con las
>    columnas borradas. Se grepa sobre texto, no sobre comportamiento.
> 2. Al quitar los comentarios, quitaba los cuerpos `$$...$$` **antes** que los
>    comentarios, y hay un `$4,093.80` dentro de un comentario que desalineaba el
>    emparejamiento: el archivo se encogía de 48 KB a 5 KB y `CREATE TABLE
>    compra_items` desaparecía. 12 tests en rojo con el DDL íntegro.
> 3. Y aun así daba verde: un `COMMENT ON COLUMN tickets.ieps_amount IS` dos
>    líneas más abajo **menciona** la columna sin **crearla**.
>
> Tres versiones del test, y la tercera es la que sirve. Un test que no sabe
> distinguir "esta columna no existe" de "no sé dónde está" es peor que ninguno:
> alguien lee "todo en verde" y concluye que el DDL está bien.

### 10. `REVIEW_CONFIDENCE = 0.60` no hace nada
`confidence_gate.py:198-203`: las dos ramas (`>= 0.60` y el `else`) producen
`REQUIERE_REVISION`; solo cambia la etiqueta (`confidence_medium` vs `confidence_low`).
Si alguien lo sube esperando un cambio de comportamiento, no lo habrá. Cosmético hoy,
confuso mañana.

### 11. `text` no existe en el enum `SourceType`
`file_type=text` funciona en la cascada (`capture.py:292`) pero al persistir cae al fallback
y se guarda como `source_type="image"` (`tests/integration/test_capture_pipeline.py:102-116`).

### 34. El parser leía el precio ya descontado como total — ✅ CORREGIDO

**Medido con `scripts/medir_precision_ocr.py` sobre los 5 comprobantes rotulados a
mano, corriendo el OCR de verdad:**

| Campo | antes | después |
|---|---|---|
| **`total_amount`** | 60% (3 de 5) | **80%** (4 de 5) |
| **`subtotal`** | 20% (1 de 5) | **60%** (3 de 5) |
| `tax_amount` | 60% | 60% (sin cambio) |

> **Y un hallazgo de método que importa más que los números: el OCR NO es
> determinista entre corridas.** La misma foto de DSW dio `SUBTOTAL 279.33` en la
> corrida cuyo `raw_text` quedó guardado en `tickets`, y `SUBTOTAL 279.93` en una
> corrida nueva. Medir el parser sobre el `raw_text` guardado mezcla el estado del
> código con el de una lectura vieja, y produce cifras que **no reproducen**: por
> eso la primera medición de este arreglo dio "60%, sin cambio" y la correcta da
> 80%.
>
> **Mide siempre con el script, que relee las fotos.** Es el mismo error que medir
> el parser contra su propia salida.

#### El defecto 1: la línea de ahorro se leía como el total

El papel de DSW (`IMG_4253 2.HEIC`) imprime, textual:

```
Usted ahorra $119.97 de $399.90
SUBTOTAL 279.93
Usted ahorra en Total: $119.97
```

El total real es **279.93**. El sistema leía **119.97**, que es el precio ya
descontado, y de ahí salía un `subtotal_plus_tax_mismatch` sobre dos números que
no eran los del comprobante: **el gate se paraba por una razón que no era la
real**. Hizo bien en parar; paró por lo equivocado.

Sin el filtro, ese texto da `total 119.97`. Con el filtro, da **`279.93`** — el
valor correcto del papel, y el que lo sube al 80%.

**El filtro está en DOS sitios y en el primero no bastaba.** El `continue` en el
bucle principal quita la línea de ahorro de ahí, pero el **fallback del final
reescanea `lines` completas** buscando `total|importe`. Con el filtro solo en el
bucle, DSW seguía leyendo `119.97` por esa puerta y el arreglo no hacía nada.
Medido: fue exactamente lo que pasó en la primera implementación, y por eso hay
un test que prueba **solo** el fallback, sin el bucle alrededor.

#### El defecto 2: `SUBTOTAL` con puntuación pegada

La etiqueta exigía `^` o whitespace antes, y el OCR pone ruido de caja delante:

```
“SUBTOTAL 108.31     <- Bodega Aurrera, comilla tipográfica   NO SE LEÍA
«SUBTOTAL 217.27     <- angle bracket                          NO SE LEÍA
; SUBTOTAL 964.00    <- este sí entraba
```

En los dos primeros el importe **estaba a la vista**. Es el arreglo que sube el
subtotal de 20% a 60%: el de Aurrera pasa de `None` a `108.31`, que es el valor
correcto del papel.

El borde pasó de `(?:^|\s)` a `(?:^|[^\w])`. **No se relaja más**: con
`r"sub..."` a secas, `XSUBTOTAL 108.31` entra como subtotal, que es inventar un
número. Esa mutación está en `verify_capture_mutations.py` y en el test.

#### Lo que estos arreglos NO arreglan, y conviene no prometer

- **`provider_name` sigue en 0%.** El nombre real del emisor está al **final** del
  texto, junto al RFC, y el parser toma las primeras 15 líneas —donde el OCR pone
  ruido de caja (`Pog ae — TAPA`, `AAA AI`, `GRUPO COMERCIAL DS!`)—. Es la pieza
  de mayor ganancia pendiente y **no es un arreglo de una línea**: cambia el campo
  que el gate exige para aprobar, así que necesita su propia medición.
- **Mercastar sigue en `0.00`.** El OCR funde la etiqueta (`ARTIC ATOTAL: 5000` en
  vez de `ARTIC. TOTAL: 48.00`) y §21 documenta por qué relajar ese patrón **no
  se adoptó**: leería `5000`, que es 100× el importe real, en una fila que
  parecería buena.
- **El IVA nunca se lee** (`FINA`, `TVA`, `TEPS` en vez de `IVA`) y **el RFC
  tampoco** (el OCR pierde las tres letras del RFC del emisor, §21). No hay
  arreglo de parser para ninguna de las dos.

Verificado con `tests/unit/test_parser_ahorro_y_bordes.py` (20 tests, 4 mutaciones
comprobadas a mano: filtro fuera del bucle · filtro fuera del fallback · borde
vuelto a whitespace · borde relajado a cualquier carácter) y con
`verify_capture_mutations.py`, que subió de 26 a 28 mutaciones.

### 37. Los tres pasos del gasto eran tres pantallas sueltas sin orden — ✅ CORREGIDO

**El hueco:** digitalizar, registrar y conciliar son tres trabajos que van en
orden, y en el menú lateral no había ninguna pista de cuál seguía. Quien
digitaliza 40 comprobantes y cierra la laptop vuelve con 40 tickets `PENDIENTE` y
ninguna señal de que el próximo paso es **revisarlos**, no volver a escanear.

`front/src/components/FlujoGuiado.tsx`, en el dashboard.

#### Los tres pasos, y por qué el orden no es decorativo

| Paso | Pantalla | Qué muestra |
|---|---|---|
| 1. Digitalizar | `/scan` | comprobantes leídos |
| 2. Registrar | `/review` | pendientes de confirmar, y cuántos ya están afirmados |
| 3. Conciliar | `/reconciliations` | sin cruzar, y cuántos cruzados |

El paso 3 puede estar bloqueado con el paso 2 incompleto, y **eso es correcto**:
un ticket `PENDIENTE` no se concilia ni se exporta (`SETTLED_STATUSES`).

#### Los números salen del dashboard, no de una llamada nueva

`DashboardResponse` ya traía `por_estado`, `cola_revision` y
`por_estado_conciliacion`. **No se agregó ningún endpoint**: dos consultas que
cuentan lo mismo son dos verdades, y este proyecto no las sostiene.

#### EL BUG QUE SOLO SE VIO MIRANDO LA PANTALLA

La primera versión pintaba **✓ verde en "Conciliar"** con 0 cruzados y 7
comprobantes sin confirmar. Dos cosas lo causaban:

**1. El orden de la condición.** Era:

```ts
if (!registradas && paso.pendientes > 0) return 'bloqueado';
return paso.pendientes === 0 ? 'hecho' : 'en_curso';
```

Con `pendientes === 0` la primera línea no disparaba y la segunda devolvía
`hecho`. El contador propio del paso no puede decidir si el paso **está hecho**:
lo que decide es si hay **algo sin confirmar antes**. Corregido a bloquear
primero:

```ts
if (!registradas) return 'bloqueado';
return paso.pendientes === 0 ? 'hecho' : 'en_curso';
```

**2. El test de texto no lo veía.** Buscaba la palomita en
`html.slice(html.indexOf('Conciliar'))` — y **en el HTML la palomita va antes de
la palabra**, así que el recorte empezaba después y el test era verde con la
pantalla mintiendo. El criterio correcto es contar sobre todo el HTML: con algo
sin confirmar hay **exactamente una** palomita, no tres.

> **Un test que busca una cadena a partir de otra puede estar midiendo el
> recorte y no el componente.** Cuando el orden del markup importa, contar sobre
> todo es lo que se necesita. Esto es lo mismo que ya pasó con el panel de
> progreso (§31): el texto que sale hay que mirarlo, y mirarlo en la pantalla
> encuentra cosas que el string no.

**3. La numeración duplicada.** El `<ol>` pintaba sus propios "1. 2. 3." al
margen y el badge de cada paso repetía el número: se leían seis pasos. `list-none`
lo arregla. **Solo se notó en la captura** — en el texto plano los números se
mezclan y no se distingue uno de otro.

#### Verificación

`front/scripts/verificar_flujo_guiado.tsx`: **26 comprobaciones** sobre el render
con los datos reales de la máquina, más el cálculo de estado aislado (sin React,
para que un fallo diga si fue la comparación o el render). Mutación verificada:
reintroducir la condición vieja **mata 2 tests** (la palomita y el estado).

Y verificado **mirando la pantalla renderizada**: los tres pasos en tres columnas,
`Digitalizar` en verde con ✓, `Registrar` en azul con 6 por confirmar, `Conciliar`
en gris **sin palomita**, y la nota que explica que la cola no es un error.

### 36. No había forma de dejar la base limpia para volver a probar el escaneo — ✅ CORREGIDO

**El hueco era real y está documentado en el propio código:** `ScanRequest.simular`
decía *"para no dejar rastro hay que apuntar a otra empresa de prueba, o borrar
después"* — y **"borrar después" no existía como herramienta**. La primera limpieza
de la base de este trabajo se hizo a mano con `psql`.

`POST /api/v1/scan/reset`, con `app/services/scan_reset.py`.

#### Los cuatro cerrojos, y por qué están en ese orden

| # | Cerrojo | Qué pasa sin él |
|---|---|---|
| 1 | `TICKETS_SCAN_RESET_HABILITADO`, **apagado por omisión** → **404** | Cualquier despliegue del código tiene un endpoint que borra la base |
| 2 | `UsuarioVerificado` (token + `X-Contrasena-Actual`) → **401** | Un token robado borra el histórico contable |
| 3 | La frase `BORRAR_YA` exacta → **422** | Un `true` de más borra |
| 4 | Trabajo humano presente → **409** | Se borra la corrección de alguien que miró un papel |

El **4** es el que protege lo irrepetible: un ticket con `reviewed_at` o una compra
`PROCESADO` bloquean el borrado. Todo lo demás —una lectura automática— se puede
volver a hacer; eso no.

#### EL ORDEN DE LOS CERROJOS NO ES EL DEL CÓDIGO, Y POR ESO EL FLAG ES UNA DEPENDENCIA

La primera versión tenía el flag como `if not settings...: raise 404` al principio
del handler, y el test lo cazó: **con el flag apagado devolvía 401**, no 404.

FastAPI evalúa las **dependencias antes** que el cuerpo del handler, así que
`UsuarioVerificado` ya había comprobado token y contraseña cuando se llegaba al
primer `if`. El `if` del cuerpo era el **tercer** cerrojo en ejecutarse, aunque en el
código estuviera el primero.

Eso no es cosmético: **un 401 confirma que la ruta existe**, y para un endpoint que
borra la base, confirmar que hay una puerta de borrado es parte del daño. Por eso
el flag va en `_reset_permitido`, que encadena flag → contraseña dentro de una
misma dependencia, donde FastAPI **sí** garantiza el orden.

Poner el parámetro del flag primero **no** lo arregla: FastAPI no garantiza el
orden entre un `Annotated` y un `Depends` suelto. Eso se comprobó.

#### El orden de los `DELETE` depende de un trigger, y está medido

`tickets` va antes que `ticket_documents` porque este es **append-only por
trigger** (`0009_documento_inmutable.sql`): el trigger rechaza borrar el documento
*mientras su ticket exista*.

Medido contra Postgres real, no inferido del DDL:

- **Orden malo** (papel primero): `ERROR: ticket_documents es append-only: no se
  borra un documento mientras su ticket exista. Ticket 38a1c061-...` — y
  `ROLLBACK`, con los 7 tickets intactos. **Pasó de verdad** en la primera limpieza.
- **Orden bueno** (ticket primero, papel en cascada): borra los 7.

También se aisló el mecanismo con una tabla de dos niveles y el mismo trigger: el
orden malo da `append-only`, el bueno deja 0 filas.

**Y el trigger tiene que seguir en pie después.** Un endpoint que lo deshabilitara
haría que su borrado pasara y habría quitado la regla 18 sin que nada lo notara.

> **Este orden NO se puede probar en `unit`, y es importante saber por qué.**
> SQLite no tiene triggers y `conftest.py` construye el esquema desde los modelos,
> así que sobre SQLite el trigger no existe y el orden invertido pasaría sin error.
> Un test en `unit` probaría que Python ejecuta dos `DELETE` en secuencia —que es
> lo que hace cualquier código—. Se intentó el test de integración con las
> migraciones aplicadas, y es un avispero que hoy no justifica: las migraciones
> asumen columnas que `create_all` no crea, y asyncpg tokeniza los `\` de
> continuación de línea antes de quitar los comentarios. La evidencia del orden está
> arriba, medida contra la base viva.

#### Verificación

`tests/unit/test_scan_reset.py`: **29 tests, 5 mutaciones comprobadas a mano** — el
flag se ignora · la frase ya no importa · el cerrojo del trabajo humano desaparece ·
se borran también `users` y `companies` · el preview también borra. Las cinco
mueren.

La mayoría son pruebas **negativas** a propósito: un endpoint destructivo se
verifica por lo que *no* deja pasar.

### 35. `verify_capture_mutations.py` contaba el subtotal como no verificado — ✅ CORREGIDO

El script mutaba `r"(?:^|\s)sub[\s-]?total..."`, y al cambiar el borde a
`r"(?:^|[^\w])sub[\s-]?total..."` el patrón se quedó obsoleto. **El script lo
reporta y sale con 1**, pero es el aviso que `AGENTS.md` describe: *"si refactorizas
código que estos scripts mutan, actualiza la ruta. Si no, el script cuenta la
defensa como no verificada, y lo hace en silencio."*

Reapuntado, y de paso se **añadieron dos mutaciones** que el arreglo de abajo
dejó sin cubrir: el borde del `total` (que cambió en el mismo commit por el mismo
motivo) y el filtro de ahorro. Sin ellas, esos dos arreglos estaban verificados
solo por tests unitarios y el script los contaba como cubiertos.

Queda **1 patrón obsoleto, preexistente**: *"un documento ilegible se pierde en
vez de ir a la cola"*. Comprobado con `git stash -u`: revienta igual sin los
cambios de este trabajo.

### 31. El feed del escaneo no pintaba los datos del ticket — ✅ CORREGIDO

**Medido, sobre los 7 tickets reales de la base.** El feed en vivo
(`GET /scan/runs/{id}` → `procesados[]`) llevaba **cinco campos**: ruta, importe,
estado, motor y acción. El panel mostraba:

```
[revisar]  IMG_4222.jpeg                                   $0.00
```

**Y eso no distingue dos situaciones que piden acciones opuestas:**

| Lo que se veía | Lo que pasó | Qué hay que hacer |
|---|---|---|
| `$0.00` + `total_not_positive` | el sistema **no supo leer** el papel | volver a la foto |
| `$119.97` + `subtotal_plus_tax_mismatch` | el sistema **sí leyó**, pero el gate vio que no cuadra | comparar dos números |

Con solo el total, las dos se veían como `$X, pendiente`. El operador no sabía cuál
era, y por lo tanto no sabía qué hacer. No faltaba un dato bonito: faltaba el
dato que convierte una fila en una tarea.

**Lo que se agregó al feed**, leído de la **fila** de `tickets` y no de la
lectura cruda — la misma distinción que separa `ScanItemResponse.datos`: si el
gate mandó la lectura a revisión, aquí se ven los números **que produjo la
lectura** junto al veredicto que los rechazó:

`proveedor` · `rfc` · `fecha` · `subtotal` · `iva` · `ieps` · `lineas` (conteo) ·
`validation_errors`

**Las líneas NO se copian, solo se cuentan.** `MAX_PROCESADOS` es 60 y un ticket
de supermercado trae 28 partidas: copiar `items` entero son 1 700 objetos en
memoria para pintar un número. Quien quiera las líneas las pide en el ticket.

**`lineas: null` y no `0` cuando no hay.** La ruta OCR nunca trae detalle de
partidas —`tickets.items` queda a `NULL` y `registrar_compra` devuelve `None` sin
error, que es el caso normal—, y `0` afirmaría que el papel no trae partidas.
Con OCR al 60% eso sería una afirmación falsa.

**Por qué no es una segunda verdad que se desincroniza.** Son los mismos campos de
la misma fila que ya se leían para pintar `monto` y `extraction_status`. No hay una
segunda lectura ni un segundo criterio; lo que cambió es que ahora se copian todos
en vez de dos. Si se desincroniza, se desincroniza en los campos que ya lo hacían.

**El `date` casi revienta la respuesta, y donde falla no se ve.** `a_dict()`
serializa con `json.dumps`, y un `date` crudo lanza `TypeError` ahí — en el
**serializado**, que es donde parece un fallo de red y no del feed. `_json_safe`
lo cubre junto con los `Decimal`.

Verificado con `tests/unit/test_feed_del_ticket.py` (13 tests, 5 mutaciones
comprobadas a mano: el caller deja de pasar el encabezado · el registro deja de
guardarlo · `_json_safe` deja de tratar `date` · `MAX_PROCESADOS` se relaja a
`100000` · se conservan los primeros en vez de los últimos) y con
`front/scripts/verificar_panel_progreso.tsx`, que renderiza el panel con el feed
real y comprueba el **texto que sale** — incluido que no haya `undefined`,
`NaN`, `null` ni `[object Object]`, que `tsc` no ve.

### 31.bis. Los duplicados se pintaban como "omitidos" y no tenían contador — ✅ CORREGIDO

El backend ya sabía qué pasaba: al encontrar otro archivo con los mismos bytes
fija `scan_files.status = DUPLICADO`, apunta al ticket hermano y escribe en
`detalle` el texto «mismos bytes que …». **Pero reporta la acción como
`OMITIDO`**, que es el contrato de la respuesta. Tres huecos desde ahí:

| Hueco | Consecuencia |
|---|---|
| `es_duplicado=detalle.accion == "OMITIDO"` | un OMITIDO por falta de empresa se marcaba como copia, y la copia real como omitido |
| `estadoVisual` miraba `accion` antes que `es_duplicado` | el `case 'DUPLICADO'` del front quedaba inalcanzable en el escaneo |
| `ScanItemResponse.detalle` llegaba, pero el panel no lo pintaba | el operador veía «hay una copia» sin saber **de qué archivo** |

`Corrida.a_dict()` tampoco tenía `duplicados`: el contador existía solo en el
resumen final (`ScanResponse.duplicados`), así que **la corrida en vivo no podía
decir cuántas copias había** mientras el escaneo seguía corriendo.

Lo que quedó:

1. `es_duplicado` se deriva de `detalle.status is ScanStatus.DUPLICADO`, que es
   el dato guardado, no de la etiqueta de la respuesta.
2. `_ticket_de_otro_archivo` devuelve `(ticket_id, relative_path)`: el id responde
   qué gasto se conservó, la ruta qué papel de la bandeja es la copia.
3. `scan_registry.registrar_procesado` añade `detalle` a cada fila del feed e
   incrementa `Corrida.duplicados` **en la escritura**, antes de podar con
   `MAX_PROCESADOS`: cuenta duplicados de la corrida, no filas visibles.
4. El front evalúa `es_duplicado` antes que `accion`, pinta el destino y el
   «no crea ticket ni suma gasto», y muestra el contador aparte en la cabecera.
5. El archivado excluye por `detalle.status is not ScanStatus.DUPLICADO`, no solo
   por la cadena `"DUPLICADO"`: como la acción es `"OMITIDO"`, el guardia viejo
   era **código inerte** aunque el comentario dijera lo contrario. Con el
   archivado encendido la copia se retiraba de la entrada y `detalle` se
   sobrescribía con el motivo de bandeja, borrando justo la ruta que el panel
   necesita. `tests/unit/test_scan_service.py` lo comprueba sobre el retiro real.

**Los importes de esa fila son del ticket original**, no de una segunda lectura.
Contar la copia en `con_ticket` haría creer que la bandeja creó dos gastos; contarla
en `con_error` sugeriría que hay algo que arreglar, cuando lo único que pasó es que
alguien metió dos veces el mismo papel.

Verificado con `tests/unit/test_feed_del_ticket.py` (el test distingue OMITIDO de
DUPLICADO con el mismo `accion`) y con `front/scripts/verificar_panel_progreso.tsx`
(24 comprobaciones sobre el HTML renderizado). Mutaciones comprobadas a mano:
derivar `es_duplicado` de `accion`, cambiar la etiqueta a «omitido», apagar el
contador, borrar el destino y quitar el guardia de `status` del archivado — las
cinco mueren. La suite completa sigue con
**8 fallos preexistentes de entorno/colección desfasada**, idénticos con el árbol
limpio (`git stash`), ninguno en el feed. La muestra comparada fueron 8 fallos;
el conteo total de la suite no se afirma aquí porque la salida completa estaba
truncada.

### 32. El panel muestra los motivos del gate traducidos, y el código a la vista

Los códigos (`total_not_positive`, `subtotal_plus_tax_mismatch`) van **en inglés y
sin traducir**, y la explicación va en español al lado. La razón es de búsqueda:
esos códigos son los que aparecen en `validation_errors`, en los logs y en la base.
Traducirlos los haría imposibles de buscar, y un motivo que no se puede buscar
es un motivo que hay que volver a aprender a mano cada vez que el gate crece.

**Un código sin entrada en el glosario se muestra tal cual**, no se esconde. Es
mejor ver `algo_nuevo_del_gate` que no ver nada y creer que no hubo error: un
switch silencioso es un switch que miente cuando el backend crece.

### 33. `npm run verify` falla en `verify_login` — ✅ **CORREGIDO (opt-in)**

`verify_login.ts` tenía una etapa `contraLaApiReal()` que hacía un login real
contra `http://localhost:8000` y fallaba siempre: no leía credenciales, y con
`POST /auth/login` exigiendo scrypt y token, «un login real» solo puede ser con
una cuenta de verdad. Como `npm run verify` encadenaba esa etapa, la señal roja
era permanente y dejó de servir: nadie sabía si el front estaba bien o si solo
faltaban credenciales.

Ahora la etapa de red es **opt-in**: corre solo si están definidos `LOGIN_EMAIL` y
`LOGIN_PASSWORD`; si no, imprime
`Contra la API real: OMITIDA (defina LOGIN_EMAIL y LOGIN_PASSWORD para correrla)`
y sigue. Las etapas unitarias (las que no tocan la red) corren siempre.
`npm run verify` es verde en local sin credenciales y las tres puertas siguen
siendo `npm run build` (que corre `tsc`), `npm run lint` y los verificadores
puntuales (`npm run verify:caja`, `npm run verify:fecha`, `npm run verify:cierre`).
Con credenciales, la etapa de red vuelve a ejecutarse y puede volver a fallar por
su cuenta: eso es una prueba de integración, no una puerta de compilación.

### 18. El escáner de carpeta: lo que todavía no está resuelto
`feature/tickets-ocr-api` añadió el escáner y el registro por archivo. Estos cuatro puntos
son deuda real, no pendientes teoricos:

- **El lock de escaneo es de proceso, no de base.** `scan_service.py` usa un
  `asyncio.Lock`: dos escaneos en el mismo proceso se serializan, dos en procesos
  distintos no, y compiten por el `UNIQUE` de `relative_path`. El proyecto corre un
  solo contenedor, así que hoy no importa; con dos workers hay que poner
  `INSERT ... ON CONFLICT DO NOTHING` con reintento, o un advisory lock de Postgres.
- **`ScanStatus` no tiene un valor "desactualizado".** Un archivo `PROCESADO` cuyo
  contenido cambió y cuyo ticket tiene correcciones humanas queda `PROCESADO` con el
  aviso en `last_error`, y `GET /scan/stats` lo cuenta aparte. Es un parche: un
  cuarto estado sería más honesto y merece su propia migración.
- **Los symlinks se saltan sin aviso.** Un comprobante enlazado desde otra carpeta no
  se procesa y no aparece ni como `ERROR` ni como `NO_SOPORTADO`. Es la decisión
  conservadora (un symlink es la forma más corta de leer fuera de la carpeta), pero
  el operador no ve por qué su archivo no apareció. Registrar cada symlink
  saltado ensuciaría el registro en macOS, donde Finder los crea por todas partes.
- **El tope por corrida no tiene prueba de integración.** La lógica es un `break`
  con un contador y el campo `omitidos_por_tope` está en la respuesta, pero no hay
  un test que ponga 3 archivos, el tope en 2 y compruebe que queda 1.

### 19. `easyocr` estaba soportado pero **nunca funcionó** — 🟡 PARCIALMENTE CORREGIDO

La rama de easyocr **existía pero no podía ejecutarse.** `ocr.py:_leer_easyocr` pasaba una
imagen de Pillow a `readtext`, y EasyOCR solo acepta ruta, bytes o array de numpy:

```
ValueError: Invalid input type. Supporting format = string(file path or url), bytes, numpy array
```

Reproducido contra EasyOCR instalado en el contenedor. O sea: la afirmación "easyocr está
soportado" era una verdad a medias, y por eso el endpoint `GET /scan/ocr` reportaba
"no disponible" sin que nadie notara que además estaba roto.

**Corregido en dos partes:** la conversión a numpy y el paso por `_preparar` (que aplica
`exif_transpose`, escala y autocontraste). Lo del EXIF no es cosmético: una foto de iPhone
llega rotada 90 grados, y EasyOCR no la endereza solo. Sin eso se comparaban dos motores
con entradas distintas y el resultado no habría dicho nada sobre el motor.

**Lo que sigue pendiente, y es lo importante:**

- **`easyocr` ya está en `requirements.txt` — ✅ CORREGIDO.** La entrada explica
  además por qué `torchvision` se instala en el `Dockerfile` y no desde PyPI: una
  build con CUDA reinstallaría `torch` encima de la versión CPU. Lo que antes no
  persistía tras `docker compose up --build` ahora queda declarado.
- **No cabe en el contenedor con la memoria configurada.** Medido:

  | | RAM |
  |---|---|
  | Cargar el lector de EasyOCR | **1235 MiB** |
  | Tras una lectura | **1615 MiB** |
  | El API ya usando | ~460 MiB |
  | `mem_limit` de `expense-api` (medición original; hoy **1750m**) | **1709 MiB** |

  1615 + 460 > 1709: el proceso lo mata el OOM killer. **No es un problema de disco ni de
  dinero, es `mem_limit` en `docker-compose.yml`.** El límite actual (1750m) sigue por
  debajo de lo que el segundo motor necesita junto con la API; cambiarlo es una decisión
  de despliegue, y no se hizo a ciegas. El equipo tiene 18 GB y Docker tiene 8
  asignados, de los que en reposo se usan ~520 MB. Hay aire de sobra: la solución es
  medir con el límite nuevo, no comprar nada.
- **La confianza de EasyOCR no está normalizada.** EasyOCR devuelve la confianza por línea
  en `[0, 1]` pero hay versiones que la devuelven en `[0, 100]`, y el código la usa tal
  cual, así que `confianza_media` podría valer `87.0`. **No afecta al gate** (esa
  confianza la calcula `confianza_por_campos_ocr` a partir de la evidencia), así que el
  daño es acotado a un campo informativo. Normalizar por el máximo observado.

La medición de si dos motores juntos valen la pena está en `scripts/medir_dos_motores.py`
y su resultado en §21.

### 20. `TICKETS_INPUT_DIR` por omisión es una ruta de una máquina concreta
`config.py` usa `/Users/carloslott/Documents/Tickets/Tickets_app`, que es lo que pide el
enunciado. En cualquier otra máquina, en el contenedor o en la de otra persona hay que
cambiar la variable, y la app no falla: **crea la carpeta**. En un servidor eso
significaría crear `/Users/carloslott/...` como root. `GET /api/v1/scan/config` expone
la ruta efectiva para poder confirmarlo sin correr un escaneo, y `docker-compose.yml`
la sobrescribe a `/tickets` porque el `.env.dev` del host no aplica dentro del
contenedor. Es aceptable para "una máquina, un contador" y no para un despliegue real.

---

## 🔴 Seguridad

> Lo que sigue se verificó contra el código y contra el contenedor corriendo. Los
> archivos `.env` **no** están en el historial de git de este repo (`git log -S` sobre la
> contraseña y la clave no devuelve nada), así que lo pendiente ahí es higiene local, no
> exposición: sí hay que rotar por el repo público que menciona §15, y el `.gitignore`
> actual ya evita que vuelva a pasar.

### 21. El OCR cambia cifras y el gate no lo detecta — **el peor defecto medido**

**Medido con `scripts/medir_precision_ocr.py` sobre las tres fotos reales de
`~/Documents/Tickets/Tickets_app`, rotuladas a mano mirando el papel:**

| Archivo | El papel | El sistema leyó | Veredicto |
|---|---|---|---|
| `1C2C52A2…jpeg` (Oxxo) | **51.50** | **31.50** | fallo |
| `IMG_4220.jpeg` | 97.56 | 97.56 | acierto |
| `IMG_4222.jpeg` | 48.00 | 0.00 | fallo |

**Exactitud del total: 33.3%.** El objetivo pedido es 98.5%. Con tres comprobantes esto
no es una medida, es una anécdota (el script lo avisa), pero el orden de magnitud
no es un detalle: falta un factor de tres, no un punto.

| Campo | Exactitud |
|---|---|
| `tax_amount` | 100.0% |
| `provider_name` | 33.3% |
| `expense_date` | 33.3% |
| `total_amount` | 33.3% |
| `provider_tax_id` | 0.0% |
| `subtotal` | 0.0% |

**Y lo que frena el daño hoy: nada, dentro del ticket.** El total `31.50` equivocado pasa
`total_not_positive`, porque es positivo. Lo único que lo detiene es que al ticket le falte
RFC, subtotal y fecha, y por regla 4 no auto-aprueba. El daño está contenido por la
conservadurismo del gate, no por una defensa sobre la cifra.

#### El caso, al detalle

El recorte donde Tesseract coloca `31.50` **dice 51.50** (verificado mirando el recorte), con
**confianza 93**. Es un 5 leído como 3 y la confianza del motor no dice nada.

#### Lo que se probó y NO funciona, con la medición

Cuatro caminos, los cuatro descartados con datos y no con opinión. **No los vuelvas a
intentar sin una razón nueva:**

1. **No es resolución.** `_preparar` reduce a 2000 px de ancho; la foto es de 3840. A
   resolución nativa el total se sigue leyendo `31.50` (confianza 93). Subir la resolución no
   cambia el resultado.
2. **No es preprocesado.** Unsharp, SHARPEN, mediana y umbrales globales **no lo arreglan:
   lo desplazan**. Medido sobre el recorte, aciertos de `51.50` sobre 4 modos de
   segmentación: sin tratar 4/4 · unsharp r2 2/4 · mediana 3 2/4 · **umbral 110 0/4, que lee
   `51.80`** · unsharp r4 4/4 · umbral 165 4/4. Cualquier ajuste que "arregle" esta foto
   rompe otra; es elegir el parámetro que da el número que ya sabes. Un filtro de whitelist de
   dígitos empeora: `551.50`, `01.50`.
3. **No es un voto entre lecturas.** `_leer_tesseract` ya hace 12 lecturas (4 rotaciones ×
   3 PSM) y descarta todas menos una. Medido: **11 de las 12 no encuentran total, y la única
   que lo encuentra es la equivocada.** No hay contradicción que detectar: hay una lectura
   afortunada. Una regla de "mayoría" no tiene nada que votar.
4. **No es un re-OCR del recorte.** El recorte ceñido a los dígitos lee `31.50`; includída la
   etiqueta lee `51.50`; con un recorte genérico (todo el ancho de la banda) lee un **tercer**
   valor, `21.50`. Que el recorte a mano dé el correcto es casualidad de ajuste, no una
   técnica: la caja se eligió después de ver la imagen.

**Lo que sí se descubrió de paso:** el contexto de maquetación desambigua el glifo. Con
`TOTAL M.N. $` a la vista Tesseract lee `51.50`; con los dígitos solos, `31.50`. Es un
mecanismo real y explicable (el LSTM usa los tokens vecinos), pero no se puede explotar sin
saber de antemano dónde está el total, y saberlo requiere haberlo leído.

#### Lo que sí funciona, y es lo que queda

**Nadie afirma nada de las fotos.** Con la regla 4 vigente, ningún ticket de OCR auto-aprueba:
entra a la cola con `REQUIERE_REVISION` y una persona confirma el importe contra el papel. La
precisión de lo que el sistema **afirma** sobre fotos es 100% por construcción, porque no
afirma nada. Eso es defendible; lo que no es defendible es que nadie pueda **medir** si la
cosa mejora, y ese era el agujero real:

> **El reporte de exactitud no puede medir el OCR.** El muestreo del 5% entra solo sobre
> tickets `AUTO_APROBADO` (`ticket_persistence.py`), y un ticket de OCR nunca llega ahí.
> Medido en la base de este proyecto: las dos filas con `confidence_source='ocr'` tienen
> `spot_check_status = NULL`. `GET /api/v1/tickets/accuracy` agrupa por origen, así que su
> fila `ocr` siempre dirá "sin evidencia". **El número que se necesita no existe todavía.**

Por eso se agregó `scripts/medir_precision_ocr.py`: mide la ruta real de OCR contra una verdad
rotulada a mano, por fuera del ciclo de vida del ticket, y es lo único hoy que puede decir si
un cambio en el OCR ayudó o empeoró.

**Los comprobantes personales NO van al repositorio.** El archivo `verdad.json` vive en la
carpeta de las fotos (`--init` lo escribe ahí). El repo lleva el script y
`scripts/verdad_ejemplo.json`, con datos inventados.

#### Camino que sí queda, y cuesta

La evidencia dice que un lector único e inestable no da 98.5% en cifras. Lo que puede darlo:

- **Un segundo motor que vote.** EasyOCR ya está soportado en el código
  (`ocr.py:_leer_easyocr`, `obtener_motor("easyocr")`) y **no está instalado** porque arrastra
  torch (~2 GB). La inestabilidad de un lector es justamente lo que un segundo lector
  detecta. Con dos motores, el total se afirma solo si **ambos** coinciden; si no, a la cola.
  Contra: AGENTS.md prohíbe arrastrar torch al arranque y el presupuesto de RAM es de 7.7 GB
  con 7.2 GB ya comprometidos.
- **Más fotos rotuladas.** Con 3 no se mide nada. Con 30 se empieza a ver una tendencia; para
  afirmar 98.5% con intervalo de confianza hacen falta cientos. Es trabajo de captura, no de
  código, y es lo que decide si el resto vale la pena.

#### Dos reglas del parser que NO se adoptaron, y el caso que las refuta

- **El RFC sin etiqueta.** El ticket de Oxxo trae `(CCO-860523-1N4)` en el encabezado sin la
  palabra "RFC", y `RFC_CANDIDATO_RE` lo matchearía. **No se adoptó** porque en
  `IMG_4220.jpeg` el mismo patrón captura un RFC con etiqueta `R.F.C`, que es de la
  **empresa que factura** (una tercera), no del comercio que emitió el ticket. Sin
  etiqueta no hay forma de distinguirlos, y un RFC ajeno cuenta como evidencia en el gate.
  RFC ausente es mejor que RFC equivocado.
- **La etiqueta pegada: `ARTIC. TOTAL:` → `ATOTAL:`.** El OCR funde la etiqueta, el parser no
  la reconoce y el total queda en 0. Relajar el patrón haría leer `5000` (que es `48,00` con
  el punto perdido) como el total: 100× de error en una fila que parecería buena.

### 21.quater. El OCR no registraba el decodificador de HEIC — ✅ CORREGIDO

`app/services/ocr.py` **nunca importaba `app/core/heif.py`**, así que el soporte de HEIC no
era una propiedad del OCR: era un efecto secundario de que `scan_service`, `capture` y
`api/tickets` importen `ai_extractor` **antes** de que corra el OCR. Eso es orden de
imports, y no es una garantía.

**Reproducido con el archivo real** (`CremeriaHermanosCoronel.heic`), cargando `ocr.py`
por ruta sin que nada importara `ai_extractor` antes:

```
app.core.heif cargado?  False
PIL abre la HEIC?       NO -> UnidentifiedImageError
_preparar devolvio:     bytes  (los crudos, por el except)
```

Y lo que viene después es el fallo caro, porque **no parece de HEIC**: `pytesseract`
recibe algo que no es imagen y lanza `TypeError: Unsupported image object`. El operador ve
un error de OCR y busca en el OCR.

**Por qué pesa más de lo que parece:** una foto de iPhone es media propuesta de valor del
producto — `archivo_real.py` reconoce la firma `ftyp` de HEIC precisamente para no
dejar fuera al primer usuario con un ticket de teléfono — y este es el único camino que las
lee.

Lo interesante es que **el diagnóstico ya estaba escrito** en el docstring de
`app/core/heif.py`, con el modo de fallo exacto y la explicación de por qué centralizar el
registro no bastaba. Lo que faltaba era la línea en `ocr.py`. Un docstring que describe un
bug sin señalar dónde está el `import` es documentación, no defensa.

**La defensa, y por qué mira el código y no el comportamiento:** la primera versión del
test cargaba el módulo por ruta y miraba `sys.modules`, y **hacía skip en la suite
completa** — porque `conftest.py` importa `app.main`, que importa `ai_extractor`, que
registra HEIC. O sea que la foto abría de todas formas y el test, aun ejecutándose, no
medía lo que dice. Ahora recorre el **AST** de `ocr.py` y exige la sentencia de import; el
AST y no una búsqueda de cadena porque el docstring del propio test menciona
`app.core.heif` y una búsqueda por palabra pasaría con el import borrado. Muerto por
`verify_ocr_mutations.py`.

### 21.ter. `_linea_vertical` tenía un filtro comentado y no implementado — ✅ CORREGIDO

`app/services/parser_lineas.py:_linea_vertical` tenía el comentario

> `# Una linea sin ningun numero no aporta nada al inventario.`

seguido de un `)` — **la condición que el comentario describía no estaba**. El `return`
tenía dos ramas (`not texto`, `ROTULOS_FINALES.match(texto)`) y tres líneas de comentario
que describían una tercera.

Medido antes de corregirlo:

```python
_linea_vertical({"palabras": [{"texto": "GRACIAS POR SU COMPRA"}]})  # -> False
```

Es decir, el rótulo de un ticket entraba a `leer_lineas` como **línea de producto**. Y no
es inocuo por una razón concreta: una línea **sin** números no genera ningún número que
`_elegir_por_aritmetica` pueda rechazar, así que la aritmética —que es lo que protege de
una línea mala— no tiene nada que detener ahí. Depende de que el filtro de arriba la coja.

Ahora usa `re.search(r"\d", texto)` y **no** `re.match`: en un ticket el número va al
**final** de la línea (`COCA COLA 1.5L 35.00`) y el texto al principio, así que anclar al
principio habría descartado todas las líneas de producto. Esa es la trampa del arreglo, y
está en `test_no_descarta_la_linea_que_si_tiene_numero`.

**El comentario no era la prueba, y por eso conviene decirlo:** un comentario que describe
código ausente no es documentación, es una afirmación que el código contradice. La
diferencia con los casos anteriores de este archivo es que aquí el comentario **parecía**
correcto — describe algo que debería ser cierto — y por eso nadie lo revisó.

### 21.bis. La binarización adaptativa entra SIN medir — 🟠 abierta la medición

Lo que se hizo: se agregó umbral adaptativo gaussiano (`ADAPTIVE_THRESH_GAUSSIAN_C`) a
`app/services/ocr.py:_binarizar`, con OpenCV como dependencia **opcional**. Lo que
**no** se hizo: medir si mejora la exactitud.

**Por qué no choca con §21.** §21 descartó unsharp, mediana y **umbrales globales**
(110 y 165), que son otra pregunta: un umbral global pregunta «¿este píxel es más claro
que 165?», y el adaptativo pregunta «¿es más claro que la media de **su** ventana?». La
segunda es la pregunta correcta para una foto con una sombra cruzando el papel, y es
exactamente lo que un filtro global no puede responder. La medición de §21 no la cubre,
así que no la contradice — pero tampoco la respalda.

**Y por qué entra así, y no como reemplazo.** `_leer_tesseract` ya puntúa cada lectura
con evidencia del documento (`_puntuar`: total + RFC + aritmética) y se queda con la
mejor. La binarización entra como **una lectura más de esa competencia**: gana solo si el
papel sale mejor leído, y si no gana, el resultado es byte a byte el de antes. La garantía
que se puede afirmar hoy es pequeña y cierta —**no puede empeorar**— y no la grande y
desconocida. Es lo que permite ponerla antes de tener la medición.

Lo que se midió, con una imagen sintética (no es una foto real, así que no cuenta como
exactitud; cuenta como que el mecanismo hace lo que dice):

| Preprocesado | Lectura de una foto con sombra y tinta degradada |
|---|---|
| sin binarizar | **nada** — 0 palabras |
| umbral **global** 165 (lo que §21 descartó) | 8 líneas, **pero los dígitos cambian**: `1.23456` sale `1.23458`, `1.23468`, `1.23468Z`, `1.23468G` |
| umbral **adaptativo** gaussiano | 8 líneas, dígitos **estables**: `1.23456` en las 8 |

Eso es exactamente el modo de fallo que §21 describe — «no arregla, **desplaza**» — y en
esta imagen el adaptativo no lo tiene.

## EL A/B CON LAS FOTOS REALES: EMPATE — 🟢 cerrado, sin ganancia

Medido el **2026-10-09** con `scripts/medir_precision_ocr.py`, forzando
`ocr._binarizar = lambda imagen: None` para una corrida y dejándolo actuar para la otra.
Las 5 fotos que hoy se pueden medir (`~/Documents/Tickets/verdad/verdad.json`):

| | `total_amount` |
|---|---|
| **sin** binarización | **60.0%** (3 de 5) |
| **con** binarización | **60.0%** (3 de 5) |

**Empate exacto, en los mismos tres comprobantes y los mismos dos fallos.** Los dos fallos
son del mismo tipo y ninguno lo arregla la etapa: el parser leyó un **número suelto** en
vez del total — `97.56 → 2.00` y `48.00 → 1.00`. Eso **no** es ruido de imagen: es el
regex del total agarrando el número equivocado de una línea que sí se leyó bien.

**Conclusión honesta:** la garantía que se diseñó (no puede empeorar) se cumplió al pie
de la letra. La expectativa que se prometió (sube el total) **no se cumplió: la ganancia
es cero**. La etapa se queda porque no cuesta nada y está verificada, no porque sirva; y
queda escrito aquí para que nadie la recomiende después por un beneficio inexistente.

Y el caso de §21 —`51.50` leído como `31.50` con confianza 93— es un **glifo ambiguo**, y
un umbral adaptativo no arregla glifos. No hay preprocesado que lo haga: hay que resolverlo
con la aritmética o con un segundo motor.

### Lo que sí apareció al medir, y es más grave que esta etapa

**El 80% que AGENTS.md afirmaba no es reproducible.** Dos de las fotos sobre las que se
midió (`1C2C52A2-….jpeg` e `IMG_4220.jpeg`) ya no están en la carpeta. Con las 5 que sí
están, el total da **60%**. La cifra estaba escrita sin la condición que la sostiene, y
eso es lo que hace que una métrica envejezca mal: no avisa cuando deja de ser cierta.
Corregido en `AGENTS.md` con la fecha de la medición y la lista de lo que falta.

Lo que sí tiene defensa verificada (`scripts/verify_ocr_mutations.py`, 15 mutaciones,
todas mueren): el zoom es condicional (x2 solo bajo 1200 px) y con tope (nunca sobre
2000), `--oem 3` se dice explícito, y sin el paquete de OpenCV **el número de lecturas
es el de antes: 12**.

El detalle del `is_finite` que se quitó está en `AGENTS.md`, sección `LineaDeTicket`.
Se escribió un `field_validator` que rechazara `NaN` e infinito, y resultó **inalcanzable**:
Pydantic v2 ya los rechaza al construir el campo (`finite_number`), antes de correr
cualquier validador. Un validador que nunca se ejecuta nunca puede morir con él.

### 22. scrypt estaba por debajo del mínimo de OWASP — ✅ CORREGIDO
`app/core/security.py` usaba `N=2**14` con `p=1`, y el comentario afirmaba que era "el mínimo
recomendado por OWASP". **No lo era**: la Password Storage Cheat Sheet lista cuatro
combinaciones, y `N=2**14` solo aparece **con p=5**. Con p=1 eran 16 MiB y 27 ms por hash, 8
veces menos memoria que `N=2**17`.

Ahora usa `N=2**17, r=8, p=1`: 128 MiB y ~230 ms. Los hashes con N=2\*\*14 que ya estaban en
la tabla `users` **siguen validando**, porque el formato lleva sus propios parámetros
(`scrypt-v1$n$r$p$...`) y `_VERSION_HASH` no subió. Subir la versión sin migrar habría dejado
fuera a todos los usuarios con un `return False` silencioso.

`maxmem` pasó de una constante hardcodeada (132 MiB) a `_maxmem_para(n, r)`: con la constante,
un hash con N alto lanzaba `ValueError` en una máquina con menos memoria — un 500 en el login —
y al subir N sin subir el techo, **todos** los logins se rompían a la vez.
`verify_auth_mutations.py` cubre las cuatro (42 mutaciones, todas mueren).

> Lo que **no** se cambió: `ACCESS_TOKEN_EXPIRE_MINUTES=480` y la ausencia de refresh tokens.
> Son decisiones documentadas de diseño para "una máquina, un contador", no defectos. Lo que sí
> conviene saber es que **no hay revocación**: un token robado es válido 8 horas, y desactivar
> la cuenta lo invalida (regla 9) pero no invalida tokens ya emitidos para otras rutas que no
> reconsultan. Ver `deps.py`.

### 23. `CORS_ORIGINS=["*"]` con credenciales = cualquier sitio web lee la API — ✅ CORREGIDO
La app se sirve con `allow_credentials=True`. Con `allow_origins=["*"]`, `is_allowed_origin` de
Starlette devuelve `True` para **cualquier** `Origin` y, como hay credenciales, la respuesta
**refleja** el origen pedido en vez de mandar `*`:

```
Access-Control-Allow-Origin: https://sitio-que-no-es-nuestro.example
Access-Control-Allow-Credentials: true
```

Cualquier página abierta en el mismo navegador lee tickets, empresas y extracto bancario con
la sesión que ya está abierta. Sin contraseña, sin token, y sin aparecer en el log.

`config.py:_revisa_el_cors` ahora **aborta el arranque**. `tests/integration/test_cors.py`
pregunta al servidor, porque el middleware es de Starlette y leer el `main.py` no prueba nada.

> Un detalle que hace el ataque más grave de lo que parece: el token vive en `localStorage` y
> viaja en `Authorization: Bearer`, así que un `fetch` desde el origen ajeno puede reusarlo.
> Con una cookie `HttpOnly` el daño sería de lectura.

### 24. Los puertos se publicaban en `0.0.0.0` — ✅ CORREGIDO
`"8000:8000"`, `"5434:5432"` y `"11434:11434"` sin prefijo publican en **todas** las interfaces:
la API sin TLS y **la base de datos con su contraseña** alcanzables desde la red del café. Ahora
los cuatro van a `127.0.0.1`. El de Ollama es `11435` porque hay un Ollama nativo en 11434.

### 25. Archivos `.env`: de cuatro a dos a tres, con secretos duplicados — ✅ CORREGIDO
`.env.example` (versionado, sin secretos) · `.env` (600, solo configuración) · `.env.local`
(600, solo `SECRET_KEY` y `POSTGRES_PASSWORD`). `.env.dev` ya no existe.

**Primera ronda (cuatro → dos).** La contraseña de la base estaba en `.env.dev`, dentro de
`DATABASE_URL`, con permisos 644; `config.py` leía solo ese archivo mientras Compose leía los
dos, así que `uvicorn` en local firmaba con otra clave ("la sesión se cae, pero solo cuando
depuro"). Los comentarios además mentían: la clave "mide exactamente 32 caracteres" (mide 64) y
`.env.dev` "SÍ está versionado" (está en `.gitignore`).

**Segunda ronda (dos → tres, por responsabilidad).** La fusión dejó `POSTGRES_PASSWORD` en
`.env` junto a toda la configuración, y el secreto se llamaba `password_inicial` en Compose.
Dos fallos medidos al validar:

- **`env_file:` no interpola `${...}`.** Con el shell limpio, `docker compose config` moría con
  `required variable password_inicial is missing a value`, aunque los archivos estuvieran
  ahí. `--env-file` es lo único que alimenta la interpolación; `env_file:` solo inyecta
  variables al contenedor. Quitar `.env.local` de `env_file:` para "evitar duplicar" rompe la
  otra mitad: la API se queda sin `SECRET_KEY`, genera una clave efímera y cada `--reload`
  cierra las sesiones. Los dos mecanismos tienen que seguir vivos.
- **El chequeo viajaba en el mensaje equivocado.** `${password_inicial:?Copia .env.example a
  .env y rellenala}` mandaba a escribir el secreto en el archivo que tiene que quedar sin
  secretos. Ahora es `${POSTGRES_PASSWORD:?POSTGRES_PASSWORD no esta definida. Ponla en
  .env.local}`, y el alias `password_inicial=${POSTGRES_PASSWORD:?...}` se inyecta solo al
  contenedor para que `app/main.py` siembre el admin sin leer un segundo valor escrito.

`TestLaPlantillaDeLosEnv` defiende los tres archivos (sin secretos en la plantilla ni en
`.env`, los dos y solo los dos en `.env.local`, `CORS_ORIGINS` una sola línea).
`TestLosEnvFileDelArranque` mira la **receta** (`_lineas_de_receta`), no el texto completo:
una cadena en un comentario daba verde con el `--wait` y con los `--env-file` quitados.

**Pendiente, y es lo único de esta sección que no se puede arreglar en el código: rotar
`SECRET_KEY` y `POSTGRES_PASSWORD`.** Los archivos están fuera de git, pero §15 dice que el
repo estuvo público, y una clave que estuvo expuesta sirve hasta que se rota, no hasta que se
borra del archivo.

---

## 🟡 Deuda / data quality

### 26. El trigger de stock negativo contaba con el signo equivocado — ✅ CORREGIDO
Medido leyendo el DDL, y **inerte hasta que se escribieron ventas y ajustes**. Era el
defecto mas caro de los que quedaban en pie, porque la defensa contra el stock negativo
**no defendia** y nadie lo notaba.

Las dos mitades, medidas:

```
                  stock_de()        trg_movimientos_no_negativo
ENTRADA 10            +10                 +10
SALIDA  4              -4                  -4
AJUSTE  5              +5                  -5      <-- el trigger resta
SUM de filas previas:                    sin signo: con ENTRADA 10 y SALIDA 4 decia 14
                                        en vez de 6
```

Consecuencias, ambas reales:

1. Un `AJUSTE` de "se conto de menos" se rechazaba con *"dejaria el stock en -5
   (negativo)"* en una base donde la API ya lo habia aceptado con 201.
2. Con `ENTRADA 10` y `SALIDA 4` quedan 6. Una `SALIDA` de 7 mas deja **-1** y el
   trigger la dejaba pasar, porque su `SUM` sin signo creia que habia 14. La defensa
   contra el stock negativo no bloqueaba el caso para el que existe.

**Por que nadie lo noto.** La unica via que escribia en `movimientos_inventario` era
`confirmar_compra`, y esa **solo produce `ENTRADA`**: el `ELSE` del trigger no se
ejecutaba nunca y ningun test de ataque alcanzaba la rama. Es el mismo modo de fallo
que §9 y §21: una defensa en su sitio y no probada, y lo que la sostenia es que nadie
llego al camino.

Arreglado en `db/migrations/0013_compras_rechazables.sql`: el signo sale de una sola
pregunta (`tipo = 'SALIDA'`), que es la misma que hace `TipoMovimiento.suma_stock` en
Python. El `SUM` de filas previas ahora aplica el signo.

**Y la defensa nueva se rompio a proposito para comprobar que muerde.** Se revirtio la
migracion al bug y el script debe salir con 1. La primera version de la comprobacion
**daba verde con el bug puesto**, y el motivo esta escrito en
`verify_postgres_inventario.py::_un_ajuste_suma_no_resta`: usaba `tipo='ENTRADA'` con
`referencia_tipo='AJUSTE'`, que con el trigger viejo sumaba igual. La comprobacion
tiene que insertar `tipo='AJUSTE'` de verdad, que es el valor que la constraint acepta
y que un `psql` puede escribir aunque la API no lo haga nunca.

Comprobado contra Postgres real (`make up` y el script dentro del contenedor):

```
un AJUSTE suma (el signo del trigger)              ok
el stock descuenta las SALIDAS (el signo del SUM)  ok
la salida que no alcanza sigue bloqueada           ok
```

### 27. Funciones documentadas sin endpoint — ✅ CORREGIDO
La `cola` de productos (`GET /inventario/productos?solo_sin_verificar=true`) existia,
`AGENTS.md` le atribuia la tarea de *"revisar, renombrar o fusionar"*, y **no habia
ningun `PATCH` que la vaciara**. `TipoMovimiento.AJUSTE` estaba en el enum, `stock_de`
lo sumaba, y no habia ninguna via para escribirlo: tres valores de tipo, uno
producible.

Lo mismo con `EstadoCompra`, que solo avanzaba: una compra rechazada se "rechazaba" no
haciendo nada, porque `registrar_compra` la volvia a crear (y `compras.ticket_id` es
UNIQUE, asi que borrarla tampoco servia: el reescaneo la resucitaba).

Anadido en `0013`: `GET`/`PATCH /inventario/productos/{id}`, `POST
/inventario/movimientos`, `POST /inventario/compras/{id}/rechazar` y `/reabrir`,
`PATCH /reconciliations/{id}`, `PATCH`/`DELETE /reconciliations/mappings/{id}` y
`GET`/`GET`/`PATCH`/`POST /usuarios`.

Tres decisiones que no son obvias y estan en el codigo:

- **`AJUSTE` no tiene signo, y no se le da.** Se escribe como `ENTRADA` o `SALIDA`
  con `referencia_tipo='AJUSTE'`. Un unico valor de enum no puede decir "se conto de
  mas" de "se conto de menos", y `cantidad` es positiva por constraint.
- **Una `ENTRADA` sin `es_ajuste` se rechaza con 409.** Si existiera un camino que
  suma stock sin compra, `compras.ticket_id` UNIQUE dejaria de ser la garantia de que
  el inventario solo refleja compras de verdad.
- **No hay `DELETE` de producto.** `movimientos_inventario.producto_id` tiene
  `ON DELETE CASCADE`: borrar el producto borra su historial y `stock_de` deja de poder
  responder por el. Se da de baja con `activo=false`.

### 28. Los endpoints viejos de `/usuarios` no miran el rol — 🟡 PARCIALMENTE CERRADO
`PATCH /usuarios/{id}` y `POST /usuarios/{id}/contrasena` fueron los primeros endpoints
del proyecto que **comprometen a otro usuario** y siguen sin mirar `usuarios_empresas`.
Cualquier cuenta autenticada que sepa su propia clave puede dar de baja a otra, o rotar
la ajena si conoce la clave de esa cuenta. Se conservaron asi a proposito: ponerles
`requiere_rol` cambiaba su contrato y rompia `test_administracion_api.py`, que es la
defensa que existe para esos endpoints. La autoridad nueva esta en las rutas de
Mantenimiento (§49), no aqui.

Lo que si se hizo desde entonces:

- **Token Y contrasena** (`X-Contrasena-Actual`, `deps.get_current_user_verificado`).
  Un token robado no basta: es justo lo que el atacante de un token robado no tiene.
- **La ultima cuenta activa no se puede dar de baja** (409). Sin eso, un sistema con
  una sola cuenta se queda sin puerta de entrada y la unica recuperacion es entrar a
  Postgres a mano.
- **Cambiar la contrasena pide dos cosas distintas**: el header es la contrasena de
  *quien llama*, y `contrasena_actual` del cuerpo es la de *la cuenta que se cambia*.
  Con una sola, un atacante con un token robado rotaria la clave de cualquiera y el
  dueno ya no podria recuperarla.
- **Ya hay modelo de roles** (`0022`, §48) y una pantalla que lo administra (§49). Ya
  no es cierto que "no hay `POST /usuarios`" ni que no se pueda decidir *quien puede
  tocar a quien*: lo que sigue abierto es que las rutas antiguas no usen ese modelo.
  El alta sigue siendo autenticada, nunca publica.

Lo que **no** se hizo, y sigue siendo deuda: cerrar las rutas antiguas con el rol por
empresa sin romper a quien ya las usa. Hasta entonces, el producto sigue siendo de
"una maquina, un contador" en esa frontera, aunque el menu y el reset administrativo ya distingan
owner/admin de contador y operador.

### 29. `verify_postgres_inventario.py` no se podia aplicar a una base ya creada
`asyncpg` no acepta varias sentencias en una sentencia preparada
(`cannot insert multiple commands into a prepared statement`), y el script hacia
`conn.execute(texto_del_archivo_entero)`.

Era **inerte**: `_aplicar_migracion` solo ejecutaba `0010` si `movimientos_inventario`
no existia, y en una base creada con `db/init.sql` —que es lo normal— nunca hacia
falta. El fallo estaba en el camino que nadie tomaba, igual que §26.

Corregido con un partidor de sentencias que respeta bloques `$$` (el cuerpo de los
triggers es plpgsql y tiene `;` dentro), comillas simples y comentarios `--`. Los
comentarios importan de verdad: el SQL de estas migraciones cita codigo en prosa y hay
cuatro lineas con un apostrofo que abrian una cadena sin cerrar.

### 30. `PATCH /reconciliations/{id}` no decia quien habia corregido el veredicto
Anadido junto con el endpoint, y por el mismo motivo que §26: `match_status` decide que
se exporta (`MATCHED_STATUSES`), asi que un `PERFECT` que produjo el motor y uno que
aprobo una persona son la misma fila en la base.

Es el mismo motivo por el que existe `confidence_source` en `tickets` y por el que
`compras.confirmada_por` es obligatorio para `PROCESADO`: **"lo automatico lo hizo" y
"lo hizo una persona" no se suman**. Sin la columna, una conciliacion corregida a mano
salia a CONTPAQI indistinguible de una que el motor aprobo sola.

`revisado_por` sale del token y `revisado_at` lo pone el router: aceptar el autor en
el body seria firmarlo por otro.

### 12. `VendorNormalizer` nunca se ejecutaba — ✅ CERRADO BORRÁNDOLO

Solo lo referenciaba el `pipeline.py` muerto, y el módulo muerto se borró (§3).
`VendorNormalizer` ya no está en `ai_client.py` y `rapidfuzz` salió de
`requirements.txt` porque era su único consumidor. «Oxxo», «OXXO» y «OXXO
EXPRESS» siguen siendo tres proveedores en el dashboard: la normalización no se
implementó. Lo que se cerró es la deuda de tener código muerto que parecía una
defensa; el problema de producto sigue abierto y, si se decide resolverlo, hay
que hacerlo en la ruta viva (`capture.py`) con su test, no resucitando el módulo
borrado.

### 13. ~~`analitica.py` / `hallazgos.py` no están expuestos~~ — ✅ La premisa estaba mal
Son **servicios**, no routers. Que no aparezcan en `api_router.py` es lo correcto: se invocan
desde `app/api/dashboard.py:52` y `dashboard.py:368`, y ese router sí está montado. Alimentan el
landing del dashboard y el front los pinta (`Dashboard.tsx:585-604`).

Lo que sí conviene verificar es lo contrario: que un módulo de `services/` tenga a alguien que lo
llame. `VendorNormalizer` (§12) era el contraejemplo: instanciado en `ai_client.py` y con un
único caller en el `pipeline.py` muerto. Los dos se borraron; el patrón de revisión sigue.

### 14. Por la UI, ningún ticket pasa por el confidence gate — ✅ CORREGIDO
**Este punto se cerró.** La UI ofrece los dos caminos, y se elige con un botón explícito:

| | "Aceptar y verificar con IA" | "Revisar y corregir" |
|---|---|---|
| Endpoint | `extract-and-create` | `POST /tickets/` + `subirDocumento` |
| Revisión humana antes de guardar | no (se guarda directo) | sí |
| Comprobante original | sí | sí |
| `confidence` + `confidence_source` | los del gate | NULL / MANUAL |
| Entra al muestreo | sí, si sale `AUTO_APROBADO` | no |

Implementación: `Tickets.tsx:363-390` (`aceptarLecturaDeIA`) y `Tickets.tsx:400-406`
(`editarAntesDeGuardar`). Si `extract-and-create` falla, se degrada a la vía manual con el
formulario ya lleno: un ticket guardado con su comprobante vale más que una lectura perfecta que
se perdió.

**Por qué se elige por clic y no comparando el formulario contra la extracción:** un diff de
strings se rompe por cosas que no son corrección — `"4094.80"` contra `"4094.8"`, una fecha con
otro formato, un RFC con espacios. Y el usuario tiene que saber que está eligiendo, porque los
dos caminos terminan en tickets con significado distinto.

**La limitación que sí queda, y es real:** es una intención declarada, no un hecho verificado.
Si el usuario pulsa "Revisar y corregir" y no cambia nada, el ticket se guarda como `MANUAL` y no
cuenta para la medición (falso negativo de exactitud). La señal es el clic, no el contenido.
Arreglarlo exigiría comparar la extracción contra lo guardado **en el backend**, donde se puede
comparar el `Decimal` ya parseado y no el string que escribió el usuario.

### 16. `.vscode/` ignorado por configuracion global — ✅ CORREGIDO
El `.gitignore` del repo decia `!.vscode/settings.json` y sus tests de texto daban verde,
pero `git status` no mostraba el archivo. **La documentacion afirmaba una cosa y git hacia
otra** — el mismo modo de fallo que el punto 15, y el que este repo viene pagando en
consecuencia.

Causa: `~/.gitignore_global` (linea 5) tenia `.vscode/`, la CARPETA. Git no baja a un
directorio excluido, asi que ninguna `!` declarada abajo puede re-incluir un archivo dentro.
La unica forma de meterlo era `git add -f`, que funciona una vez y el siguiente `git add .`
lo pierde en silencio.

Arreglo: `.vscode/*` en vez de `.vscode/`. Se conserva la intencion (los repos que no lo
exentan siguen ignorando todo lo de ahi) y se abre la puerta a que un repo opte por versionar
archivos concretos.

La parte que importa: **los tests que leian el texto del `.gitignore` no lo habrian detectado
nunca**, porque afirmaban algo que era cierto sobre el archivo y falso sobre la realidad. Por
eso `test_git_no_lo_ignora` pregunta a `git check-ignore` en vez de leer el `.gitignore`, y por
eso la mutacion esta en `scripts/verify_vscode_mutations.py` (no automatizable: tocar el
`.gitignore` global es de la maquina, no del repo).

### 15. Credenciales expuestas en un repositorio público — ✅ CORREGIDO
**Qué pasó.** El repo estuvo público con `.env.dev` versionado desde el commit inicial. Ahí
vivían las dos credenciales del stack:

- `SECRET_KEY`, que firma los tokens HS256. Con ella se puede **firmar un token válido sin
  pasar por el login**: `leer_token` verifica la firma y `get_current_user` solo exige que el
  `sub` sea un UUID con formato. La defensa de reconsultar el usuario en la base no ayuda si el
  token es auténtico — el usuario existe, lo que el atacante controla es *quién dice ser*.
- La password de Postgres, en `.env.dev`, en `docker-compose.yml:12` y `:78`, en el README y en
  los 5 `verify_postgres_*.py`, que la tenían hardcodeada en el DSN.

**Por qué no era solo higiene.** `AGENTS.md` §Reglas 5 y 9 son correctas y son las que hacen que
esto sea un problema: reconsultar el usuario en la base, y no servir el `content_type` que declara
el cliente. Ambas asumen que la clave que las sostiene es secreta. **Una defensa que depende de
un secreto público no es una defensa**, por muy bien escrita que esté.

**Qué se hizo.** Rotadas las dos (borrar el archivo no las saca de los 6 commits donde ya
estaban), `.env.dev` dejó de versionarse, `docker-compose.yml` lee la password de `.env` con
`${POSTGRES_PASSWORD:?...}` —que además hace que el compose falle con un mensaje en vez de
levantar un Postgres con password vacía—, los 5 scripts leen el DSN del entorno, y el README
dejó de publicarla.

**Verificado:** un token firmado con la clave que estaba en GitHub responde `401`; uno legítimo
responde `200`.

**La lección, que es la parte que importa:** cambiar el archivo no borra el historial. Un secreto
que estuvo versionado está comprometido aunque lo borres, y la única salida es **rotarlo**.
Por eso la plantilla (`.env.example`) sí se versiona y el valor real nunca.

### 18. El export CONTPAQI inventaba datos que nadie le dio — ✅ CORREGIDO
`export_service.py` escribia en todas las filas, sin preguntar: `TipoComprobante="I"`,
`Moneda="MXN"`, `TipoCambio="1.0000"`, `MetodoPago="PUE"`, `UsoCFDI="G03"`, y
`Serie`/`Folio`/`Cuenta` vacías. Nada de eso venía de leer el comprobante: eran
literales en un diccionario.

El problema no es que fueran defaults, es que **un default escrito en una celda deja
de ser un default y pasa a ser una afirmación**. Un comprobante en USD salía con
`Moneda=MXN` y `TipoCambio=1.0000`, sin marca de error, y el contador lo subía a
CONTPAQI creyéndolo. Para un contador, una celda vacía dice "no lo sé" y se corrige
a mano; una celda con `1.0000` dice "lo sé", y eso no se ve ni se corrige.

Es la regla 11 del contrato —lo que no se pudo calcular se declara, no se rellena—
aplicada a la exportación.

**El camino para llenarlas sigue igual:** `AccountingMapping`, que es la decisión
del contador y queda registrada en el mapeo. Lo que se quitó es que el sistema lo
decidiera por él. Hay un test que lo comprueba (`test_con_mapping_las_columnas_siguen_
aceptando_valores`), porque quitar el default no puede quitar la capacidad.

**Y el test que ya existía afirmaba el defecto:** `test_transform_to_contpaqi_
applies_mapping` afirmaba `result[0][13] == "1.0000"` con `mapping=None`. Un test que
pasa porque el código tiene el bug. Los índices de ese test también iban escritos a
mano y uno estaba corrido; ahora salen de `CONTPAQI_COLUMNS`.

### 19. La fecha inventada depende de la zona horaria y el test no lo ve

`verify_capture_mutations.py` corre 26 mutaciones sobre la ruta de captura y **una sobrevive**:

```
- una fecha ausente se reemplaza por la fecha UTC
```

La mutación cambia `app/api/tickets.py`:

```python
expense_date=extracted.expense_date or date.today(),    # hora local
expense_date=extracted.expense_date or utcnow().date(),  # UTC
```

Las dos expresiones dan la misma fecha durante la mayor parte del día, así que
`TestFechaInventadaEnLaFila` no puede distinguirlas: **el test pasa por casualidad del
reloj, no por criterio**. Solo fallaría en las horas pegadas a medianoche, en un huso
horario al oeste de UTC, o el día que cambie el huso.

No es hipotético lo que se protege. Cuando la lectura no trae fecha, el sistema **fabrica
una** para que el ticket exista, y esa fecha entra al cierre mensual: es justo el caso que
`parser_service.py:498` ya marca como prohibido. Fabricar una fecha es una decisión
deliberada y auditable; fabricarla **en UTC en vez de en hora local** desplaza el
comprobante al día siguiente —o al anterior— y eso cambia en qué periodo cae el gasto. Un
ticket de las 23:30 en México se guarda con la fecha de mañana y desaparece de donde el
contador lo buscó.

**Estado:** verificado, sin corregir. La mutación sobrevive desde antes de este commit; se
comprobó revirtiendo el cambio de imports de `ai_client.py` y reproduce idéntico.

**Arreglo (cuando se retome):** que el test no dependa del reloj. O bien fijar la zona
horaria del proceso en el test (`TZ=America/Mexico_City` más `time.tzset()`), o bien
parchear `date.today` para que devuelva un valor conocido. Un test que depende de la hora
del día no es un test: pasa o falla según cuándo se corra.

### 17. El cliente elegía la ruta de lectura — ✅ CORREGIDO
`file_type` llegaba como campo `Form` y se usaba literal para elegir el escalón de
la cascada (`capture.py:289-297`). Con un token válido, quien llama decidía por dónde
se leía el documento. Medido con el mismo PDF de las dos formas:

```
file_type=pdf   ->  pdf_text  ->  0.97  ->  PAPELERIA Y SUMINISTROS  ->  4094.80
file_type=image ->  llm       ->  None  ->  Unknown Provider        ->  0.00
```

Las tres consecuencias, en orden de gravedad:

1. **By-pass de la regla 3 de `AGENTS.md`.** "Un PDF con texto no toca el modelo" es
   la barrera conceptual más importante del sistema y la salta quien llama.
2. **Contaminación de la medición.** El ticket entra con `confidence_source=llm`, y el
   reporte de exactitud agrupa por origen (`accuracy_service.py:346-357`). Una lectura
   que no fue lectura falseando la evidencia del SLO.
3. **Amplificador de DoS.** Forzar visión sobre 10 MB obliga a renderizar, reescalar e
   inferir. Con un token válido, es lo más barato que hay para quemar la máquina.

**Arreglo:** `app/core/archivo_real.py` deduce el formato de los bytes con lista cerrada
de firmas, y el `file_type` del cliente solo se acepta si *coincide*. Es el mismo patrón
que ya existe dos veces en el repo por el mismo motivo: `ENCODINGS_PERMITIDOS` y
`content_type_servible()`.

**No es un 4xx, y esa es la parte importante:** el documento es válido, lo que estaba mal
era la etiqueta. Un contador no puede perder su comprobante porque su cliente mandó
`image` en vez de `pdf`; se procesa igual con el formato deducido, y el motivo queda en
el log.

**El otro extremo también se cerró.** Mandar `pdf` con algo que no es un PDF ya no llega
al modelo: por rules, `render_pdf_pages` falla y sale "ilegible"; por imagen, `_a_jpeg()`
devuelve `None` y sale `IMAGEN_ILEGIBLE` en vez de reenviar los bytes declarados
`image/jpeg`.

**Verificado por mutación:** 8 mutaciones, 8 mueren. Tres de ellas sobrevivieron a la
primera ronda y por qué:
- Dos tests llamaban a la función en vez de al endpoint. Una defensa bien escrita y no
  conectada deja todo en verde; por eso los tests de cableado van por HTTP.
- El test de `source_type` comparaba dos POST del mismo archivo a la misma empresa, y la
  idempotencia por `(company_id, source_hash)` devolvía el mismo ticket. Comparaba dos
  veces lo mismo sin mirar nada.

### 38. La fecha del comprobante se descartaba cuando sí estaba — ✅ CORREGIDO, y **sin ganancia medible**

El **2026-10-10**, con las 7 fotos rotuladas de `verdad.json` (las 5 de antes más 2 que
aparecieron en el respaldo):

**1. `provider_name` está en 0%, pero 3 de 7 no son fallos.**

| foto | verdad | lo que devolvió el parser |
|---|---|---|
| `1C2C52A2-….jpeg` | `CADENA COMERCIAL OXXO` | `Cadena Comercial) Uxxo,` |
| `IMG_4222.jpeg` | `MERCASTAR` | `A ~ NERCASTAR \|` |
| `IMG_4253 2.HEIC` | `GRUPO COMERCIAL DSW S.A. DE C.V.` | `GRUPO COMERCIAL DS!` |

Son el mismo comercio con el OCR encima. **La comparación del script es de cadena exacta**, y
una cadena exacta no sobrevive al ruido de un logo. Los otros 4 sí son fallos reales: 2 dan
el nombre de la sucursal (`CMT QUERETARO REVOLUCION` en vez de `CARNE MART`, que no está en
el texto) y 2 toman ruido de caja (`AAA AI`, `Pog ae — TAPA`, `Y ARTICULO TOT`).

**El 0% es una cota pesimista, no el estado real.** No se cambió la yardstick: hacerlo
justamente para que el número suba es la forma limpia de fabricar una expectativa falsa.
Queda escrito para que nadie recomiende una heurística de proveedor "para subir el 0%", que
es lo que pasa cuando se lee esta tabla sin el detalle.

**2. `provider_tax_id` en 0% es un límite del motor, no del parser.** Los cinco RFC no están
en el texto que devolvió el OCR. Ninguna regla los recupera.

**3. `subtotal` en 43% se le pide a papeles que no lo traen.** El ticket de OXXO imprime
`IVA INCLUIDO: $ 0.00`: el IVA va **dentro** del total. El propio archivo de verdad lo dice.
Cuatro de los siete fallos de `subtotal` no son fallos del sistema.

**4. Lo que sí se corrigió, y por qué no mueve la cifra.**

Había dos rutas de fecha y las dos botaban fechas que el papel sí traía:

| texto real del OCR | qué pasaba |
|---|---|
| `06/10/2026 11:30:21 211.36 36 30` | la fecha de la compra, sin ninguna marca al lado → `date_missing` |
| `o 02 Fecha: 08/10/202¢ iH tap i` | el OCR comió el `6` de `2026` → `date(202,10,8)` → **`0202-10-08`**, una fecha concreta y falsa |

Lo segundo era lo grave: no era un dato que faltaba, era **un año inventado con forma de
leído**. Sale del parser hacia la cola de revisión y solo lo marca `date_too_old` después.

Se corrigieron tres cosas: la **hora pegada** cuenta como marca (un vencimiento de cupón es
un plazo y se imprime sin hora; una fecha con hora es un momento), el **año son 2 o 4
dígitos nunca 3**, y la ruta etiquetada pasó de `pd.to_datetime(dayfirst=True)` a `_a_fecha`.

**El A/B da CERO.** Sobre las 7 fotos, los seis campos dan **exactamente el mismo número**
antes y después, con el **mismo texto OCR** en las dos columnas. La razón está escrita en
el test que acompaña el arreglo: en esas 7 fotos la fecha no está en el texto que devolvió
el OCR, y ahí ningún parser la recupera. El arreglo no se equivocó; no tenía dónde ganar.

**Eso es lo que hace peligroso medir sin A/B.** El OCR **no es determinista entre corridas**
(la foto de DSW dio `SUBTOTAL 279.33` en la corrida guardada y `279.93` en una nueva), así
que dos corridas del script de medición comparan **dos motores y no dos parsers**. Con 7 fotos
el ruido es del mismo orden que el efecto. Por eso existe `scripts/medir_precision_ocr_ab.py`:
lee cada foto una vez, cachea el texto y deja evaluar N versiones del parser contra ese
mismo texto.

**Una defensa que se probó y se quitó por ser código muerto:** añadir `fecha` a
`MARCA_DEL_COMPROBANTE_RE`. El argumento era bueno —una línea que dice `Fecha:` **es** la
fecha— pero `_fecha_del_comprobante` solo corre cuando el bucle principal no encontró nada,
y una línea `Fecha:` ya la agarra el patrón etiquetado de más arriba. Una defensa que nunca
decide nada se lee como una garantía y no está protegiendo nada. Lo que sí faltaba, y sí
es real, era la hora.

**Verificado por mutación:** `scripts/verify_fecha_mutations.py`, 8 mutaciones, 8 mueren.
Dos de ellas sobrevivieron a la primera ronda, y por qué:
- La del año de 3 dígitos en `FECHA_SIN_ETIQUETA_RE` **no se aplicaba**: la mutación
  buscaba un texto que ya no estaba en el archivo. Un verificador que no se aplica y sigue
  contando es peor que uno que no existe, porque el conteo dice 8 y son 6.
- La de `pd.to_datetime` **sobrevivió**, porque con la regla del año ya puesta el año de 3
  dígitos nunca llegaba ahí. Buscando el caso donde de verdad difieren apareció el bueno:
  `Fecha: 01/13/2026` da `2026-01-13` en `pd.to_datetime` — **día y mes cambiados, sin
  avisar** — y `None` en `_a_fecha`. Se prefirió `None`: entre adivinar una fecha e ir a
  revisión, se va a revisión. Y un intercambio silencioso pasa todos los checks del gate,
  porque una fecha creible no es una fecha futura ni una vieja.

**Con 7 fotos esto no es una medición, es una anécdota** — y el propio script lo dice. Para
afirmar 98.5% hacen falta cientos.

---

### 39. El total se perdía con la etiqueta `Total` bien leída a la vista — ✅ CORREGIDO (1 de 3 → 2 de 3)

> **Cifras medidas con el instrumento de antes**, que se comía una foto sin avisar (punto 40).
> Repetido con el instrumento arreglado dan las mismas dos de tres, pero el A/B oficial es
> **33.3% → 66.7%** sobre las tres fotos. Ver el punto 40.

El **2026-10-15**, sobre el texto de OCR que devolvió la corrida de las 18:42 (guardado en
`/tmp` antes de limpiar la base; el parser anterior y el nuevo sobre **ese mismo texto**):

**El síntoma que reportó quien digitaliza:** los montos no coinciden con el papel.

**No era que el parser no supiera leer `Total $`.** Medido con las seis formas de la etiqueta
—`Total $ 193.65`, `Total $193.65`, `TOTAL $ 193.65`, `Total$: 193.65`, `Total $ 1,100.50`,
`Total $ 51,50`—: **las seis se leían bien**, antes y después. El caso que fallaba es otro:

```
Total $ 193.65 =                    <- la etiqueta CON su importe
oe Nenta total 5 © 19S Be           <- ruido de caja del OCR
Total despues del ahorro $ 193 65   <- filtrada por AHORRO_EN_LINEA_RE
```

Con el ruido **antes** de la línea buena, el mismo texto daba `193.65`. Con el ruido
**después**, daba **`5.00`**, y el papel dice `193.65`. La etiqueta se leía bien y **una línea de
basura escrita después la pisaba**, porque el bucle competía por posición y ganaba la última
coincidencia.

**El arreglo** es un criterio de fuerza, no de posición (`_fuerza_de_un_importe`): un importe
**con centavos** no se deja pisar por un **entero suelto**. El entero suelto es, en un
comprobante mexicano, mucho más probable que un trozo partido (`51 .50`) o un código que el OCR
entrecortó (`19S` → `19`). Es el mismo criterio y el mismo "por qué" que ya tenía
`_importe_tras_la_etiqueta`; lo nuevo es que también decide **entre líneas**, y no solo dentro
de una.

**El desempate entre candidatos de la MISMA fuerza NO cambió** (sigue ganando el último, que es
como funcionaba). Se conserva a propósito: cambiar la fuerza y el desempate en el mismo commit
haría imposible atribuir el efecto de ninguno de los dos, que es exactamente el error que
produce un arreglo de este tipo.

**Lo que destapó el arreglo, y que estaba escondido desde antes:** el guardia que excluye el
subtotal comparaba contra la palabra `"subtotal"`, y en `Sub-Total: 500.00` hay un guion en
medio, así que **la línea no se excluía**. El patrón de total sí la aceptaba (su borde es
`(?:^|[^\w])` y el guion no es carácter de palabra), de modo que el subtotal entraba como
candidato y competía con el total real. No se notaba porque el desempate era "gana la última" y
el total real va después; con la regla nueva, `Sub-Total: 500.00` + `Total: 580.00` devolvía
`500.00`. Lo fija `_es_linea_de_subtotal`, que normaliza guion, espacio y guion bajo.

**Medido, con verdad rotulada, sobre el mismo texto OCR (3 fotos):**

| foto | papel | antes | ahora |
|---|---|---|---|
| `IMG_4398.heic` | `193.65` | `5` | **`193.65`** |
| `IMG_4407.heic` | `234.00` | `234` | `234` |
| `IMG_4409.heic` | `279.93` | `219.93` | `219.93` |

`1/3 → 2/3`. **Con 3 fotos eso es una anécdota, no una medición**, y la que sigue fallando no
es cosa de este arreglo: en `IMG_4409` el texto del OCR solo trae `TOTAL` a secas y la línea de
ahorro, así que el parser no tiene de dónde sacarlo y el número que se guardó es **el del modelo
de visión** (`219.93`), que es el defecto ya documentado de §"la lectura que se guarda".

**Verificado por mutación:** tres mutaciones nuevas en `scripts/verify_capture_mutations.py`, las
tres mueren — y la primera muere en el test del caso medido, con `Decimal('5')` contra
`Decimal('193.65')`, que es la forma más legible de ver el defecto.

**Lo que este arreglo NO arregla:** el total absurdo de `IMG_4415` (`750047801456`, el del "techo
del total") sigue saliendo igual, porque es un problema de lo que el OCR leyó, no de qué línea
gana.

---

### 40. La medición se comía fotos sin avisar, y por eso el §39 no se podía citar — ✅ CORREGIDO

MEDIDO el 2026-10-15. La foto que motivó el §39 (`IMG_4398.heic`, la del `total 5.00`)
**nunca se había medido**, y el script no lo decía.

La causa: la verdad la nombra `IMG_4398.HEIC` y el archivo en disco se llama `IMG_4398.heic`, y
el filtro era `f.name in verdad` —comparación exacta—. **No es un número malo: es menos datos de
los que creías**, y la foto perdida era justo la del defecto que se estaba mirando.

Dos arreglos, en `scripts/medir_precision_ocr.py` (y el A/B usa la **misma** función, no una
copia):

1. El nombre se compara en minúsculas, y `selecciona_fotos()` devuelve **también** las rotuladas
   que no tienen foto, para que se impriman.
2. **La caché del A/B ahora corre el OCR solo de las fotos que faltan.** Antes `if CACHE.exists():
   return cache` + `evaluar` recorriendo las claves de la caché = **una foto nueva jamás se
   midía**. Agregar evidencia a la medición era la única forma de no tenerla, y sin aviso.

Lo que la corrección destapa al correrla de verdad sobre las 8 fotos de la carpeta:

```
3 foto(s) rotuladas en /tickets
ATENCION: 7 rotulada(s) NO estan en la carpeta y quedan fuera del A/B:
  1c2c52a2-….jpeg, aa4d0e8f-….jpg, ee2c6866-….jpg, fc3cb9f8-….jpg,
  img_4220.jpeg, img_4222.jpeg, img_4253 2.heic
```

**El A/B del §39 con el instrumento arreglado, texto OCR idéntico en las dos columnas:**

| `total_amount` | antes | después |
|---|---|---|
| | **33.3%** (1 de 3) | **66.7%** (2 de 3) |

Con 3 fotos eso es una anécdota, no una medición, y el propio script lo dice. Lo que sí se puede
afirmar sin adornos es que **el instrumento ahora mide lo que dice que mide**, que era lo que no
se podía afirmar.

**Verificado por test:** `tests/unit/test_medir_precision_ocr.py`, 8 tests, con el caso real
(`IMG_4398.HEIC` contra `IMG_4398.heic`). Mutado el filtro a comparación exacta, mueren 4.

---

### 43. Los modelos se instalaban a mano, y el aviso que lo decía no servía — ✅ CORREGIDO

**MEDIDO el 2026-10-15.** El `ollama pull` que el propio front pedía era un paso manual que se
olvida en cada máquina nueva. Peor: la causa de que faltara era invisible, porque `/scan/ia` para
Ollama tenía el cálculo de `faltan` cortocircuitado con un `pass` y su `else` —el único que lo
hacía— era código muerto. Con **cero** modelos instalados reportaba `faltan: []` y
`disponible: true`.

Ahora hay un servicio `modelos` que baja lo que declara la `.env` al hacer `make up`, y la API
depende de él con `service_completed_successfully`.

**Los tres bugs que costó, porque los tres se ven igual desde fuera:**

1. **Sin `networks`, no resuelve `ollama`.** Compose mete un servicio sin `networks` en la red
   **por defecto** del proyecto, que no es `expense_network`. El script se quedaba esperando y
   decía *"Ollama no respondio"*, que es falso: responde, es que no estaba en su red.
2. **Los modelos por `${OLLAMA_MODEL}` en vez de `env_file`.** Descargó `moondream` mientras la
   app usaba `qwen2.5:3b`. Compose resuelve `${VAR}` **primero del shell y después de la `.env`**,
   y en esta máquina el shell tiene `OLLAMA_MODEL=moondream` exportado. Dos servicios leyendo la
   "misma" variable con reglas de precedencia distintas, y el que no gana es invisible.
3. **`set -e` + `&&` rompió el "ya está".** `[ -z "$x" ] && continue` dentro de un `while` en una
   tubería: cada `&&` que evalúa a falso devuelve 1 y `set -e` sale del subshell en la primera
   línea, antes de comparar nada. La segunda versión tenía la lógica al revés. **Las dos se veían
   igual: `make up` bajaba 3.2 GB en cada corrida.** Parecía un fallo del `ollama pull` y era de la
   pregunta.

> **El detalle que hace esto fácil de repetir:** los tres producen el mismo síntoma —"no hay
> modelo" o "vuelve a bajarlo"— y ninguno de los tres estaba donde el síntoma apuntaba.

**Cambio de modelo, con lo que se afirma y lo que no:** `OLLAMA_MODEL` pasa de `moondream` a
`qwen2.5:3b`. `moondream` está documentado en la propia `.env` como *"no lee comprobantes"*, y es
un modelo de ~1.8B para **describir** imágenes; aquí hace falta **extraer campos**. **No se afirma
que `qwen2.5:3b` lea mejor un PDF**: no hay ningún PDF rotulado, así que no se puede medir. Para
volver: `OLLAMA_MODEL=moondream`.

**El aviso del front, que además mentía:** decía *"Mientras falte, esos comprobantes caen a la
lectura por reglas o se quedan en la cola"*, y con el modelo de visión instalado eso era **falso**:
lo que faltaba era el de **texto**, que no toca las fotos. `EstadoDeIa` distingue los dos casos.

**Verificado:** `tests/unit/test_instalar_modelos.py`, 10 tests, con un `ollama` falso en el PATH
—sin red y sin 3 GB. Las tres mutaciones de arriba mueren: quitar la red, volver a la
interpolación, y la lógica al revés.

**Trampa de arranque, medida el mismo día:** `docker compose restart` **no** relee el `.env`. Las
variables de `env_file` se meten en el entorno del contenedor **al crearlo**, y `os.environ` gana
sobre el archivo que la app lee. Tras cambiar `OLLAMA_MODEL`, `restart` dejó la app con el valor
viejo. Hay que **recrear**: `docker compose up -d --force-recreate <servicio>`.

---

### 44. Un archivo que no se puede leer desaparecía sin dejar rastro — ✅ CORREGIDO

**MEDIDO el 2026-10-16.** Tres fotos en la carpeta, las tres con `[Errno 5] Input/output error` al
abrirlas (un montaje de archivos de Docker Desktop roto tras un reinicio), y el `POST /scan`
devolvió **200** con `archivos_vistos: 0`. Sin fila en `scan_files`, sin evento en el registro de
la corrida, sin número en el resumen y **sin una palabra en la pantalla**. El único rastro era una
línea de log.

Dos sitios se perdían, y el primero es el que importaba: `listar_archivos()` hacía `lstat()` y, si
fallaba, `continue` a secas — el archivo **no existía** para el escaner, así que no llegaba ni a
contarse. El segundo (`read_bytes()` dentro del bucle) al menos sumaba a `con_error`.

**Lo que se cambió:**

- `listar_archivos(con_ilegibles=...)` recoge los que no se pudieron abrir, con nombre y motivo. Es
  un **parámetro de salida** y no un valor de retorno porque nueve llamadores de la suite y del
  servicio usan esa función como `list[ArchivoVista]` y solo uno necesita el dato.
- `ResumenEscaneo.ilegibles`, `ScanResponse.ilegibles` y el `Scan.tsx` lo muestran, con el motivo
  al lado.
- **Lista y no número**, porque "3 ilegibles" deja igual de amplio el abanico entre "la carpeta no
  está montada" (un problema de entorno, se arregla fuera) y "tres papeles dañados" (hay que ir por
  ellos). Son dos acciones opuestas.

**Por qué no alcanzaba con contar:** `con_error` es para los que se abren y no se entienden,
`no_soportados` para los que están bien en un formato que no se lee, y `omitidos_por_tope` para los
que se verán en la pasada siguiente. Un archivo ilegible no es ninguno: **no se volverá a mirar
nunca**, y de los tres solo `omitidos_por_tope` promete un "vuelve a escanear" que aquí no se
cumple.

**Verificado:** `tests/unit/test_archivo_ilegible_se_ve.py`, 5 tests. Tres mutaciones mueren:
el `continue` a secas, el router que deja de pasarlo, y el campo que desaparece del schema.

---

### 41. La IA no estaba disponible y el sistema decía que sí — ✅ CORREGIDO (y con él, el techo de exactitud)

**MEDIDO el 2026-10-15.** El síntoma que reportó quien escanea: los montos no coinciden con el papel y
no se pasa del 70%. La causa no era el lector. Era que **no había ningún modelo instalado**:

```
$ ollama list          (vacío)
$ du -sh /root/.ollama 32K
```

y el diagnóstico del propio sistema decía:

```json
{"configurado": {"modelo_texto": "moondream", "modelo_vision": "qwen2.5vl:3b"},
 "modelos_instalados": [], "faltan": [], "disponible": true}
```

**Tres defectos encadenados**, y cada uno escondía al siguiente:

**1. `GET /scan/ia` mentía.** Para Ollama, el cálculo de `faltan` estaba cortocircuitado con un
`pass` y su `else` —el único que hacía el cálculo— era **código muerto**. Y `disponible` usaba
`instalados is None or not error`, o sea **verdadero con la lista vacía**. El endpoint que existe
para decir *qué modelo falta* decía que no faltaba ninguno, y `EstadoDeIa` en el front **solo
aparece si hay algo que arreglar**.

Consecuencia medida: las fotos se leían **solo con Tesseract**, y el techo de exactitud del
sistema pasaba a ser el del OCR sin que ninguna pantalla lo dijera. El síntoma de un modelo
ausente es *"el sistema no lee bien los montos"*, que es indistinguible de un OCR malo.

**2. La cascada no consultaba al modelo aunque lo hubiera.** `IMAGENES_VISION_PRIMERO=false` y
`ESCALAR_A_IA_SIN_LINEAS=false` en el `.env`: si la lectura del OCR parecía plausible, se
devolvía sin preguntar a nadie. `219.93` parece plausible.

**3. Una foto que se contradecía a sí misma se guardaba.** MEDIDO con `qwen2.5vl:3b` sobre las 3
fotos rotuladas:

| foto | papel | OCR | visión | desglose del OCR | relee |
|---|---|---|---|---|---|
| `IMG_4398` | 193.65 | 193.65 | 193.65 | no | **no** |
| `IMG_4407` | 234.00 | 234 | 234.0 | no | **no** |
| `IMG_4409` | 279.93 | **219.93** | 279.93 | **sí, y no suma** | **sí** |

El OCR leyó **bien el subtotal** de la tercera (`279.93`, la verdad) y **mal el total**: el
documento se desmentía a sí mismo y el sistema tenía la contradicción a la vista. Se guardaba
`219.93` sin marca de error.

**Lo que quedó:** `_necesita_relectura` (`capture.py`) relee cuando no hay total **o cuando la
aritmética da `−1`**. El desempate entre lecturas lo hace `_mejor_de`, que ya existía, así que el
OCR no se tira: releer nunca es "replace".

**Costo medido:** el modelo se consulta **1 de 3** fotos. Las otras dos salen por el camino
barato (~81 s). Con visión primero serían 3 de 3 y 260 a 490 s **cada una**.

**Lo que NO está resuelto: el 500 intermitente de Ollama.** La llamada a visión devolvió **500 dos
veces seguidas** sobre `IMG_4409` y a la tercera funcionó y devolvió `279.93`. Sin saber por qué.
Lo que sí se arregló es poder saberlo: `raise_for_status()` solo lleva la URL, y ahora
`_ollama_chat_completion` registra el **cuerpo** del error antes de relanzar, porque el 500 de
Ollama puede ser contexto excedido, memoria, o el modelo que no cabe, y **el cuerpo lo dice y el
código no**.

> La máquina estaba con **12.6 GB de swap usados** y en un punto se le fueran **todos los
> contenedores** de Docker (`docker ps -a` vacío, volúmenes intactos). Se levantó con `make up` y
> los datos estaban enteros. No se descarta que el 500 y la perdida de contenedores tengan la misma causa: presión
> de memoria. **Queda abierto y medido**, no cerrado.

**Verificado por mutación:** cuatro mutaciones nuevas en `scripts/verify_capture_mutations.py`
(relectura de la foto contradictoria, releer sin desglose, `faltan` vacío, `disponible` sin modelo,
y el log del 500) — **36 mutaciones, 36 mueren**.

> **Dos tests de esta regla pasaron por el motivo equivocado antes de probar nada.** El primero
> corría con `ESCALAR_A_IA_SIN_LINEAS=True` —el default de `config.py`, no lo que dice el `.env`—,
> y entonces la cascada escalaba siempre y la regla no decidía nada: **quitar la regla no rompía
> ningún test**. El segundo usaba un proveedor inventado que disparaba `_nombre_parece_basura`, y
> entonces la lectura se declaraba "no útil" por otro motivo. Un test que pasa por una razón que
> no es la que dice no prueba lo que dice: parece cobertura.

---

### 45. `/expenses` era un router muerto con un `folder_path` del cliente — ✅ CORREGIDO

`app/routers/expenses.py` existía, **no estaba montado**, no tenía tests y hacía una
**segunda canalización de lectura**: OpenCV + Ollama directo + un Pydantic propio, con
dos TODO (`dedup` y `periodo cerrado`) que nunca se implementaron. Parecía una feature
y no podía cumplir nada de lo que prometía. Peor: su `batch_upload` aceptaba una
carpeta del cuerpo y hacía `Path().glob()` — la misma puerta de lectura arbitraria
por la que se borró `app/modules/expenses/`. **Un módulo muerto no se arregla, se borra
o se declara muerto**: montarlo habría abierto la puerta que por eso estaba muerta.

Ahora es un adaptador fino sobre `procesar_archivo`, el único camino de captura:

| Endpoint | Qué hace | Respuesta |
|---|---|---|
| `POST /expenses/upload-batch` | `files[]` + `company_id` | `list[ScanItemResponse]` |
| `POST /expenses/digitalizar-directorio` | `carpeta` opcional + `company_id` | `DigitalizacionDirectorioResponse` |

Los dos exigen `UsuarioActual`, comprueban la empresa (404 si no existe) y pasan por
el **mismo** `_validador_de_periodo`. `ExpenseReceiptSchema` sigue vivo como contrato
estrecho de la lectura de IA, **no como respuesta**: devolverlo aparte habría dejado
los números del papel separados del veredicto del gate, que es justo lo que el gate se
niega a afirmar.

**Cuatro defensas con mutación que muere al quitarlas:**

1. **Dedup por SHA-256, no por nombre.** `relative_path` se deriva de los bytes
   (`<sha>.<ext>`) tanto en la carga como en el directorio. Dos personas suben el
   mismo comprobante con distinto nombre → una sola fila en `scan_files`. El nombre
   solo conserva la extensión para que la clave siga diciendo qué tipo era.
2. **Periodo cerrado se decide DENTRO del pipeline**, justo después de leer la fecha
   del papel y antes de escribir el ticket. Si se validara en el router, el rechazo
   llegaría al cliente con el gasto **ya contabilizado** en un mes cerrado, y
   deshacerlo exigiría borrar un ticket con documento adjunto. La fila de
   `scan_files` queda `ERROR`, `resumen.accion = PERIODO_CERRADO`, el evento es
   `OMITIDO` con `PERIODO_CERRADO: <motivo>` en el detalle y el cliente ve el motivo.
   > El evento NO se llama `PERIODO_CERRADO`: `ck_scan_events_action` es una
   > constraint cerrada en la base y añadir un valor al DDL sin migrar deja el
   > `INSERT` rechazado en producción (precedente de `BORRADO` en `archivado_service`).
3. **Quien firma el override sale del token.** `autorizado_por = usuario.email`, nunca
   un campo del formulario (regla 23). Un solo callback compartido por las dos puertas
   evita que una se olvide del override o firme con otro nombre.
4. **Secuencial sobre una sola sesión.** El router anterior usaba `asyncio.gather`
   con semáforo de 3 sobre una `AsyncSession` compartida: no es concurrency-safe, y el
   `commit` de un archivo podía intercalarse con el `flush` de otro. La concurrencia
   se consigue con más workers, no compartiendo la sesión.

**La ruta escrita no abre el disco.** `digitalizacion_directorio.py` es un servicio
aparte porque `scan_service.escanear` guarda `relative_path` relativo a
`TICKETS_INPUT_DIR`: meter una ruta arbitraria haría colisionar `factura.pdf` de dos
carpetas y la segunda saldría "sin cambios" con bytes distintos. `resolver_carpeta`
comprueba `is_relative_to` **después** de `resolve()` contra
`raices_permitidas()` (`TICKETS_INPUT_DIR` + `TICKETS_SCAN_RUTAS_PERMITIDAS`,
separadas por `os.pathsep`); un `..` o un symlink a `/etc` no cuela. Los symlinks
dentro de la carpeta se reportan, no se leen. El tope por archivos y por bytes se
cuenta, no se silencia.

**Lo rechazado se nombra.** `archivos_no_permitidos` lleva nombre, extensión y motivo;
`archivos` solo contiene lo que se leyó. Un `.docx` mezclado en la tabla parecería un
ticket que falló, cuando es un archivo que nadie intentó leer. `nombres` y `formatos`
son arrays paralelos a `archivos` y se emparejan por índice en el front.

**`formatos` no es `origen`.** `formatos` es qué son los bytes (`pdf`, `image`, `xml`),
`origen` es quién leyó (`pdf_text`, `ocr`, `llm`, `xml`) y `modelo_ia` es el modelo
concreto. El `accept` del selector es comodidad: los bytes deciden el lector. El set
mide el caso que separa las tres preguntas: un `.jpg` con bytes de PDF da
`bytes=pdf · lector=texto del PDF`.

**El front no copia la configuración.** `GET /scan/config` expone
`rutas_permitidas` y `extensiones_permitidas`; `Scan.tsx` arma el `accept` y el
`datalist` con esas listas. Antes ofrecía `.txt`, que el backend rechaza, y las rutas
eran texto write-only. `npm run verify:lote` mira **el HTML renderizado**, no el retorno
de las funciones: 18 comprobaciones, y tres mutaciones verificadas a mano (quitar
`formatos`, sustituir el nombre del rechazado por texto genérico, vaciar el datalist)
mueren.

### 45.bis. El set de pruebas parecía medir OCR y no lo hacía

El set versionado en `test-files/set-digitalizacion/` se genera con
`scripts/generar_set_pruebas_digitalizacion.py` y se mide con
`scripts/inspeccionar_set_pruebas.py --con-ocr` (solo Tesseract, sin llamada de red).

La primera versión de `ideal/04_foto_requiere_ocr.jpg` tenía **0.12 %** de píxeles
oscuros. `tesseract` por línea de comandos la leía, pero `leer_imagen()` devolvía
`texto=''`: `ocr.py` descarta imágenes con menos de 0.5 % de tinta para no hacer 16
llamadas sobre una foto del techo. El set decía "foto con OCR" y el OCR no corría.

**Se arregló el artefacto, no la defensa.** 800×600 con fuente 22 da ~1.1 % de tinta y
`leer_imagen()` devuelve las seis líneas con confianza 0.9475. Bajar
`_FRACCION_OSCUROS_MINIMA` habría sido quitar una protección para que un test pasara.

Medido con `--con-ocr`: el PDF con capa de texto da `pdf_text` sin OCR ni modelo
(total 1100.00, fecha 2025-03-15); el XML por firma da `xml`; la extensión en
mayúsculas, el `.jpg` con bytes PDF, el `.HEIC` que es JPEG y la copia con otro nombre
se leen igual; `Sub-Total` no compite con `Total`; el PDF corrupto da `llm`, total
`0.00` y el error `Failed to load document (PDFium: Data format error).` visible.

### 45.ter. La colección de Insomnia mandaba `files` como texto

`scripts/generar_insomnia.py` reconocía solo `file` como campo de archivo.
`POST /expenses/upload-batch` declara `files: list[UploadFile]`, así que el request
exportado ponía el campo como texto y respondía **422**: el archivo parecía opcional
cuando FastAPI lo exige. `CAMPOS_ARCHIVO` incluye ahora `files` y el generador produce
87 peticiones en 16 carpetas con `files` como `type: file`.

> La regeneración se validó primero con `--spec /app/openapi-local.json --check`
> sobre el OpenAPI de `app.openapi()` porque `expense_api_service` estaba
> `unhealthy`. Cuando el servicio volvió a estar sano se corrió **`make
> insomnia-check` contra la API viva**: `expense-reconciler-insomnia.json esta al
> dia (87 peticiones, 16 carpetas)`. El archivo temporal `openapi-local.json` se
> borró; `--spec` no comprueba que el servidor sirva lo que dice, por eso valía
> la pena repetirlo contra el vivo.

**Verificación:** 7/7 mutaciones en `verify_expenses_batch_mutations.py`, 10/10 en
`verify_expenses_directorio_mutations.py`, 18/18 en `verify_scan_mutations.py` (con
`git` instalado en el contenedor y `core.excludesfile` apuntando al global del host),
y 3/3 en el front. Suite completa en verde: **1818 recolectados, 1815 passed, 3
skipped** con `git` instalado en el contenedor y `core.excludesfile` apuntando al
global del host. Sin `git`, esos 6 tests de `.gitignore`/lockfile fallan **por
entorno**, no por código: pasan en host con
`python3 -m pytest --noconftest tests/unit/test_lockfile.py
tests/unit/test_vscode_settings.py -q` (28 passed).

### 46. El Escáner decía "Error desconocido" ante un 500 en texto plano — ✅ CORREGIDO

**MEDIDO el 2026-11-20.** Al abrir **Escáner**, el cartel rojo decía `Error
desconocido` y dos peticiones a `GET /api/v1/scan/files` respondían 500. El mensaje
no era el bug: era el síntoma de dos defectos apilados.

**Causa 1 — la base en vivo venía de otra rama.** `schema_migrations` tenía
`0019_lotes.sql`, `0020_usuario_empresa.sql` y `0021_scan_files_status_revisar_foto.sql`,
pero **no** `0019_extraccion_multipaís_y_controles.sql`, que es la que este checkout
usa. Faltaban 11 columnas de `tickets` (`pais`, `folio`, `hash_duplicado`,
`estado_conciliacion`, `periodo_cerrado_*`, ...) y el SELECT del ORM moría con
`column tickets.pais does not exist`. La suite no lo ve: construye el esquema desde
los modelos, no desde la base que ya existe. **`init.sql` no se tocó**: ya declara
esas columnas; el problema era una base vieja, no el DDL.

**El arreglo manual no era aceptable.** Aplicar la migración con `psql -f ...`
desbloqueó los dos endpoints (pasaron a 200), pero arreglaba el contenedor, **no
el proyecto**: al recrearlo volvería a estar roto. La solución durable es el
servicio one-shot `migraciones` en `docker-compose.yml`: corre
`scripts/migrar_auto.sh` contra Postgres y la API lo espera con
`depends_on: service_completed_successfully`, igual que a `modelos`. Funciona con
`make up` y con `docker compose up` directo. El script crea `schema_migrations`
si no existe, aplica los `.sql` pendientes en orden (todos son aditivos e
idempotentes) y **registra después del `psql -f`**: si falla, `set -e` sale con 1
y la API no arranca. `scripts/migrar.sh` del host ya solo consulta (`--estado`)
o delega en el mismo servicio (`make migrar`); usuario y base salen de `.env`
(antes decía `USUARIO="postgres"`, un rol inexistente). Una sola implementación
del bucle: dos copias divergen y la del arranque es la que nadie mira.

> La validación del nombre de migración permite acentos a propósito: el archivo
> real se llama `0019_extraccion_multipaís_y_controles.sql`. Una validación ASCII
> lo rechazó en la primera corrida del servicio, `make up` falló y la API no
> arrancó. Sí se prohíben comillas y punto y coma, porque el nombre se pega al
> `INSERT`. 7 tests en `tests/unit/test_arranque_por_healthcheck.py`, con 5
> mutaciones verificadas.

**Causa 2 — el front no sabía mostrar un 500.** `fetchApi` hacía
`response.json().catch(() => ({detail: 'Error desconocido'}))`. Un 500 de uvicorn
responde `text/plain` con `Internal Server Error`, así que el parseo fallaba y el
`detail` inventado **reemplazaba** al `Error ${status}` que ya estaba calculado.
El operador veía un texto que no distinguía una caída del servidor de un fallo del
front, y la reacción era repetir la corrida.

Ahora se lee el cuerpo como texto y se intenta parsear como JSON solo después: si
no hay `detail`, el mensaje es `Error 500: Internal Server Error` (código **y**
cuerpo, con el texto acotado a 200 chars). La comprobación va en
`front/scripts/verificar_carga_lote.tsx` con un `fetch` falso; se verificó que
**muere** al revertir el arreglo (el mensaje vuelve a ser `Error desconocido`).

> Una base con migraciones de otra rama no se arregla borrando el cartel: el 500
> era verdadero. Y un mensaje que no dice el código ni el cuerpo convierte un fallo
> diagnosticable en un "no sé qué pasó" que invita a repetir la operación.

### 47. La caja chica existía en el SQL, pero no en el software — ✅ CORREGIDO

**MEDIDO el 2026-11-25.** El único artefacto del proyecto era
`db/migrations/0021_caja_chica_y_huella_fiscal.sql`: creaba `cajas_chicas`,
`movimientos_caja`, la columna `tickets.hash_duplicado` y el índice compuesto
`ix_tickets_company_fiscal_huella`. No había modelo, schema Pydantic, servicio,
endpoint, pantalla ni test. Una base nueva podía tener las tablas y la API seguir
sin saber que existían: el DDL solo no es una funcionalidad.

**Lo que se construyó**, en el orden en que el dato tiene que viajar:
`app/core/enums.py` → `app/models/caja_chica.py` → `app/schemas/caja_chica.py` →
`app/services/caja_chica_service.py` → `app/api/caja_chica.py`, más
`app/api/duplicados.py` sobre `duplicados_service.py`, registro de los dos routers
en `api_router.py` con token a nivel de router, y el espejo en `db/init.sql` para
que una base vacía no nazca sin columnas (`test_init_sql_espeja_los_modelos.py`
lo comprueba dentro del contenedor: 24 passed).

**El saldo no se calcula dos veces.** Se mueve con
`UPDATE ... WHERE saldo_actual + delta >= 0` en la misma transacción que el
movimiento. Dos gastos que leen el mismo saldo y escriben dos filas dejarían la
caja en negativo; con la condición en el `UPDATE`, la segunda no escribe y recibe
409 con los dos números. Postgres además toma `FOR UPDATE`; SQLite decide con la
condición, que es por lo que corre la suite. La comprobación previa en Python no
sustituye al cerrojo atómico: solo traduce el conflicto en un mensaje legible.

**La huella fiscal no es el hash del archivo.** Dos fotos del mismo papel tienen
bytes distintos, y el mismo archivo subido dos veces sí tiene el mismo hash. La
huella de negocio canónica es RFC + fecha + folio + total, normalizados, con la
empresa dentro. La búsqueda va por los **campos** del índice y no solo por
`hash_duplicado`: los tickets anteriores a la migración no lo traen, y un falso
negativo deja contar dos veces el comprobante sin aviso. Sin fecha o sin total no
se afirma duplicado; un falso positivo bloquea una carga válida y cuesta más.

**El resumen no suma filas.** `total_conciliado_sin_duplicados` cuenta una unidad
por huella y solo si esa unidad está conciliada; los tickets sin huella se cuentan
aparte. Sumar los `grupos` en el navegador duplicaría los reenvíos y contaría como
gasto un ticket que nadie concilió. Por la misma razón `saldo_bajo_umbral` es un
`@computed_field` (`<=`, no `<`) y el front solo lo lee: dos clientes calculando
el umbral por su cuenta son dos verdades. Los importes llegan como texto (`Decimal`)
y `dinero()` los formatea sin redondearlos de nuevo.

**El front también cayó en la trampa del HTML no ejecutado.** La primera versión de
`front/scripts/verificar_caja_en_pantalla.tsx` montaba la pantalla pero nunca
entraba a la pestaña de duplicados: `renderToStaticMarkup` no corre clics ni
`useEffect`, así que seis comprobaciones fallaban aunque la página estuviera bien.
La pantalla acepta ahora `pestanaInicial` **solo** para el verificador, igual que
ya hacía `Cierre` con `informeInicial`. Con eso pasan las 16 comprobaciones sobre
el HTML, y se verificó que mueren al quitar el aviso de umbral, al reemplazar el
total del backend por `0.00` y al ignorar `pestanaInicial`.

**Contratos que no son lo que parecen:** `CierreCajaRequest.closed_at` es opcional
(el servicio estampa `utcnow()` si no llega, aunque el formulario lo pide) y
`responsable_id` **no** es una firma: el front lo toma de la sesión, pero viaja en
el cuerpo porque es la persona a cuyo cargo se pone la caja. Quien firma los
movimientos sí es `created_by`, y sale del token (regla 23 de `AGENTS.md`).

**Verificación:** `tests/unit/test_caja_chica_y_duplicados.py` (17 tests) y la
suite completa en verde (**1889 passed, 1 skip**, 20 warnings, medido en local
con Python 3.14 y las variables del shells); `cd front && npm run build`,
`npm run verify:caja` (16/16) y
`npm run verify:fecha`. La colección de Insomnia se regeneró desde la API viva
(107 peticiones, 18 carpetas) y `generar_insomnia.py --check` está al día.

### 48. Tres pantallas, tres empresas distintas con el mismo token — ✅ CORREGIDO

**MEDIDO el 2026-11-25.** Cada pantalla tenía su propio `useState(companyId)` y
llamaba a `companiesApi.list()`: el Escáner ofrecía «Todas las empresas», Tickets
elegía la suya y Caja Chica otra. Con un mismo token, la interfaz podía defender
tres empresas a la vez; el servidor respondía 409 y ninguna pantalla decía cuál
era la elegida. `GET /companies` lista *todas* las empresas del sistema, así que
responder «qué hay» no puede responder «en cuál opera esta cuenta».

**Los roles viven en la BD, no en el JWT.** `0022_roles_y_empresa_activa.sql`
crea `usuarios_empresas(user_id, company_id, rol, activo)` con PK compuesta. Los
roles son `owner` · `admin` · `accountant` · `operator`; `perfil` y `grupo` son
texto libre y no saben *en qué empresa* valen, así que reutilizarlos habría
inventado autoridad donde solo hay una etiqueta. El backfill pone **admin en
todas las empresas** para que aplicar la migración no deje sin acceso a una
cuenta existente. `GET /api/v1/auth/empresas` devuelve las membresías activas
con su rol, leídas en cada petición: cambiar un rol surte efecto sin nuevo token,
igual que surte efecto dar de baja una cuenta (`get_current_user` no se tocó).

**`X-Company-ID` no es una sugerencia:** `app/core/deps.py:empresa_activa` la
comprueba contra las membresías de la BD antes de que corra el handler. UUID
inválido → 400; empresa ajena o sin membresía → 403; sin header y con varias
empresas → 409; un recurso de otra empresa → **404**, no 403, porque un 403
confirmaría que la caja existe en otro lado. Un `company_id` en el cuerpo o en
la query distinto del activo → 409. El query param se conserva como alias por
compatibilidad pero se **compara**, no se confía en él. Los routers de caja y
duplicados llevan `Depends(empresa_activa)` a nivel de router, no un `if` dentro
de cada endpoint.

**El alcance de `operator` se decide en el servicio.** Un operador solo ve y
mueve cajas donde `cajas_chicas.responsable_id == usuario.id`; el listado ya
viene filtrado y cada operación vuelve a comprobarlo. Meter ese filtro solo en el
router dejaría el camino del servicio abierto para el siguiente llamador. Y el
guardián del front (`rolPuedeCaja`) solo evita ofrecer una pantalla imposible:
la autoridad sigue siendo la BD, leída en cada request.

**La cabecera se inyecta en un solo sitio y al final.** `fetchApi` pone
`...cabeceraDeEmpresa()` **después** de `...(resto.headers)`: si una pantalla
manda su propio `X-Company-ID`, gana la empresa activa global. El contexto
`front/src/contextos/Empresa.tsx` es el único que elige; Escáner, Tickets y Caja
Chica comparten `AvisoSinEmpresa` en lugar de tres avisos que puedan divergir.
`npm run verify:empresa` (21 comprobaciones) monta el aviso en HTML —no mira el
retorno de una función— y comprueba el orden de inyección, que ninguna de las
tres pantallas decida `companyId` por su cuenta, que un fallo de red no borre la
última lista válida y que el guardián use el rol por empresa.

**Verificación:** `tests/unit/test_roles_y_empresa_activa.py`,
`test_auth_empresas.py`, `test_caja_chica_roles.py` y
`test_caja_chica_y_duplicados.py`; las 10 mutaciones de
`scripts/verify_roles_empresa_mutations.py` fueron detectadas. `NOMBRES` de
`generar_insomnia.py` incluye `GET /auth/empresas`: sin esa entrada la petición
nueva aparecía en la colección con el docstring como nombre, y el test
`test_no_falta_ninguna_entrada_en_nombres` lo cazó antes de regenerar.

### 49. No había dónde administrar usuarios ni roles por empresa — ✅ CORREGIDO

**MEDIDO el 2026-11-25.** El sistema ya tenía roles en la BD (§48) pero ninguna pantalla
para usarlos: `Login.tsx` decía que las cuentas las creaba quien administra la base, y el
menú se filtraba con `users.perfil`, un texto libre que no sabe en qué empresa vale. Un
administrador podía entrar a `/api/v1/auth/empresas` y ver los roles, pero no había forma
de crear una cuenta, darle acceso, cambiar su rol o reponer una clave olvidada sin tocar
Postgres.

**Dos decisiones, no una.** Alta de cuenta y acceso a una empresa son preguntas distintas:
`POST /usuarios` crea la cuenta; `POST /usuarios/membresias` le da un rol en la empresa
activa. La pantalla de Mantenimiento dice eso en el formulario ("crear la cuenta no da
acceso a ninguna empresa") porque si el alta diera `admin` por defecto, un descuido en el
alta seria un administrador nuevo. El `company_id` **no** va en el cuerpo: sale de
`X-Company-ID` y los schemas usan `extra="forbid"` para que reenviar la fila entera reciba
un 422 con el nombre del campo.

**El reset ajeno es otra ruta, no un permiso nuevo en la vieja.**
`POST /usuarios/{id}/contrasena` prueba dos cosas: que quien llama es quien dice ser
(header) y que conoce la clave de la cuenta afectada (cuerpo). Un administrador no conoce
la clave de otra persona y pedirla convertiria el reset en una adivinanza. Por eso existe
`POST /usuarios/{id}/contrasena-admin`: exige la clave **del administrador** en el header y
su rol en la empresa, nunca la clave objetivo, y no escribe la nueva en la auditoria. No
las sesiones conocidas de la cuenta quedan revocadas (`reset_administrativo`); un token
emitido pero nunca usado no se puede nombrar y vive hasta `exp`. La pantalla lo dice en vez
de prometer una revocacion total.

**La firma se inyecta donde no la puede falsificar una pantalla.**
`fetchApi` pone `X-Contrasena-Actual` **después** de `...(resto.headers)` y antes de
`cabeceraDeEmpresa()`. El mismo orden que `X-Company-ID`: un componente que mande su propio
header no puede declararse ya verificado ni cambiar de empresa. La clave se pide en cada
operacion, no se guarda en `localStorage`, y `verificar_mantenimiento.tsx` mide ese orden
leyendo `api.ts` dentro de `fetchApi` —fuera de esa funcion hay menciones en comentarios que
no inyectan nada—.

**La guardia del ultimo admin no es un aviso.** `membresias_service` cuenta los
`owner`/`admin` activos de la empresa y devuelve 409 antes de quitar el acceso o degradar
al ultimo. Un 409 aqui es la diferencia entre "no puedes" y "te quedas sin puerta de
entrada y hay que entrar a Postgres a mano". Las escrituras auditan con `usuario_id` del
token, `rol` leido de la BD, `registro_afectado` y el evento correspondiente; la clave
nunca entra en `detalles`.

**El menu es comodidad; la autoridad sigue en el servidor.**
`front/src/permisos.ts` es la unica matriz pagina × rol. `Mantenimiento` va al final del
recorrido aprobado y solo lo ve `owner`/`admin`; `accountant` no ve ni Mantenimiento ni Caja
chica, y `operator` ve Inicio, Escáner, Tickets y su Caja chica. `App.tsx` filtra el menu y
cierra la ruta escrita a mano con la misma funcion, sin esperar a que `cargandoEmpresas`
termine para sacar a alguien que si tiene acceso. La etiqueta del rol que ya existia en el
pie de la barra (`Rol en esta empresa: ${rol}`) se conservo: no se creo un componente nuevo
que compitiera con ella.

**Verificacion:** `tests/integration/test_mantenimiento_roles.py` (16 pruebas HTTP),
`npm run verify:menu` (13 comprobaciones) y `npm run verify:mantenimiento` (13
comprobaciones, una de ellas cae si la firma se inyecta antes de los headers sueltos o
despues de la empresa). `make insomnia` regenero la coleccion con las cuatro rutas nuevas
(107 peticiones, 18 carpetas): la primera corrida dejo dos tests en rojo porque
`NOMBRES` y `RUTAS_CON_CONTRASENA` son listas manuales —el OpenAPI no declara una
dependencia como parametro de operacion—, y los dos se arreglaron en el generador, no
editando el JSON. Los endpoints antiguos de `/usuarios` se dejaron intactos (§28).

---

### 50. El rol decidía el menú, pero no las acciones: `viewer` era imposible — ✅ CORREGIDO

**El defecto que se pide.** El usuario pidió un rol de solo lectura. Con el código anterior,
`PAGINAS_POR_ROL` decidía qué pantallas se veían, pero **no había forma de expresar "puede
ver esta pantalla y no puede pulsar nada"**. `accountant` veía Conciliación y por tanto
podía ejecutarla y descargar el Excel; `operator` veía Tickets y podía aprobar. Quitar una
pantalla del menú no cierra la ruta: con un token válido, `curl` llegaba igual. Añadir
`viewer` a la matriz de páginas habría producido exactamente lo que se pidió **en la
pantalla** y no en el sistema.

**La solución es una autoridad, no una lista de botones.** `app/core/permisos.py` traduce
el rol de `usuarios_empresas` a diez acciones y `guardia_de_permisos()` (`app/core/deps.py`)
se instala en `PROTEGIDOS` en `app/api/api_router.py`, junto al token. Todo endpoint nuevo
nace protegido sin que nadie recuerde añadirle un `Depends`. `auth` queda fuera: es la
puerta que todavía no tiene token. Los routers de caja/duplicados que ya tenían
`Depends(empresa_activa)` siguen comprobándolo en el servicio; la guardia no lo sustituye,
lo precede.

**Por qué el verbo HTTP NO pudo ser el criterio (mutación medida).** La primera idea —
`POST→crear`, `PATCH→editar`, `DELETE→borrar`, `GET→leer`— deja dos agujeros: `operator`
ya tiene `crear` y podría aprobar con el `PATCH /tickets/{id}/review` y ejecutar la
conciliación con el `POST /reconciliations/run`; y `GET /reports/cierre-mensual.pdf`
sería una lectura, así que `viewer` podría llevarse el PDF. `_permiso_de_la_peticion()`
clasifica primero por ruta y solo al final cae al verbo. `POST /duplicados/buscar` está
en `_RUTAS_DE_SOLO_LECTURA`: es una consulta, y exigirle `crear` rompería la búsqueda de
`viewer`. El verificador de mutaciones lo comprueba.

**El `X-Company-ID` basura devuelve 400 en la guardia.** No es cosmético: varios routers
(tickets, reconciliations, reports) no piden `empresa_activa`, así que una cabecera
inválida hacía que la guardia no encontrara roles y —según cómo se escribiera— podía
caer al conjunto vacío o dejar pasar la escritura. Se decide antes de mirar permisos.

**Roles: nadie pierde lo que ya hacía.** `owner`/`admin` conservan todo; `accountant`
todo menos `administrar`; `operator` `leer`/`crear`/`editar`/`cerrar`; `viewer` solo
`leer`. Un rol desconocido en la BD se descarta (`a_rol_de_empresa()` / `roles_validos()`),
nunca se vuelve admin, y las cuentas sin membresías siguen sin bloquearse por
compatibilidad con las anteriores a `0022`. `GET /auth/empresas` devuelve los permisos
calculados en servidor, en orden fijo. El front los usa **antes** que su matriz: probado
con un `owner` cuyo JSON dice `permisos: ['leer']`, que no ve Corregir ni Aprobar.

**DDL y menú.** `db/migrations/0024_rol_viewer_y_permisos.sql` amplía el `CHECK` y se
espeja en `init.sql`, que además registro hasta `0023` (faltaba en el seed de una base
nueva). `viewer` ve Inicio, Tickets, Cola, Muestreo, Banco, Conciliación, Cierre y
Auditoría, sin botones de firma/ejecución/descarga. `ORDEN_PAGINAS` no incluía
`cajachica` aunque la página existía: se añadió en su sitio del flujo. `rolPuedeCaja`
quedó sin callers y se borró; `verificar_menu_roles.tsx` comprueba que no vuelva.

**Verificación (y por qué hay tres verificadores de mutaciones distintos):**

| puerta | qué mide |
|---|---|
| `test_permisos_por_accion.py` (38) | mapa, clasificación por ruta, traducción de roles |
| `test_permisos_http.py` (7) | 403 por cable, con contraprueba de que `admin` sí entra |
| `verify_permisos_mutations.py` (12) | el mapa y la clasificacion |
| `verify_permisos_http_mutations.py` (4) | la guardia desconectada, el PDF como lectura, el header basura, el POST-consulta |
| `npm run verify:menu` | orden del menú y roles, incluido `viewer` |
| `npm run verify:permisos` | **HTML montado**: viewer ve datos y no ve botones; admin ve los botones |

`verify:permisos` monta `TicketEnCola`, `TarjetaMuestra` y `CajaChica` con el contexto que
devuelve `/auth/empresas`, y mira el HTML, no el retorno de `puede()`: `tsc` pasa con un
botón que no debiera estar. La contraprueba con `admin` evita el verde de una pantalla
vacía. `verify_permisos_front_mutations.py` (3) quita guardias del front y exige que
estos verificadores mueran: **uno de los tres sobrevivió en la primera corrida** porque
el test de `POST /duplicados/buscar` solo comprobaba `!= 400`, así que un 403 por la vía
equivocada daba verde. Se cambió por `== 200` y ahora muere.

**Lo que NO se hizo:** no se creó tabla `roles` ni panel de edición de permisos; son roles
fijos y el `CHECK` los declara. No se borró evidencia: no hay nuevo `DELETE`, y la baja
sigue siendo lógica. Los endpoints viejos de `/usuarios` se conservaron (§28).

**Efecto colateral medido en la base en vivo.** Al desplegar esto, la base tenía registradas
las migraciones hasta `0021` pero **no** tenía `usuarios_empresas` ni las columnas de `0023`.
Como la guardia consulta membresías en CADA escritura, `POST /auditoria/evento` empezó a
devolver 500 con `relation "usuarios_empresas" does not exist`; `GET /tickets` ya fallaba por
`column tickets.advertencia_duplicado_fiscal does not exist`. No era un bug del guardia: era
una base sin migrar, y el guardia lo hizo visible en la primera escritura en vez de dejarlo
oculto. `bash scripts/migrar.sh --estado` lo dijo y `make migrar MIGRAR_SI=1` aplicó `0022`,
`0023` y `0024`. Verificado después: `POST /auditoria/evento` → 204 y `GET /tickets` → 200.
Si vuelve a aparecer un 500 con `relation ... does not exist`, **no** se arregla relajando la
guardia: se arregla migrando. Relajarla daría 200 a una escritura sin saber qué rol la firma.

**El libro de migraciones de una base nueva también mentía (medido el 2026-11-26).** El seed de `db/init.sql` registraba `0020_auditoria_segura` sin el sufijo `.sql` y no registraba `0027_costo_del_kardex.sql`. Ninguna de las dos cosas rompía el arranque —ambas son idempotentes—, así que el servicio volvía a aplicarlas y `schema_migrations` quedaba con una fila que no corresponde a ningún archivo y sin la fila de un archivo que sí existe. Es el mismo género de defecto que el párrafo anterior: una base que dice estar al día cuando no lo está, solo que aquí la víctima es la base nueva. `test_el_seed_registra_cada_archivo_con_su_nombre_exacto` compara nombres exactos (no números) y `scripts/verify_registro_de_migraciones_mutations.py` (3 mutaciones, las 3 mueren) impide que el seed vuelva a recortar o omitir un nombre.

### 51. El logout era solo un aviso al navegador — ✅ CORREGIDO

**El defecto.** Un JWT HS256 firmado no se puede deshacer: `auth.py` lo decía en el
docstring y `Mantenimiento.tsx` lo decía en pantalla —"un token emitido antes del
reset sigue vivo 8 horas"—. Eso no era una advertencia callada: era la declaración
de que logout, cambio de clave y reset administrativo no revocaban nada. Faltaban
el `jti`, el sitio donde guardarlo y la comprobación en la puerta común. Además la
sesión nunca caducaba por inactividad: el único reloj era `exp`.

**La mitad de la base ya existía y sigue igual:**

| archivo | decisión |
|---|---|
| `db/migrations/0025_tokens_revocados.sql` | `jti` como PK, `user_id`, `motivo`, `revoked_at`, `expires_at`. `ON DELETE RESTRICT`: borrar la cuenta no borra la revocación. Append-only por trigger: borrar una fila reviviría un token robado. CHECK de cuatro motivos, repetido en `MOTIVOS_REVOCACION`. |
| `db/migrations/0026_auditoria_company_id.sql` | `auditoria.company_id` nullable con FK a `companies` e índice `(company_id, fecha DESC)`. Sin backfill: la tabla es append-only desde `0020` y un `UPDATE` revienta. `NULL` significa **"no se registró"**, no "todas las empresas". |
| `db/migrations/0028_sesiones_activas.sql` | `jti`, `user_id`, `emitida_at`, `expira_at`, `ultima_actividad`. Es estado operativo, no evidencia: se crea en la primera petición y se renueva en cada una. |
| `db/init.sql` | Espejo de las tres: DDL, índices, triggers y registro en `schema_migrations`. Una base nueva no nace rota. |

**La guardia.** `crear_token` emite `jti`; `get_current_user` consulta
`tokens_revocados` y `sesiones_activas` en cada petición y guarda el `jti` en
`request.state.jti` para que el logout sepa qué token cerrar. No se fía del
`company_id` ni del rol del cuerpo: siguen viniendo de la base y de la cabecera,
como antes. La caducidad por inactividad se mide desde `ultima_actividad` y no
desde `emitida_at`: con `SESSION_IDLE_MINUTES=30` una petición reciente renueva la
ventana; con `0` se desactiva. Al vencer, la fila **no se borra**: borrarla haría
que la petición siguiente la creara de nuevo y el token volviera a servir.

**Las tres trampas que se pagaron al cerrar el hueco:**

1. **No dejar al usuario sin token al desplegar.** Los tokens emitidos antes del
   `jti` siguen valiendo hasta `exp`. No se pueden revocar ni medir su inactividad
   porque no hay valor que nombrar; forzar su caducidad habría cerrado la sesión
   de quien estaba trabajando durante el despliegue. `Mantenimiento.tsx` lo dice
   en vez de prometer una revocación total.
2. **Las operaciones largas se autentican al empezar y se dejan terminar.** El
   escaneo con fotos tarda minutos; la guardia no vuelve a mirar el token a mitad
   de la respuesta, y el `catch` del front no apaga el canal de progreso. Un 401
   del propio logout tampoco se cuenta como "no se guardó nada": es el resultado
   buscado, por eso `fetchApi` acepta `sinAviso401`.
3. **«Revocar las sesiones» no significa «revocar todos los tokens».** Un token
   emitido pero nunca usado no tiene fila de actividad y no se puede nombrar.
   Cambio de clave, reset y baja revocan las sesiones **conocidas**; la baja además
   surte efecto inmediato porque `is_active` se lee en cada petición.

**El front。** `salir()` ya no se limita a borrar el `localStorage`: llama a
`POST /auth/logout` con el token todavía guardado y borra después, incluso si la
red falla. Un token copiado deja de servir en la siguiente petición aunque el
navegador que lo copió no se entere.

**Verificación:**

| puerta | resultado |
|---|---|
| `tests/integration/test_sesion_y_revocacion.py` | recorrido HTTP con la guardia real, incluido el token anterior al `jti` y el reloj deslizante |
| `tests/unit/test_revocacion_ddl.py`, `tests/unit/test_init_sql_espeja_los_modelos.py` | DDL, modelos y registro de migraciones |
| `scripts/verify_revocacion_mutations.py` (10) | esquema append-only, CHECK, FK y backfill prohibido |
| `scripts/verify_sesion_y_revocacion_mutations.py` (9) | quitar `jti`, relajar la revocación, borrar la fila vencida, medir desde `emitida_at`, logout mudo, apagar la inactividad o romper el espejo DDL |
| `npm run verify:sesion` y `scripts/verify_sesion_front_mutations.py` (4) | la petición que sale, el borrado posterior, el 401 propio y el token vencido |

SSO/MFA y la exportación de auditoría siguen sin existir: son los siguientes pasos
de la fase, con su propio turno.

---

### 52. El comprobante no se veía, y el nombre de archivo no abría nada — ✅ CORREGIDO

**Medido.** `ComprobanteAlLado` pedía **solo** `GET /tickets/{id}/documento` y decidía
por `blob.type`. El HEIC real de esta máquina (`IMG_4409.heic`, ticket
`d4c814b1-226b-4718-a497-8b24955d7923`) se sirve como
`application/octet-stream` —está fuera de la lista cerrada de tipos servibles a
propósito, porque ningún navegador de escritorio lo pinta—, así que caía en el
`<object>` y el recuadro de «Comprobante original» quedaba vacío. En Tickets, el
botón que decía *«Abrir … en otra pestaña (para verlo en grande)»* abría ese
mismo original: el navegador lo **descargaba** en vez de mostrarlo. `tsc` pasaba
con los dos defectos puestos: los dos son HTML, no tipos.

Y en «Registro de archivos» (Escáner), la columna **Archivo** era texto plano.
El único acceso al papel estaba en la última columna («Detalle»), sin ninguna
señal de que perteneciera a la fila que se estaba mirando.

**Lo que se arregló, y por qué no es relajar el allowlist:**

- `ComprobanteAlLado` pide ahora original **y vista previa** con
  `Promise.allSettled`, igual que `VerDocumento`. La previa JPEG se pinta; el
  original aporta el `Content-Disposition` (lo único que distingue un HEIC de un
  `.zip`, que también llega como `octet-stream`) y el enlace de descarga con su
  nombre. El tipo que decide **cómo se pinta** es el de la previa: con el del
  original el HEIC volvía al visor de PDF teniendo la foto al lado.
- «Ampliar» ocurre sobre el `Blob` ya descargado y con token: la misma imagen a
  pantalla casi completa, con acercar/alejar/ajustar, `Escape` para cerrar y
  `z-[60]` para quedar por encima del modal de edición (`z-50`). No se vuelve a
  pedir el papel ni se abre una pestaña sin `Authorization`. Se eliminó el botón
  de Tickets que prometía ampliar y descargaba el original.
- `nombreDelContentDisposition` se movió a `front/src/utils/documento.ts` y lo
  comparten los tres sitios que muestran el papel. Tres copias del lector del
  header es la forma de que una deje de entender `filename*` sin aviso.
- En Scan.tsx, la columna Archivo es un `<button>` con el **nombre de la imagen**
  (sin la carpeta) que abre el mismo detalle que «Detalle». La ruta completa se
  conserva en el `title` para no confundir dos archivos iguales en carpetas
  distintas. El botón no se deshabilita sin ticket: un archivo rechazado es justo
  el que alguien tiene que mirar.

**Contraprueba que costó un fallo:** la primera versión decidía `esHeic` mirando
el tipo de la imagen **pintada** (`image/jpeg` en la previa), así que el enlace
de descarga perdía el nombre original. El verificador la cazó. Ahora `esHeic` se
decide por el nombre del archivo, que es lo que identifica al original.

**Verificación:** `cd front && npm run build`, `npm run verify:comprobante`
(23 comprobaciones, miran el HTML montado con los datos del HEIC) y
`npm run verify:registro` (7 comprobaciones, monta la tabla con filas reales).
`npm run verify:papel` sigue pasando: la disposición del papel no cambió.
Seis mutaciones controladas, **6 de 6 mueren**: quitar la vista previa, pintar
con el tipo del original, quitar el control de acercar, usar un nombre de
descarga genérico, desconectar el botón del Archivo y mostrar la ruta entera en
la etiqueta.

---

### 53. El Escáner no contaba los duplicados, y los mutadores sin ejecutar parecían verdes

**MEDIDO el 2026-11-26.** `PanelStats` de `Scan.tsx` mostraba Archivos, Con ticket,
Con error y Sin ticket. Un duplicado no aparecía por ningún lado: el feed de la
corrida sí trae `duplicados`, pero ese número es por corrida y se pierde al cerrar
el panel.

`GET /scan/stats` ya tenía la verdad en `por_estado[DUPLICADO]`; solo faltaba
exponerla. Se agrega `archivos_duplicados` al schema y al panel con **la misma
consulta**, no con un contador propio: dos cuentas distintas del mismo hecho se
desincronizan. Un duplicado no es un `OMITIDO` cualquiera —la acción del feed lo
disfraza—, por eso se cuenta por `status` y no por la acción.

**Los verificadores que no corren también son deuda.** La línea base de los 22
mutadores tenía dos rojos que no eran defensas rotas sino verificadores
desactualizados: `auth` tenía 3 mutaciones supervivientes porque la puerta se
movió a la lista compartida `PROTEGIDOS`, y `capture` tenía 5 patrones
`NO PROBADA` porque el dia provisional paso a `fecha_ticket`/`fecha_provisional`
y el diagnóstico de IA se reescribió. Un patrón obsoleto se contaba como verde:
peor que un test rojo, porque nadie va a mirarlo.

`scripts/verificar_todas_las_mutaciones.py` descubre los 22 por glob, los corre
en secuencial (cada uno escribe el archivo original y lo restaura) y falla tanto
por código de salida como por marcas de "no se aplicó". `make verificar-defensas`
y `.github/workflows/defensas.yml` son la misma puerta. Añadir un mutador nuevo
entra en la corrida sin tocar la lista.

---

### 54. Bajar una empresa no era posible y borrarla borraba la evidencia — ✅ CORREGIDO

**MEDIDO el 2026-11-27.** `DELETE /companies/{id}` ofrecia en el confirm "se borraran
sus tickets y movimientos": las FK son `ON DELETE CASCADE`, y en Postgres eso no era una
amenaza retórica. No habia baja, no habia reactivacion, y el boton de eliminar estaba
siempre disponible. Las tres premisas del producto —empresa con movimiento se desactiva,
sin empresa activa no se procesa, el sistema no se queda sin empresa con datos— no tenian
ninguna representacion en el modelo.

`estado` sustituye al booleano `activo` (`activo`/`inactivo`/`eliminado`), con CHECK y
indice en `0029_estado_empresa.sql` y en `init.sql`. Tres transiciones, tres rutas;
`DELETE` es alias de `/eliminar` (soft delete). Los cerrojos estan en
`empresas_service.py`: cuentan filas de trabajo, no hijos ni catalogo sembrado. Con datos
no se elimina; la ultima activa con datos en el sistema no se desactiva; una eliminada
no se reactiva. Todo se audita con quien firma, sacado del token, y es idempotente.

**Cerrar la ultima empresa tenia que parar tambien las lecturas.** El primer bloqueo solo
miraba las escrituras, asi que un token con membresias cerradas seguia viendo tablero,
tickets e informes: la baja era decorativa. Ahora `guardia_de_permisos`, `empresa_activa`
y `empresa_visible` filtran por `CompanyModel.estado == 'activo'` y distinguen "membresias
todas cerradas" de "cuenta anterior a 0022 sin membresias" (esta ultima sigue viendo todo
por compatibilidad). `_es_recuperacion` deja abiertas exactamente las rutas que permiten
salir: login, `GET /auth/empresas`, listar/crear/activar empresas. Sin esa excepcion, el
cerrojo encerraba a la persona que tenia que reactivar la empresa.

`GET /companies` por omision solo activas; `?incluir_inactivas=true` es la via para
reactivar. El front lee el `estado` del servidor, ofrece "Mostrar inactivas" y recarga el
contexto de empresa despues de cada cambio, porque si no la barra lateral seguia creyendo
que la empresa reactivada no existia.

**La migracion no se pudo aplicar con `make migrar MIGRAR_SI=1` tal cual.** El shell tiene
`POSTGRES_PASSWORD` y `SECRET_KEY` exportados, y Compose los interpoló **antes** que
`.env.local`: el `psql` del servicio recibio la clave del shell y respondio
`password authentication failed for user "admin"`, aunque la clave del archivo era la de
la base. Se aplico con `env -u POSTGRES_PASSWORD -u SECRET_KEY make migrar MIGRAR_SI=1`.
Es la misma trampa que la de `OLLAMA_MODEL`, pero con secretos: no se arregla editando la
clave, sino quitando la variable del entorno del shell.

Verificacion: `tests/unit/test_empresas_service.py` (13),
`tests/integration/test_baja_de_empresas.py` (12, incluye `POST /scan` sin empresa y la
recuperacion), `scripts/verify_baja_empresas_mutations.py` (12 mutaciones, las 12 mueren)
y `npm run verify:empresa`. La coleccion de Insomnia se regenero contra la API viva:
121 peticiones, 19 carpetas.

---

## El cliente de IA vivo no tenia `chat_completion` (MEDIDO 2026-11-27)

Con `AI_ENABLED=true` en esta maquina, `AIExtractor._extract_from_text` y
`_extract_from_vision` llamaban a `ai_client.chat_completion(...)`, pero el
cliente module-level era un `OllamaVisionClient` que solo exponia
`extract_receipt_data`. Resultado: `AttributeError` **antes** de cualquier
peticion a Ollama. Ninguna lectura que escalara al modelo llegaba a contestar;
la cascada degradaba a OCR y el sistema parecia "que el modelo no lee".

Habia una segunda mitad que el primer sintoma tapaba: `AIExtractor` decide
que modelos pedir con `getattr(ai_client, "_provider", "openai")`. Sin el
atributo, pedia `gpt-4o-mini` / `gpt-4o` a un servidor Ollama aunque la
configuracion dijera `qwen2.5:3b` y `qwen2.5vl:3b`. Arreglar solo el metodo
habria dejado al modelo equivocado en la llamada.

Tercera mitad: el cliente se construia con `http://localhost:11434` escrito a
mano. Dentro del contenedor Ollama esta en `http://ollama:11434`, asi que
incluso con el metodo presente la peticion habia ido a un puerto que no es el
proveedor. `disponibilidad()` ya usaba la configuracion; el cliente que
realmente llama, no.

Lo que quedo:

- `OllamaVisionClient._provider = AIProvider.OLLAMA` y base_url/modelo leidos
  de `settings` al construirlo (con los literales anteriores como fallback).
- `chat_completion(...)` devuelve `AIResponse` (`.content`, `.model`,
  `.provider`), que es lo que `_parse_ai_response` ya esperaba. Traduce
  `response_format={"type":"json_object"}` a `format: "json"` de Ollama.
- `num_predict`, `temperature` y `keep_alive` salen de la llamada y de la
  configuracion; el timeout es `settings.OLLAMA_TIMEOUT`, no los 60 s del
  metodo heredado (una foto tarda mas que eso en 16 GB).
- El cuerpo de un 500 se registra con el mismo formato que en
  `AIClient._ollama_chat_completion`, para que "contexto agotado" no se
  confunda con "sin memoria".

`tests/unit/test_chat_completion_vivo.py` comprueba las tres mitades y el
payload real; sin el metodo, sin `_provider` o con la URL escrita a mano,
muere. El metodo antiguo `extract_receipt_data` sigue igual y sigue sin
llamadores: no es la ruta de la cascada.

**Lo que el arreglo destapo:** los tests de `file_type` pasaban porque la
llamada al modelo moria con `AttributeError` y `_cascada_texto` devolvia las
reglas. Con el cliente arreglado, `ESCALAR_A_IA_SIN_LINEAS` quedo en su default
de `config.py` (`true`, la politica de "pagar un modelo por las lineas") y los
PDF con texto salieron como `llm`: la regla 3 ("un PDF con texto no toca el
modelo") la estaba sosteniendo un bug, no una decision. La app corre con
`ESCALAR_A_IA_SIN_LINEAS=false`, y ahora esa decision esta escrita en `.env`
y en `.env.example`. Los tests unitarios la fijan con `monkeypatch`; los de
integracion dependen del entorno, y por eso el valor tiene que vivir en la
plantilla ademas de en la maquina.

---

## Arreglados en el pasado (no reabrir)
- **El trigger de stock negativo contaba con el signo equivocado**, y la defensa no
  bloqueaba la salida que dejaba el inventario en negativo — punto 26.
- **Funciones documentadas sin endpoint**: la cola de productos no se podia limpiar,
  `AJUSTE` no se podia escribir y una compra no se podia rechazar — punto 27.
- `PATCH /reconciliations/{id}` sin decir quien habia corregido el veredicto — punto 30.
- `verify_postgres_inventario.py` no se podia aplicar a una base ya creada — punto 29.
- **El cliente elegía la ruta de lectura** (`file_type` sin validar) — punto 17.
- **HEIC** con nombre de fallo en vez de basura silenciosa — punto 6.
- **Credenciales en el repo público** — `SECRET_KEY` y password de Postgres en `.env.dev`,
  `docker-compose.yml`, el README y 5 scripts de verificación, visibles desde el commit inicial.
  Rotadas, sacadas del código y del historial. Ver §15.
- La UI ofrece los dos caminos y el automático pasa por el gate — punto 14.
- `analitica.py` / `hallazgos.py` no estaban muertos: la premisa estaba mal — punto 13.
- `GET /reconciliations/mappings` devolvía 422 (orden de rutas) — punto 1.
- El frontend no guardaba el comprobante original — punto 2.
- Ruta única de captura (antes había varias y una foto podía volverse un ticket falso).
- Idempotencia por `(company_id, source_hash)`.
- Comprobante original guardado (0006).
- Muestreo con veredicto firmado por usuario.
- Defensa de fórmula XLSX + allowlist de codecs.
- Auth con scrypt + JWT HS256, rate limit, re-consulta de usuario.
