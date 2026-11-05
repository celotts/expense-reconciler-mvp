# Prompt de desarrollo — los seis "plus"

**Uso.** Este archivo es el material de entrega, no una especificación de producto. Para trabajar
cada mejora se pega **el preámbulo entero** (§0) y luego **una sola tarea** (P0..P6), en una
sesión por tarea. No se pegan dos tareas juntas: cada una deja la puerta verde, y una sesión que
implementa dos deja dos mitades sin verificar.

**Orden.** P0 → P1..P4 (P1 y P2 son independientes entre sí) → P5 → P6. P0 es precondición: sin
ella la suite no está verde y ningún "terminado" es creíble.

**Si algo de este documento contradice al código, el código gana: avisa y actualiza este archivo.**
Lo mismo que hace `AGENTS.md`.

---

## §0 — PREÁMBULO (pegar en cada sesión)

Eres un desarrollador trabajando en **Expense Reconciler MVP** en
`/Users/carloslott/develop/python/expense-reconciler-mvp`. Antes de escribir una línea, lee
`AGENTS.md` y `docs/known-issues.md` enteros: son la fuente de verdad sobre el estado real del
proyecto. Este preámbulo **no** los sustituye; los resume para no empezar a ciegas.

### 0.1 Qué es el proyecto

Herramienta **local y privada** (una máquina, un contador, sin multi-tenancy) para freelancers y
pequeños comercios mexicanos: se digitaliza un comprobante, se extraen los datos, se concilian
contra el extracto bancario y se exporta a Excel/CONTPAQI. La filosofía que gobierna todas las
decisiones es: **el automatismo nunca afirma más de lo que puede sostener, y lo que no sabe lo
dice como "no disponible" en vez de inventarlo.**

### 0.2 Comandos — úsalos exactamente así

```bash
make up          # SIEMPRE este, nunca `docker compose up`. Reconstruye y espera (--wait)
make logs
make stats       # RAM/CPU vs cuota

python3 -m pytest tests/ -q          # ~1760 tests. Tarda ~2 min.
python3 -m pytest tests/unit -q
python3 -m pytest tests/integration -q
```

> `pytest.ini` usa `--import-mode=importlib` porque hay dos `test_reporte_cierre.py`
> (unit/ e integration/) y el nombre lo fija `docs/contrato-producto.md` §5.3. No lo cambies.

**La puerta del front no es `npm run verify`.** Ese comando falla siempre por `verify_login.ts`
(`known-issues.md` §33, preexistente). Lo que sirve es:

```bash
cd front && npm run build    # corre tsc: es la puerta real
npm run verify:aviso         # el aviso de formato
npm run verify:fecha         # la fecha corrida un día, en 3 pantallas
```

**Si añades rutas nuevas al API:**

```bash
make insomnia && make insomnia-check   # sale 1 si la colección quedó desfasada
```

### 0.3 Reglas que NO se rompen (cada una tiene test que muere si la quitas)

1. **La confianza alta nunca compensa un check rojo.** `decide_status()` evalúa los checks antes
   que la confianza. Un 0.99 sobre aritmética imposible va a revisión.
2. **El gate escribe después del `model_dump()`.** Los campos del veredicto van explícitos después
   de `**data.model_dump()`. `TicketBase` usa `extra='ignore'`.
3. **Un PDF con texto no toca el modelo.** Y el `file_type` del cliente **nunca** elige la ruta de
   lectura: el formato sale de los bytes (`app/core/archivo_real.py`).
4. **El OCR tiene su propia tabla de confianza**, más baja. `ConfidenceSource.OCR` es un valor
   aparte, no una variante de `LLM`: el reporte agrupa por origen y mezclar dos lectores da un
   número que no describe a ninguno.
5. **La ruta de la carpeta se configura; no se pide.** No hay endpoint, parámetro ni body que
   acepte una ruta: `{"folder_path": "/"}` sería lectura arbitraria del disco. La contención se
   comprueba con `is_relative_to` **después** de `resolve()`.
