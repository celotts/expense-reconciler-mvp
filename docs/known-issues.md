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

---

## 🟠 Trampas

### 3. `app/modules/expenses/` — muerto **y** roto. No lo montes.
- Su router **no está** en `api_router.py` → los 6 endpoints no existen.
- `crud.py:136` referencia `SourceType.AUTO`, que **no existe** en el enum.
- `pipeline.py:224-229` usa `select`/`and_` sin importar.
- **Si lo montas tal cual:** `batch_upload` (`router.py:41-45`) acepta un `folder_path: str`
  **del cliente** y hace `Path(folder_path).glob()` → lectura arbitraria de directorios del
  servidor. Ninguno de sus 6 endpoints pide `get_current_user`.

### 4. Sin multi-tenancy: cualquier usuario autenticado toca cualquier empresa
`get_current_user` no recibe `company_id` ni hay scoping por empresa. Con un token válido,
`GET /tickets?company_id=<ajena>` lee y `PATCH`/`DELETE` escriben empresas ajenas.
Es aceptable para "una máquina, un contador"; **no** lo es si esto se vuelve multiusuario.
Decisión de producto pendiente, pero documéntala antes de que alguien la descubra tarde.

### 5. El export CONTPAQI inventa datos
`export_service.py:310-327` manda literales fijos: `Serie=""`, `Folio=""`, `Moneda="MXN"`,
`TipoCambio="1.0000"`, `MetodoPago="PUE"`, `TipoComprobante="I"`, `UsoCFDI="G03"`.
Y **12 campos que sí se extraen se tiran** (`invoice_number`, `invoice_series`, `currency`,
`exchange_rate`, `payment_method`, `items`, + `provider_address`, `receiver_*`, `due_date`,
`tax_breakdown`, `payment_terms`). Un comprobante en USD sale con tipo de cambio 1.0000,
**sin marca de error**. Para un contador, un tipo de cambio falso es peor que un campo vacío.

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

### 8. Un archivo por subida; sin carga masiva
Los 3 endpoints de archivo declaran `file: UploadFile` **en singular** (`tickets.py:120,136,1031`).
Los `SourceType.BULK` / `DIRECTORY` / `CAMERA` del enum no tienen endpoint. La idempotencia
por hash ya está lista, pero nada la llama en lote.

### 9. `init.sql` y los modelos divergen (el test verde no lo ve)
- `bank_transactions.company_id`: nullable en `init.sql:126`, `NOT NULL` en
  `models/bank_transaction.py:24`.
- `users.email`: el modelo pide `unique+index` sobre la columna cruda
  (`models/user.py:26`); prod usa índice **funcional** `lower(email)` (`init.sql:32`).
Los tests corren sobre SQLite **construido desde los modelos**, así que no detectan
ninguna de las dos. Si alguna vez se genera el esquema desde el modelo, no coincide con prod.

### 10. `REVIEW_CONFIDENCE = 0.60` no hace nada
`confidence_gate.py:198-203`: las dos ramas (`>= 0.60` y el `else`) producen
`REQUIERE_REVISION`; solo cambia la etiqueta (`confidence_medium` vs `confidence_low`).
Si alguien lo sube esperando un cambio de comportamiento, no lo habrá. Cosmético hoy,
confuso mañana.

### 11. `text` no existe en el enum `SourceType`
`file_type=text` funciona en la cascada (`capture.py:292`) pero al persistir cae al fallback
y se guarda como `source_type="image"` (`tests/integration/test_capture_pipeline.py:102-116`).

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

### 19. `easyocr` está soportado pero no instalado, y su confianza no está normalizada
`ocr.py:_leer_easyocr` existe y `GET /scan/ocr` lo reporta como no disponible con su
instrucción, pero `easyocr` **no** está en `requirements.txt`: arrastra torch (~2 GB) y
descarga ~100 MB de pesos la primera vez, y el repositorio ya tiene una regla sobre no
arrastrar torch al arrancar. Igual con `opencv-python` (el preprocesado usa solo Pillow)
y `pdf2image` (el repo ya usa `pypdfium2`, que además llega con pdfplumber).

Riesgo concreto sin reproducir: EasyOCR devuelve la confianza por línea en `[0, 1]`
pero hay versiones que la devuelven en `[0, 100]`, y el código la usa tal cual, así que
`confianza_media` podría valer `87.0`. **No afecta al gate** (esa confianza la calcula
`confianza_por_campos_ocr` a partir de la evidencia), así que el daño es acotado a un
campo informativo. Cuando se agregue easyocr, normalizar por el máximo observado.

### 20. `TICKETS_INPUT_DIR` por omisión es una ruta de una máquina concreta
`config.py` usa `/Users/carloslott/Documents/Tickets_app`, que es lo que pide el
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
`~/Documents/Tickets_app`, rotuladas a mano mirando el papel:**

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

### 25. Cuatro archivos `.env` y el mismo secreto en varios — ✅ CORREGIDO
`.env` (600) · `.env.dev` (**644**) · `.env.local` (**644**) · `.env.example` (versionado).

- **La contraseña de la base estaba en `.env.dev`**, dentro de `DATABASE_URL`, con permisos de
  lectura para el grupo. Docker la sobrescribe con la de `POSTGRES_PASSWORD`, así que la de
  `.env.dev` no se usaba: era un segundo lugar donde vive el mismo secreto. Quitada.
- **`.env.dev` y `.env.local` en 644.** Con una sesión abierta en la máquina, cualquier otro
  usuario las lee. Ahora en 600.
- **`config.py` leía solo `.env.dev`** mientras `docker-compose` leía los dos. La
  `SECRET_KEY` de `.env.local` solo valía dentro de Docker: `uvicorn` en local firmaba con
  otra, y el síntoma era "la sesión se cae, pero solo cuando depuro". Ahora los dos leen los
  dos en el mismo orden.
- **Los comentarios mentían.** Los dos archivos decían que la clave "mide EXACTAMENTE 32
  caracteres" (mide 64) y que `.env.dev` "SÍ está versionado en git" (está en `.gitignore`).
  Un comentario que afirma lo contrario de lo que se hace se lee más rápido que el código.

**Pendiente, y es lo único de esta sección que no se puede arreglar en el código: rotar
`SECRET_KEY` y `POSTGRES_PASSWORD`.** Los archivos están fuera de git, pero §15 dice que el
repo estuvo público, y una clave que estuvo expuesta sirve hasta que se rota, no hasta que se
borra del archivo.

---

## 🟡 Deuda / data quality

### 12. `VendorNormalizer` nunca se ejecuta
Solo lo referencia el `pipeline.py` muerto. "Oxxo", "OXXO" y "OXXO EXPRESS" son **tres
proveedores** distintos en el dashboard. El normalizador (`ai_client.py:246-350`) está escrito
y listo; solo falta llamarlo desde la ruta viva.

### 13. ~~`analitica.py` / `hallazgos.py` no están expuestos~~ — ✅ La premisa estaba mal
Son **servicios**, no routers. Que no aparezcan en `api_router.py` es lo correcto: se invocan
desde `app/api/dashboard.py:52` y `dashboard.py:368`, y ese router sí está montado. Alimentan el
landing del dashboard y el front los pinta (`Dashboard.tsx:585-604`).

Lo que sí conviene verificar es lo contrario: que un módulo de `services/` tenga a alguien que lo
llame. `VendorNormalizer` (§12) es el contraejemplo — instanciado en `ai_client.py:355` y con un
único caller en el `pipeline.py` muerto.

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

---

## Arreglados en el pasado (no reabrir)
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
