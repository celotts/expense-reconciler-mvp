# La ruta de captura

Cómo un archivo se convierte en un ticket. Es el subsistema más complejo del proyecto y
el que más caro sale entender mal, porque reparte la decisión entre cinco archivos.

**Regla de oro:** `capture.py` decide **cómo se lee**. `confidence_gate.py` decide **si se
aprueba**. Son dos veredictos distintos y ninguno puede suplantar al otro.

---

## Mapa de responsabilidades

| Archivo | Rol | NO hace |
|---|---|---|
| `app/api/tickets.py:43` `_extract_from_upload` | Traduce excepción → HTTP 400 | No decide nada |
| `app/services/capture.py:263` `capture_ticket` | **Único punto que decide** la estrategia de lectura | No aprueba ni rechaza |
| `app/services/parser_service.py` | Lectura determinista: regex y pdfplumber | No usa IA |
| `app/services/ai_extractor.py:109` `AIExtractor` | Traduce bytes → campos | No juzga |
| `app/services/confidence_gate.py:92` | Checks + umbrales → veredicto | No lee archivos |
| `app/services/document_service.py:91` | Guarda los bytes originales | No aprueba |

Si alguna vez tienes dudas de "quién decide esto", la respuesta casi siempre es
`capture_ticket`. Los demás son piezas que él invoca.

---

## Los cuatro escalones

`capture_ticket` es una cascada. El orden **es** la lógica, no un detalle:

```
file_type == "image"?          → _desde_imagen()      # visión
file_type == "text"?           → _cascada_texto()      # reglas, sin IA
file_type != "pdf"?            → ExtractionUnavailable → HTTP 400

try:  texto = extract_pdf_text()        # pdfplumber
except:                        → _vision_pdf()        # escaneado o corrupto
if len(texto) < 40:            → _vision_pdf()        # hay imagen, no texto
                               → _cascada_texto()     # PDF con capa de texto
```

Constantes de decisión (`capture.py:89-100`):

| Constante | Valor | Por qué |
|---|---|---|
| `PDF_MIN_CHARS_PARA_INTENTAR` | 40 | Debajo de esto no hay texto que parsear |
| `PDF_MAX_PAGINAS_A_VISION` | 3 | Un PDF de 100 páginas no debe gastar 100 llamadas al modelo |
| `PDF_ESCALA_RENDER` | 2.0 | Legibilidad vs tamaño de imagen |

### El escalón que más se malinterpreta

En un PDF **con texto**, el modelo **no se llama**:

1. Se parsea con regex (`_parse_receipt_text`).
2. Si sale algo útil (`provider_name` real y `total > 0`), **se devuelve eso y se acaba**.
3. Solo si las reglas no sirven, se pregunta al LLM.

Es lo correcto: el LLM es más caro, más lento y menos determinista que un regex. El
orden pone lo barato y verificable primero. Hay una mutación que lo mata si alguien lo
invierte (`verify_capture_mutations.py`, "un PDF con texto se manda a visión igualmente").

### Cuando sí entra la IA

- **Imagen suelta** → siempre visión.
- **PDF escaneado** → se renderizan hasta 3 páginas a JPEG y se manda cada una. Si varias
  páginas sirven, gana la de **mayor monto**; la confianza **no** sube por eso
  (`capture.py:436-438`): es lectura entre varias, no lectura verificada.
- **PDF con texto donde el regex falló** → el texto completo al LLM, recortado a 8 000 chars.

---

## El confidence gate

Después de leer, `validate_extraction()` corre seis checks **en este orden**:

| # | Check | Falla si |
|---|---|---|
| 1 | `provider_missing` | vacío, espacios, o `== "Unknown Provider"` |
| 2 | `total_not_positive` | `None` o `<= 0` |
| 3 | `tax_exceeds_total` / `tax_negative` | IVA > total, o < 0 |
| 4 | `subtotal_plus_tax_mismatch` | `abs(subtotal + tax - total) > 0.01` |
| 5 | `malformed_rfc` | no cumple `^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$` |
| 6 | `date_missing` / `date_in_future` / `date_too_old` | nula, > hoy+2d, o < hoy−3 años |

**El orden no es arbitrario.** El total va antes que el IVA porque el check 3 compara
contra él. La aritmética va antes que RFC y fecha porque es el único check que no depende
de nada externo: si un total malformado la cortocircuitara, los otros cinco no se correrían.

### El invariante central

```
checks OK + confianza >= 0.90  → AUTO_APROBADO
checks OK + confianza <  0.90  → REQUIERE_REVISION
checks con fallo bloqueante    → PENDIENTE
checks con fallo no bloqueante → REQUIERE_REVISION
```

> **La confianza alta nunca compensa un check roto.**
> Un 0.99 sobre una aritmética imposible va a revisión. Está en
> `confidence_gate.py:170-175` y hay un test que lo fija.

Solo tres checks son bloqueantes (`provider_missing`, `total_not_positive`, `date_missing`).
Los otros mandan a revisión humana en vez de a `PENDIENTE`.

### De dónde sale la confianza