6. **La idempotencia del escáner usa `scan_files`, no `tickets.source_hash`.**
7. **Un ticket con `reviewed_at` no se sobreescribe automáticamente**, ni con `reprocesar=True`.
8. **El idempotente es `(company_id, source_hash)`.** El SHA-256 no lleva empresa dentro.
9. **El usuario se re-consulta en la BD en cada request.** Los claims no bastan.
10. **El rate limit va antes de gastar scrypt.**
11. **En XLSX solo `forzar_texto`; el apóstrofo es para CSV.**
12. **Allowlist de encodings**, no denylist.
13. **El `content_type` que se sirve no es el que declaró el cliente.** Lista cerrada de 7 tipos.
14. **La aritmética del documento se comprueba antes que RFC y fecha**, y con dos sumas posibles
    (`subtotal + IVA == total` y, si hay IEPS, `subtotal + IVA + IEPS == total`).
15. **Una relectura escribe lo que leyó, también cuando no leyó nada.**
16. **`SECRET_KEY` >= 32 caracteres, sin comodines.** `CORS_ORIGINS` no admite `"*"` con
    `allow_credentials=True`.
17. **scrypt con los parámetros de OWASP** y el formato lleva sus propios parámetros.
18. **El comprobante digitalizado NO SE ALTERA: se apila.** `INSERT` con `reemplaza_a`; un trigger
    de Postgres prohíbe `UPDATE` siempre y `DELETE` mientras el ticket exista.
19. **La fecha del comprobante se lee junto al folio**, no en cualquier línea.
20. **El signo del kardex: `SALIDA` resta y nada más.** `TipoMovimiento.suma_stock` es la única
    fuente de verdad y el trigger de Postgres la replica en SQL.
21. **Una `ENTRADA` del kardex solo viene de `confirmar_compra`, o de un `AJUSTE`.**
22. **Un `PATCH` usa `exclude_unset`, nunca `exclude_none`.**
23. **Quien firma sale del token, nunca del cuerpo.**
24. **Dar de baja ≠ borrar**, en `productos` y en `users`.

### 0.4 Trampas verificadas que ya costaron tiempo (no las vuelvas a visitar)

- **Orden de rutas en FastAPI: el primer match gana.** Las rutas literales van **antes** de
  cualquier `/{id}`. Solo colisionan las de un solo segmento (`/mappings`), no las de dos
  (`/export/excel`). En `reconciliations.py` `GET /{id}` está al final **a propósito**.
- **El texto del OCR conserva las líneas.** Si se une palabra con palabra, el parser no encuentra
  nada y el ticket sale vacío **con la confianza más alta de la cascada**: el fallo más caro del
  proyecto, y no se manifiesta como error.
- **El piso de caracteres del OCR es 60.** El ticket más chico real mide 112.
- **`pytesseract` no es el binario.** El Dockerfile instala `tesseract-ocr` y `tesseract-ocr-spa`.
- **Nada de OCR se importa al arrancar.** `easyocr` arrastra ~2 GB.
- **El router del escáner es `app/api/scans.py`**, no `scan.py`.
- **La persistencia vive en `services/ticket_persistence.py`.** `app/api/tickets.py:_persist_extracted`
  es una delegación.
- **`decimales` son `Decimal`, nunca `float`.** La columna JSON es `JSONConDecimal`
  (`app/core/json_decimal.py`), recursiva, a texto. No definas `json_encoder` global.
- **`db/init.sql` y los modelos divergen y el test verde no lo ve.** Si añades una columna:
  migración **y** `init.sql`. `test_init_sql_espeja_los_modelos.py` ya existe.
- **El falso positivo de una defensa silenciosa:** un test que mira el archivo entero en vez de
  la receta, y un `except: pass` que convierte un 500 en un dato inventado.

### 0.5 Defectos ABIERTOS que esta rama debe cerrar antes de empezar (ver P0)

Están medidos hoy y son precondición:

- `tests/integration/test_reporte_cierre.py::TestA2ElTotalCuadra` → **2 fallos**.
  `reporte_cierre` calcula "hoy" con `analitica.utcnow().date()` (UTC) y el test con
  `date.today()` (local). Entre las 18:00 y las 24:00 de México los dos discrepan.
- `ruff check app/services/capture.py` → **B033**: `"transferencia"` duplicado en
  `PALABRAS_DE_ENCABEZADO`.
