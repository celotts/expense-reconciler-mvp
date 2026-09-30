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