| Ruta | Cómo se calcula |
|---|---|
| **Reglas** (PDF con texto) | Tabla `CONFIANZA_POR_CAMPOS` (`capture.py:123-133`): 8 combinaciones escritas a mano según qué campos se leyeron (RFC / subtotal / fecha). Rango 0.86 → 0.97. |
| **IA** (visión) | La que devuelve el modelo, acotada a `[0,1]` y redondeada a 3 decimales. |

De la tabla de reglas se deduce algo útil: **sin RFC, el techo es 0.88, así que nunca
auto-aprueba.** Para auto-aprobarse por reglas hacen falta RFC + (subtotal o fecha).

`MANUAL` nunca persiste confianza: `persisted_confidence` devuelve `None`
(`confidence_gate.py:64-75`). Por eso un ticket manual válido sale `APROBADO`, nunca
`AUTO_APROBADO`.

---

## Idempotencia: la misma foto dos veces

```
sha256(bytes originales)  →  tickets.source_hash
```

Se calcula sobre los **bytes**, no sobre el texto parseado. Antes de insertar, se busca
`WHERE source_hash = :h AND company_id = :c`. Si existe, se devuelve el ticket existente.

**Por qué el `company_id` va en la clave** (y no solo el hash): el SHA-256 no lleva la
empresa dentro. El mismo comprobante subido por dos empresas distintas es un caso
legítimo; con unicidad solo por hash, la segunda empresa recibía el ticket de la primera.
Índice único parcial: `UNIQUE (company_id, source_hash) WHERE source_hash IS NOT NULL`
— los `NULL` de la captura manual no colisionan.

Ese mismo hash sirve para el **muestreo**: `int(source_hash, 16) % 10000 < 500` selecciona
el 5%. Es determinista y reproducible, así que la muestra no cambia entre ejecuciones.

---

## Persistencia: el gate no puede ser falseado

Este es el punto donde un cliente podría intentar aprobarse un ticket solo. Está
defendido en dos capas, y **las dos hacen falta**:

**Capa 1 — el schema descarta lo que no conoce.**
`TicketBase` no declara `model_config`, así que rige `extra='ignore'`. Si el cliente manda
`{"extraction_status": "AUTO_APROBADO", "confidence": 0.99}`, esos campos no llegan al
`model_dump()`.

**Capa 2 — el gate escribe después.**
```python
ticket = TicketModel(
    **data.model_dump(),                      # el body, ya filtrado
    extraction_status=decision.status.value,   # el gate, después
    confidence_source=decision.confidence_source.value,
    confidence=decision.persisted_confidence,
)
```

**Por qué no basta una.** Si `model_dump()` *sí* trajera `extraction_status`, esto no sería
"el gate pisa al cliente": sería `TypeError: got multiple values for keyword argument`, es
decir un 500. `extra='ignore'` evita el 500; el orden es la barrera de fondo.

Cubierto por `tests/integration/test_veredicto_forjado.py` (6 tests) y con mutaciones en
`verify_auth_mutations.py`.

---

## Límites, para saber cuándo el gate va a rechazar

| Límite | Valor | Dónde |
|---|---|---|
| Tamaño de comprobante | 10 MB | `subida.py:58` |
| Tamaño de CSV bancario | 25 MB | `subida.py:64` |
| Lectura por trozos | 64 KiB | `subida.py:68` |
| `raw_text` en BD | 20 000 chars (12k cabeza + 8k cola) | `parser_service.py:194` |
| Texto enviado al LLM | 8 000 chars | `ai_extractor.py:169` |
| Imagen a visión | reescalada a 1600 px, JPEG q85 | `ai_extractor.py:143` |
| Páginas a visión | 3 | `capture.py:96` |

`leer_con_limite` lee por trozos y **suelta lo leído** al pasarse: el pico de memoria es
el tope, no el archivo.

Cuando el PDF se trunca, el `raw_text` incluye un marcador
`[… N caracteres omitidos: …]` para que un recorte sea buscable con `LIKE`.

---

## Lo que el pipeline **no** cubre

Ver `docs/known-issues.md` para el detalle. Lo esencial para no perder tiempo:

- **HEIC/TIFF**: sin `pillow-heif`, PIL no abre un HEIC. Y si llegara por otro camino, se
  declara `image/jpeg` a un modelo que no lo decodifica → basura silenciosa.
- **PDF cifrado**: cae a visión, sale como "ilegible" sin decir que falta la contraseña.
- **Varios archivos**: `file: UploadFile` es singular. `SourceType.BULK`/`DIRECTORY`
  existen en el enum y no tienen endpoint.
- **12 campos que se extraen y se tiran**: `invoice_number`, `currency`, `exchange_rate`,
  `payment_method`, `items`… se piden al modelo, se parsean a `Decimal` y se descartan.
  El export CONTPAQI los sustituye por valores inventados (`export_service.py:310-327`).

---

## Si vas a tocar esto

```bash
python3 -m pytest tests/unit/test_capture.py tests/unit/test_confidence_gate.py -q
python3 scripts/verify_capture_mutations.py     # 26 mutaciones
python3 scripts/verify_postgres_capture.py      # contra Postgres real
```

**No cambies el orden de los checks del gate ni el orden de la cascada sin una razón
escrita.** Ambos están sostenidos por tests que existen precisamente porque alguien los
cambió alguna vez y pasó algo undesirable. El porqué está en los comentarios del código,
 junto a cada decisión.