- `capture._nombre_parece_basura` declara **basura cualquier razón social con puntos**:
  `OXXO S.A. DE C.V.`, `WALMART DE MEXICO S.A.B. DE C.V.`, `GRUPO COMERCIAL DSW S.A. DE C.V.`
  son `True` porque `S.A. DE C.V.` se parte en letras sueltas `s, a, de, c, v`. Consecuencia
  medida: un **PDF con capa de texto escala al modelo** (viola la regla 3), y con visión primero
  una lectura correcta de visión (`1000.00`) se reemplaza por la del OCR (`1100.00`).

### 0.6 Cómo trabaja este proyecto (esto es lo que hace falta que respetes)

- **Comentarios y docstrings en español.** En el código, **sin tildes**; en los `.md`, con tildes.
- **Todo importe es `Decimal`**, `model_dump()` no `dict()`, `ConfigDict` no `class Config`,
  `UUID(as_uuid=True)` + `uuid.uuid4()`, `back_populates` en los dos lados.
- **La lógica de negocio va en `app/services/`;** los routers son adaptadores finos.
- **Ningún cambio sin su test.** Y no un test que pase con el cambio quitado: un test que
  **muere** si se revierte. Ese es el criterio de "terminado" de este repo.
- **Verificación por mutación.** Si tocas una defensa, corre su verificador y actualízalo:

```bash
python3 scripts/verify_scan_mutations.py            python3 scripts/verify_capture_mutations.py
python3 scripts/verify_export_mutations.py          python3 scripts/verify_auth_mutations.py
python3 scripts/verify_documentos_mutations.py      python3 scripts/verify_reconciliation_mutations.py
python3 scripts/verify_spot_check_mutations.py      python3 scripts/verify_reporte_mutations.py
python3 scripts/verify_vscode_mutations.py          python3 scripts/verify_ocr_mutations.py
python3 scripts/verify_fecha_mutations.py           python3 scripts/verify_postgres_inventario.py
```

  Si refactorizas código que un verificador muta, **reapunta la ruta**: un verificador que no se
  aplica y sigue contando es peor que uno que no existe.
- **Un verificador que no se aplica cuenta mentira.** Al añadir una mutación nueva, comprueba
  que el texto que busca **existe** en el archivo; si un refactor movió el borde de una etiqueta,
  el patrón literal ya no coincide y la defensa queda **no verificada en silencio**.
- **Documento la medición, no la intuición.** Si dices "mejora", dime con qué script, sobre qué
  corpus y qué número antes/después. Un número sin fecha y sin forma de repetirlo es una trampa
  (ya pasó: 81 s por foto que eran 21 s, y un 80% de exactitud que no se podía reproducir).
- **Actualiza `AGENTS.md` y `docs/known-issues.md` en el MISMO commit** de cada cambio de esquema,
  API, pipeline o gate. Y añade tu sección a `docs/known-issues.md` con la medición.
- **Si añades una defensa, añade su verificador de mutación y di cuántas mutaciones mueren.**
- **Si una parte del trabajo resulta inalcanzable, dilo y márcala en `known-issues.md`.** No la
  dejes escrita como si existiera. La documentación desincronizada del código es peor que no
  tenerla.

### 0.7 Definición de terminado (no la rebajes)

- [ ] `python3 -m pytest tests/ -q` en verde, **cero regresión** sobre la línea base.
- [ ] `ruff check` sin errores **en los archivos que tocaste**.
- [ ] `cd front && npm run build` verde **si tocaste el front**.
- [ ] La defensa nueva tiene su test que **muere** si la quitas; lo verificaste revirtiéndola.
- [ ] El verificador de mutación correspondiente corre y sale con el número esperado.
- [ ] `AGENTS.md` y `docs/known-issues.md` actualizados en el mismo commit.
- [ ] Si añadiste rutas: `make insomnia && make insomnia-check` en verde.
- [ ] Si tocaste esquema: migración numerada **y** `init.sql`, con el test en verde.
- [ ] Si un verificador quedó obsoleto, está actualizado y lo dices en el commit.

**No empieces si la suite no está verde.** Si encuentras algo rojo que no es tuyo, repórtalo en
`known-issues.md` con su medición y avisa antes de seguir.

---

## P0 — Dejar la puerta verde

**Objetivo.** Que `make up` arranque limpio y que la suite pase hoy, con un solo "hoy", y que una
razón social mexicana normal no se clasifique como basura.

**Por qué antes que nada.** Los tres defectos son precondición de P1: el XML produce nombres con
`S.A. DE C.V.`, y sin puerta verde no hay forma de distinguir "arreglé algo" de "rompí algo".

### Tarea 0.1 — `S.A. DE C.V.` no es basura

- `_tokens_de` quita la puntuación antes de decidir, así que la información del sufijo legal ya
  se perdió. Se recupera **antes** de tokenizar: quitar el sufijo legal
  (`S.A. DE C.V.`, `S.A.B. DE C.V.`, `S.A. DE C.V.`, `S. DE R.L.`, `S.C.`, `S. en C.`, `A.C.`
  y sus variantes con y sin puntos) del nombre, y evaluar el resto.
- `OXXO S.A. DE C.V.` → `OXXO`. Cuidado: `OXXO` tiene cuatro letras, no cae en
  `all(len(t) <= 3)`, así que no se convierte en falso positivo. Pero `ABC S.A. DE C.V.` → `ABC`
  **sí** cae, y ese es el comportamiento correcto.
- **La lista de ruido NO se afloja.** La excepción es para el sufijo legal, no para aceptar más
  letras sueltas. `UN POLE Tag mA ee ki`, `gt Edson Nte hunera 1235`, `AAA AI`, `CANT. TOTAL`
  siguen siendo basura.
- Tests: en `TestElNombreDelProveedor`, añade a la lista de "comercio de verdad" **las formas con
  puntos** (`OXXO S.A. DE C.V.`, `WALMART DE MEXICO S.A.B. DE C.V.`, `GRUPO COMERCIAL DSW S.A.
  DE C.V.`) y a la de ruido los casos que ahora no mueren.
- Verificación por mutación: quita el borrado del sufijo legal y comprueba que mueren los nuevos.

### Tarea 0.2 — Un solo "hoy" para el informe

- `reporte_cierre` recibe `hoy` de `analitica.utcnow().date()`; los tests usan `date.today()`.
  La decisión correcta es que **la fecha de negocio es local**: un periodista fiscal cierra octubre
  a las 20:43 del 31 de octubre, no el 1 de noviembre en UTC.
- Consecuencia: `construir_reporte` acepta `hoy` (ya lo acepta, es inyectable por determinismo) y
  el router pasa la fecha local del negocio. El determinismo del PDF **no se toca**: el
  `set_creation_date` ya lo fija la versión nueva de `fpdf2`.
- El defecto real que hay que arreglar es **la comparación de dos "hoy" distintos**, no el test.
  `date.today()` y `utcnow()` no pueden estar en dos lados de la misma comparación.
- Test: fija `hoy` explícitamente y comprueba los dos casos de borde: 23:59 del último día del mes
  (el periodo sigue abierto) y 00:01 del día siguiente (ya cerrado). **No uses la fecha real del
  sistema**: un test que depende del día en que corre miente 30 días de 31 (ya pasó aquí).

### Tarea 0.3 — `ruff` limpio

- `"transferencia"` duplicado en `PALABRAS_DE_ENCABEZADO` (`capture.py:146`).

**Criterio de aceptación.** Suite verde sin `xfail` ni `skip` nuevos; `ruff check` limpio en los
archivos tocados; `test_capture.py` cubre las formas con puntos; la medición del defecto 0.2 está
en `known-issues.md` con su sección nueva.

---

## P1 — El CFDI como fuente, no como foto

**Objetivo.** Que cuando el comprobante **es** un CFDI (XML), el sistema lea la fuente exacta en
vez de mirar una foto de un papel que ya tiene el dato en forma legal, y que ese dato sea la llave
que impide contar dos veces la misma factura.

**Problema que esto arregla (está en el repo).** `invoice_number`, `currency`, `exchange_rate`,
`payment_method` y seis campos más se extraen y se tiran; el export CONTPAQI manda
`"TipoCambio": "1.0000"` y `"MetodoPago": "PUE"` inventados (`export_service.py:310-327`, y el
contrato R6 lo declara como dato ausente). Y el dedupe por `source_hash` **no ve** que la misma
factura llegó dos veces: la foto de WhatsApp y el PDF del correo son bytes distintos → dos tickets
→ gasto contado dos veces, sin ningún error en pantalla.

### Alcance

1. **Detección por bytes.** Firma nueva en `app/core/archivo_real.py`. El XML de CFDI empieza con
   `<?xml` o `<!DOCTYPE`; se comprueba como hacen los demás (lista cerrada, no extensión). El
   cliente no decide: la regla 3 manda.
2. **`file_type=xml` en `capture_ticket`.** Ruta propia: sin OCR, sin modelo, sin render. Si el
   XML parsea, `confidence_source` es un **valor nuevo `XML`** en `ConfidenceSource`, por la misma
   razón que `OCR` es aparte de `LLM` (el reporte agrupa por origen y mezclar lectores da un
   número que no describe a ninguno).
3. **Campos a extraer** (CFDI 4.0):
   - `Complemento.TimbreFiscalDigital@UUID` → **folio fiscal**, la llave real.
   - Emisor: `RfcEmisor`, `NombreEmisor`, `RegimenFiscalEmisor`.
   - Receptor: `RfcReceptor`, `NombreReceptor`, `RegimenFiscalReceptor`, `CodigoPostalReceptor`.
   - Generales: `FechaExpedicion`, `LugarExpedicion`, `MetodoPago` (PUE/PPD), `FormaPago`,
     `UsoCFDI`, `SubTotal`, `Total`, `Moneda`, `TipoCambio`, `TotalImpuestos`.
   - `Conceptos` → `items` (el modelo ya tiene `interpretar_items`).
   - `Complemento.Pagos.Pagos.DoctoRelacionado` → REP: `IdDocumento` (UUID del PPD),
     `Parcialidad`, `ImpSaldoAnt`, `ImpPagado`, `ImpSaldoInsoluto`.
4. **Deduplicado por UUID, además del hash.** Índice único parcial
   `WHERE cfdi_uuid IS NOT NULL` **por empresa**. El segundo archivo con el mismo UUID se resuelve
   como `DUPLICADO` apuntando al ticket existente, igual que hace hoy `source_hash`. La regla 8
   se **extiende**, no se rompe.
5. **Un REP no es un gasto nuevo.** Un complemento de pago de un PPD se **enlaza** al CFDI del
   gasto (`cfdi_relacionado`), no genera un ticket de gasto. Si lo generara, el periodo contaría
   el mismo gasto N veces.
6. **El export deja de inventar** `TipoCambio` y `MetodoPago` cuando vienen del XML. Si no vienen,
   siguen siendo "no disponible" (R6, R7), **no** `"1.0000"`.
7. **`scan_service`** acepta XML además de PDF/imagen: hoy marca `NO_SOPORTADO` a todo lo que no
   sea PDF con firma de imagen.

### Defensas que tienen que morir (y cómo verificarlas)

| Defensa | Mutación | Test que muere |
|---|---|---|
| Un XML válido no llama al modelo | inyecta un `extract_from_image` que revienta | el de "el PDF con texto no toca el modelo", versión XML |
| El mismo UUID no crea un segundo ticket | quita el índice único / el chequeo | integration de dedupe |
| El REP no suma total de gasto del periodo | haz que el REP cree un ticket de gasto | el de cierre con REP |
| El formato sale de los bytes | manda `file_type="image"` con XML | el de "el file_type del cliente no elige la ruta" |

### Lo que NO se afirma

- Que el XML sustituya a la foto. Para un ticket de `La Esquina` no hay XML, y ahí sigue mandando
  la foto con su confianza medida. Es **un escalón más**, no un reemplazo.
- Que el CFDI se pueda validar contra el SAT. Requiere CSD y red: eso rompe "local y privado".
  Fuera de alcance, y dilo en `known-issues.md`.

**Criterio de aceptación.** Un CFDI real (trae un XML de ejemplo al `tests/fixtures/`, sin datos
de personas reales) produce un ticket con UUID, RFC, régimen, CP, UsoCFDI, MétodoPago e items, y
su segundo archivo no crea otro ticket. Migración **y** `init.sql`. `AGENTS.md` con la sección
nueva.

---

## P2 — El banco como segunda verdad

**Objetivo.** Que el sistema conteste la pregunta que nadie se está haciendo: **¿qué pagué y no
tengo comprobante?**, y que use lo que el banco ya sabe para contradecir lo que la foto dice.

**Problema.** La conciliación es **unidireccional** (comprobante → banco). Nadie mira hacia atrás.
El resultado es silencioso y caro: el dinero salió de la cuenta y nunca se digitalizó el papel, y
eso es un gasto **no deducible** que el contador descubre en enero.

### Alcance

1. **`GET /api/v1/bank-transactions/sin-comprobante?company_id=&desde=&hasta=`** → movimientos no
   conciliados, ordenados por fecha y monto. Literal **antes** de `/{bank_transaction_id}`.
2. **Cotejo de proveedor.** El concepto del banco (`PAGO OXXO SA DE CV`) tokenizado contra
   `provider_name` del comprobante. Si el banco nombra un comercio y el comprobante dice otro, es
   una señal que hoy se pierde. **Se reporta; no se escribe en el ticket** (el campo `provider_name`
   es lo que la lectura dijo, y eso no lo pisa nadie).
3. **La fecha del banco como ventana, no como verdad.** El banco **no** corrige
   `expense_date` (regla 15). Sirve para acotar la cola: un `expense_date` nueve meses lejos de su
   movimiento bancario es una hipótesis de fecha mal leída, y eso sí se reporta.

### Lo que NO se afirma (esto es lo importante de la fase)

- **No se corrige ningún monto.** Un gasto pagado en dos transferencias es legítimo; un ticket
  pagado junto con otro en una sola transferencia también. Corregir el monto con el del banco
  produciría datos **inventados con forma de correcto**, que es el peor modo de fallo de este repo.
  Se produce una **hipótesis con discrepancia**, y esa es la salida honesta.
- **No hay endpoint que reciba una ruta** (regla 5). El banco se lee del CSV/import, que ya existe.
- No se marca `is_reconciled` por la reversa: marcarlo sería fingir que el gasto tiene comprobante.

### Defensas que tienen que morir

| Defensa | Mutación | Test que muere |
|---|---|---|
| La reversa no muta montos ni fechas de tickets | deja que escriba `total_amount` | el integration de la reversa |
| Marca solo movimientos realmente no conciliados | incluye los conciliados | el de conteo |
| No hay forma de pasarle una carpeta | postea un body con `folder_path` | el de la regla 5 |

**Criterio de aceptación.** Con una BD de prueba con tres movimientos y un solo ticket
reconciliado, la ruta devuelve exactamente los dos movimientos libres, y ningún ticket cambia su
`total_amount` ni su `expense_date` después de llamarla.

---

## P3 — La memoria de proveedores aprendida de las correcciones

**Objetivo.** Que la primera vez que alguien corrige un proveedor mal leído, el sistema lo
recuerde **para esa empresa**, con el nivel de soporte medido.

**Problema.** `VendorNormalizer` es código muerto (su único caller era el `pipeline.py` muerto) y
"Oxxo", "OXXO" y "OXXO EXPRESS" son tres proveedores distintos en el dashboard. Pero el proyecto
tiene algo que ningún normalizador heurístico tiene: `reviewed_by`, `reviewed_at`,
`confidence_source=manual`, `review_notes` y `spot_check_wrong_fields`. **Cada corrección humana
ya es dato etiquetado que hoy se tira.**

**Y no se revive `VendorNormalizer`.** El repo lo prohíbe explícitamente. Lo que se construye es
otra cosa: un mapa **aprendido** de correcciones reales, no reglas escritas a mano.

### Alcance

1. **Tabla nueva** `alias_proveedores`: `company_id`, `alias_normalizado`,
   `proveedor_canonico`, `soporte` (cuántas veces un humano lo corrigió), `rfc_asociado`,
   `creado_por`, `created_at`. Índice único `(company_id, alias_normalizado)`.
2. **Se alimenta SOLO de correcciones humanas** (`review_ticket`, `PATCH /tickets/{id}`, la cola
   de revisión, y el spot-check). **Nunca** de una lectura automática. Esa es la línea que separa
   "recuerda" de "se equivoca más rápido": un alias que se alimenta del OCR se auto-alimenta de
   sus propios errores.
3. **Se aplica con dos condiciones**, no una:
   - el alias tiene `soporte` >= umbral configurable, y
   - si el comprobante **trae RFC**, el RFC del alias coincide. Si no coincide, **no se aplica**.
   La segunda condición es la defensa: sin ella, un alias de OXXO se aplicaría a cualquier ticket
   cuyo primer renglón se pareciera a OXXO.
4. **La exactitud medida no puede bajar.** Se reporta con `GET /tickets/accuracy` agrupado por
   origen, que es donde se ve.

### Lo que NO se afirma

- Que suba la exactitud. **Primero se mide** (P4), y solo entonces se declara un número.
- Que normalize fuera de la empresa. Un alias es de una empresa; moverlo a otra sería escribir
  en el kardex de otro (la última línea de defensa multiempresa que ya existe en los servicios).

### Defensas que tienen que morir

| Defensa | Mutación | Test que muere |
|---|---|---|
| El alias no se alimenta de lecturas automáticas | deja que `_persist_extracted` escriba | el de alimentación |
| Exige `soporte` mínimo | baja el umbral a 1 y escribe un alias con un solo respaldo | el de soporte |
| Con RFC presente, el RFC tiene que coincidir | quita la comprobación | el de RFC |

**Criterio de aceptación.** Un ticket con proveedor basura corregido a mano produce un alias; un
segundo ticket con el mismo ruido y el **mismo RFC** lo resuelve solo; y con un RFC **distinto**,
no lo toca.

---

## P4 — Cerrar el círculo de la exactitud

**Objetivo.** Que cada corrección humana alimente la medida de exactitud, para que el 98.5% deje
de ser un número que el propio script dice que no se puede afirmar con 5 fotos.

**Problema (dicho por el repo).** El muestreo del 5% entra **solo** sobre `AUTO_APROBADO`; un
ticket de OCR nunca llega ahí, así que `GET /tickets/accuracy` no puede decir nada sobre las fotos.
Y `verdad.json` vive fuera del repo y se llena a mano. La frase del propio archivo: *«con 5 fotos
esto no es una medida, es una anécdota»*.

### Alcance

1. **Tabla `verdad_humana`**: `ticket_id`, `campo`, `valor_leido`, `valor_verdad`,
   `origen_lector` (`ocr`/`llm`/`pdf_text`/`xml`), `model_name`, `revisado_por`, `revisado_at`.
   `model_name` ya se persiste, y esa es la columna que permite un A/B honesto entre motores.
2. **Se alimenta de las correcciones que ya ocurren.** El spot-check de hoy deja
   `spot_check_wrong_fields` como **texto**; no lo dupliques: se deriva de ahí, o se migra. Decide
   y documenta cuál.
3. **`scripts/medir_precision_ocr.py` acepta la BD** además del `verdad.json` externo. El script
   actual corre dentro del contenedor y necesita Tesseract; la ruta nueva es contra la tabla.
4. **`GET /tickets/accuracy` agrupado por `model_name`**, no solo por origen. Hoy un A/B entre
   `qwen2.5:3b` y otro modelo es indistinguible.

### Lo que NO se afirma

- Que el sistema produzca la verdad. **La verdad la escribe una persona mirando el papel**, siempre.
  No hay endpoint que escriba verdad automáticamente: si lo hubiera, el script mediría que el OCR
  coincide consigo mismo.
- Que con esto se pueda afirmar un intervalo de confianza. Para eso hacen falta cientos de
  rotulaciones; el script ya lo avisa y **no le quites ese aviso**.

### Defensas que tienen que morir

| Defensa | Mutación | Test que muere |
|---|---|---|
| La verdad no la escribe el sistema | añade un endpoint de escritura automática | el de "la verdad la escribe una persona" |
| El spot-check alimenta la verdad sin duplicarla | escribe dos fuentes distintas | el de consistencia |

**Criterio de aceptación.** Corregir tres campos de un ticket produce tres filas de verdad con su
lector y su modelo; el script de medición las lee desde la BD y reporta el mismo número que con
el `verdad.json`.

---

## P5 — Expediente fiscal por comprobante (auditoría)

**Objetivo.** Que la cadena de custodia que este proyecto construyó a propósito tenga **salida**.
Hoy existe entera y no se exporta: `ticket_documents` es `append-only` por trigger, hay cinco
columnas que guardan quién firmó (`confirmada_por`, `revisado_por`, `cerrado_por`,
`autorizado_por`, `reviewed_by`), `auditoria` registra cada acción con actor y rol, y cada versión
de documento guarda hash y `reemplaza_a`.

### Alcance

1. **`GET /api/v1/reports/expediente-fiscal?company_id=&periodo=`** → PDF, y su JSON para
   preview. Salen del **mismo objeto** (`ReporteCierreMensual` es el precedente).
2. **Contenido por comprobante**: el documento vigente con su versión, hash, `reemplaza_a`,
   `actor` y `motivo`; la confianza, el origen y el modelo que leyó; `reviewed_by/at`; la
   conciliación (`matched_at`, estado, discrepancia); la compra (`confirmada_por`,
   `confirmada_at`, estado); y su fila de `auditoria`.
3. **Mismas reglas que el informe de cierre**: determinismo **byte-idéntico** (R4),
   `fecha_referencia` y no `emitido_en`, fuentes latin-1, y `bytes(pdf.output())`.
4. **`no disponible` en vez de vacío** (R7), y ningún dato recalculado en la maquetación.

### Lo que NO se afirma

- Que sustituye al contador. Es **evidencia**, no dictamen.
- Que valida contra el SAT (mismo motivo que P1: CSD y red).

### Defensas que tienen que morir

| Defensa | Mutación | Test que muere |
|---|---|---|
| Byte-idéntico | quita `set_creation_date` | el de determinismo |
| Sale del mismo objeto, no recalcula | recalcula en el maquetador | el de "el PDF no recalcula" |
| `null` → "no disponible", nunca `0` | pon un `0.00` en faltante | el de R7 |

**Criterio de aceptación.** Dos descargas del mismo periodo son byte-idénticas, y el expediente
de un comprobante con dos versiones de documento muestra **ambas** con su `reemplaza_a`, nunca
solo la última.

---

## P6 — Cierre anual y lista de bloqueos

**Objetivo.** Que el sistema conteste «¿qué me impide cerrar el ejercicio?» con una lista, no con
un booleano.

### Alcance

1. **Periodo `YYYY`**, con el **mismo mecanismo** que el mensual (`validar_periodo`,
   `@computed_field`, `fecha_referencia`, sin `emitido_en`, `CERRADO_CON_PENDIENTES`). No se
   duplica la máquina de estados.
2. **Sección de bloqueos detallada**: «ene: 14 sin clasificar, 3 sin comprobante, 22 en
   discrepancia, 1 compra `EN_REVISION`». El `puede_cerrarse` de hoy es un booleano; un contador
   quiere la lista y su detalle navegable.
3. **Comparativo anual** con la misma cautela que el mensual: un mes en curso **no** es
   concluyente, y lo dice (`conclusivo=False` + `nota`).

### Lo que NO se afirma

- Que un ejercicio cerrado es un ejercicio declarado. Es el mismo criterio que ya tiene el cierre
  mensual.
- Que se puede declarar cerrado con discrepancias abiertas.

### Defensas que tienen que morir

| Defensa | Mutación | Test que muere |
|---|---|---|
| `CERRADO_CON_PENDIENTES` no se reporta como "no cierra" | fusiona los estados | el del cuarto estado |
| Un mes en curso no es concluyente | quita `conclusivo=False` | el de comparativo |
| El PDF no recalcula | recalcula el total en la maquetación | el de "un objeto, dos salidas" |

**Criterio de aceptación.** Un periodo con una discrepancia abierta produce un PDF con
`CERRADO_CON_PENDIENTES`, la lista de bloqueos con sus motivos, y el mismo JSON que ve el preview.

---

## Cierre

Después de cada fase, y **en el mismo commit**: `AGENTS.md` (estado real, comandos, reglas), la
sección nueva de `known-issues.md` con la medición, y los verificadores de mutación actualizados
si refactorizaste algo que mutan. Si una parte de la fase quedó sin terminar, se marca en
`known-issues.md` con su motivo — no se documenta como si existiera.
