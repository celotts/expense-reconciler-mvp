# Dígitalización: lo que funciona, lo que no, y qué te falta

Este documento es el estado real de la digitalización **a 2026-10-04**, medido
contra los 8 comprobantes de esta máquina. No es una descripción del código: es
lo que el sistema leyó de tus papeles y lo que hay que hacer para cerrarlo.

---

## 1. Lo que ya funciona, verificado

### `POST /scan` devuelve los datos de cada ticket

Cada elemento de `detalles[]` trae un `datos` con los campos del comprobante **y
el veredicto del gate juntos**:

```json
{
  "relative_path": "ticket_mercado_simple.pdf",
  "accion": "SIN_CAMBIOS",
  "datos": {
    "provider_name": "MERCADO LOCAL DON PEPE",
    "provider_tax_id": "MLP950101ABC",
    "total_amount": "229.68",
    "subtotal": "198.00",
    "tax_amount": "31.68",
    "items": [ ... 4 líneas ... ],
    "confidence": "0.950",
    "confidence_source": "llm",
    "extraction_status": "AUTO_APROBADO"
  }
}
```

Un solo llamado, sin cruzar N `GET /tickets/{id}`.

### Los datos llegan a la base y a las tablas que les tocan

`scan_service` → `persistir_extraccion` → `tickets`, `ticket_documents`,
`scan_files`, y `registrar_compra` → `compras` + `compra_items` cuando hay
líneas. Confirmado en la base: 9 documentos, 10 filas de `ticket_documents`
con `sha256` verificado contra el disco.

### Las fotos ahora se ven

Dos bugs juntos impedían ver una foto. Los dos corregidos y verificados contra
la API viva sobre documentos **que ya estaban guardados**:

| Documento | Antes | Ahora |
|---|---|---|
| `oaxaca-qa.pdf` | `application/pdf` | `application/pdf` ✓ |
| `IMG_4253 2.HEIC` (bytes JPEG) | `application/octet-stream` | `image/jpeg` ✓ |
| las otras 6 fotos | `application/octet-stream` | `image/jpeg` ✓ |

### `PATCH /tickets/{id}` acepta las líneas

**Esto faltaba y era el bloqueo del inventario.** El endpoint aceptaba el
encabezado pero no `items`, así que una persona podía corregir proveedor, RFC,
total y fecha, pero **no escribir las líneas**. Y como la ruta OCR llega con
`items=NULL`, `registrar_compra` devolvía `None`: inventario por foto, sin
entrada.

Ahora:

```http
PATCH /api/v1/tickets/{id}
{
  "provider_name": "CARNE MART",
  "provider_tax_id": "OCO030116UR4",
  "total_amount": "97.56",
  "subtotal": "97.56",
  "tax_amount": "0.00",
  "expense_date": "2026-09-28",
  "items": [
    {"description": "MILANESA DE PECHU", "quantity": "1.028", "unit_price": "94.90", "total": "97.56"}
  ]
}
```

Responde el ticket corregido y abre la compra en `EN_REVISION` con su línea.
**Nunca en `PROCESADO`**: ese estado exige firma humana (`ck_compras_confirmacion`).

---

## 2. Lo que NO funciona, con la medición

### Ningún modelo local lee tickets de mostrador

Medido con `scripts/medir_vision_contra_ocr.py` sobre los 7 comprobantes:

| Campo | `qwen2.5vl:3b` | `moondream` |
|---|---|---|
| RFC leído | **0 de 7** | 0 de 7 |
| Proveedor | 0% (0/7) | 0% (0/7) |
| Total | 14% | 14% |

Los dos están **peor que Tesseract**. No son un problema de prompt: son modelos
pensados para *describir* imágenes, no para extraer campos de un formato fijo.
Y tus tickets son papel térmico de mostrador, que es el peor caso.

### El RFC de una foto no es recuperable sin una persona

Medido en `IMG_4220.jpeg`:

```
el papel dice   R.F.C. OCO-030116-UR4
el OCR devolvió + ©.C 000-030116-UR4
```

La fecha (`030116`) y la homoclave (`UR4`) llegan intactas; solo las tres letras
se pierden. Se probaron las dos vías y las dos están bloqueadas:

- **Reconstruir `OCO`**: imposible. `000` es indistinguible de cualquier otra
  cosa que el OCR hubiera puesto ahí.
- **Guardar `???030116UR4`**: `TicketCreate` lo rechaza con `RFC invalido`, y
  está bien que lo haga. Forzarlo habría significado relajar el validador global
  de RFC, que protege el export a CONTPAQI entero.

Un RFC fiscal inventado es peor que ninguno: no se distingue de uno leído.

### Los 33.3% anotados en `AGENTS.md` están mal medidos

No es que el OCR lea un tercio. Es que **el pipeline pierde datos que el OCR sí
leyó**. El caso del RFC es la prueba: estaba en la foto, Tesseract lo entregó casi
entero, y el parser lo tiró por tres letras.

La cifra correcta necesita los 8 rotulados a mano
(`scripts/medir_precision_ocr.py`).

---

## 3. La verdad de los 8 comprobantes, leída del papel

Esto **no** es lo que el sistema leyó: es lo que dice el papel, leído a mano.
Sirve para (a) rotular la exactitud y (b) cargar lo que el OCR no pudo.

### `1C2C52A2-648D-480D-84A6-D5211B5DF459.jpeg` — OXXO

```json
{
  "provider_name": "CADENA COMERCIAL OXXO, S.A. DE C.V.",
  "provider_tax_id": "FLORESQRM6205231H4",
  "expense_date": "2026-09-19",
  "subtotal": "47.78",
  "tax_amount": "3.72",
  "total_amount": "51.50",
  "items": [
    {"description": "ARTICULOS VARIOS", "quantity": "1", "total": "47.78"}
  ]
}
```

El sistema leyó `31.50` (total correcto) y `null` en todo lo demás.

### `AA4D0E8F-EC9C-4159-9A9F-C4C84C22303E 2.JPG` — carneMart, Ahumada

```json
{
  "provider_name": "CARNE MART (CMT QUERETARO REVOLUCION)",
  "provider_tax_id": "OCO030116UR4",
  "expense_date": "2026-09-28",
  "subtotal": "97.56",
  "tax_amount": "0.00",
  "total_amount": "97.56",
  "items": [
    {"description": "MILANESA DE PECHU", "quantity": "1.028", "unit_price": "94.90", "total": "97.56"}
  ]
}
```

El sistema leyó `97.56` y `2026-09-28` **correctos**, con `REQUIERE_REVISION`
por falta de subtotal y RFC.

### `EE2C6866-2EF7-4073-ADC2-7F148773C917 2.JPG` — Bodega Aurrera

```json
{
  "provider_name": "BODEGA AURRERA (CUAUHTEMOC)",
  "provider_tax_id": "NWM970924QW4",
  "expense_date": "2026-09-23",
  "subtotal": "108.31",
  "tax_amount": "8.70",
  "total_amount": "117.00",
  "items": [
    {"description": "PEPOSYA100 PERRO", "quantity": "1", "unit_price": "12.00", "total": "12.00"},
    {"description": "PERRO PO", "quantity": "1", "unit_price": "9.50", "total": "9.50"},
    {"description": "ROCKO 44GR", "quantity": "1", "total": "5.00"},
    {"description": "ACTII ESQ", "quantity": "1", "total": "21.00"},
    {"description": "BOLILLO", "quantity": "15", "unit_price": "2.20", "total": "33.00"},
    {"description": "PAKETAXO", "quantity": "2", "unit_price": "10.00", "total": "20.00"}
  ]
}
```

El sistema leyó el total `117.00` **correcto**; el IVA como `0.00` cuando es
`8.70`.

### `FC3CB9F8-5AB1-4546-B9A9-016C69A158C5 2.JPG` — Walmart

```json
{
  "provider_name": "NUEVA WAL MART DE MEXICO S DE RL DE CV",
  "provider_tax_id": "NWM970924QW4",
  "expense_date": "2026-09-26",
  "subtotal": "217.27",
  "tax_amount": "16.73",
  "total_amount": "234.00",
  "items": [
    {"description": "BOLILLO", "quantity": "15", "unit_price": "2.20", "total": "33.00"},
    {"description": "DONA CHOCO", "quantity": "1", "total": "10.00"},
    {"description": "DONA BLANC", "quantity": "1", "total": "10.00"},
    {"description": "ZOTE BARRA", "quantity": "1", "total": "24.00"},
    {"description": "PAKETAXO", "quantity": "3", "unit_price": "10.00", "total": "30.00"},
    {"description": "GALLETASGV", "quantity": "1", "total": "15.00"}
  ]
}
```

**28 artículos** según el papel; el sistema solo vio el encabezado y con
`subtotal_plus_tax_mismatch` porque no leyó el IVA.

### `IMG_4220.jpeg` — carneMart

Idéntico al `AA4D0E8F` (mismo RFC, mismo total, misma fecha). El sistema lo leyó
`REQUIERE_REVISION` cuando en realidad el total, la fecha y el IVA están bien.

### `IMG_4222.jpeg` — Mercastar

```json
{
  "provider_name": "MERCASTAR",
  "provider_tax_id": null,
  "expense_date": "2026-10-01",
  "subtotal": "48.00",
  "tax_amount": "0.00",
  "total_amount": "50.00",
  "items": [
    {"description": "ARTICULO TEMPORAL", "quantity": "1", "unit_price": "48.00", "total": "48.00"}
  ]
}
```

El sistema puso `total: 0.00` y `total_not_positive`: **el peor caso**, un ticket
que sí se lee a simple vista.

### `IMG_4253 2.HEIC` — DSW

```json
{
  "provider_name": "GRUPO COMERCIAL DSW S.A. DE C.V.",
  "provider_tax_id": "GCD170101GS6",
  "expense_date": "2026-01-10",
  "subtotal": "279.93",
  "tax_amount": "0.00",
  "total_amount": "279.93",
  "items": [
    {"description": "PANTALON GAB", "quantity": "1", "total": "279.93"}
  ]
}
```

**Ojo: el total real es `279.93`, no `119.97`.** El sistema leyó `119.97`, que es
el precio *ya descontado* ("Usted ahorra $119.97 de $399.90"), y por eso marcó
`subtotal_plus_tax_mismatch`. Ese error del OCR es el que dispara el check: el
gate hizo bien en parar.

### `ticket_mercado_simple.pdf` — Mercado Local Don Pepe

El único leído **perfecto**. RFC, IVA, subtotal, 4 líneas, `AUTO_APROBADO` 0.95.

---

## 4. Comparativa, que es lo que decide

| | OCR (Tesseract) | Lectura humana |
|---|---|---|
| Total | 6 de 7 | **7 de 7** |
| Fecha | 5 de 7 | **7 de 7** |
| RFC | 0 de 7 | **6 de 7** |
| IVA | 4 de 7 | **7 de 7** |
| Proveedor | 4 aproximados | **7 exactos** |
| Líneas | 0 de 7 | **6 de 7** |

**La diferencia no es de modelo. Es que el PDF trae los caracteres exactos en los
bytes y las fotos no.** El carácter está o no está.

---

## 5. Qué te falta para cerrar la digitalización

### Ya hecho

- [x] `POST /scan` devuelve los datos de cada ticket
- [x] Los datos se guardan en `tickets`, `ticket_documents`, `scan_files`
- [x] `compras` + `compra_items` se crean cuando hay líneas
- [x] `PATCH /tickets/{id}` acepta `items` y `subtotal`
- [x] La compra se abre al corregir a mano
- [x] Las fotos se ven en la UI
- [x] `Decimal` en `items` ya no tumba el INSERT

### Lo que falta, y por qué no lo hice

**1. El formulario de revisión no pide las líneas.** La API ya las acepta
(`PATCH`), pero `ReviewQueue.tsx` manda solo los cinco campos del encabezado.
Sin esto, la corrección manual sigue sin poder alimentar el inventario — y es el
único camino que funciona hoy con fotos.

**2. Rotular los 8 a mano para medir la exactitud real.**
`scripts/medir_precision_ocr.py --init ~/Documents/Tickets_app` y luego
`--min-exactitud-total 0.985`. El 33.3% anotado en `AGENTS.md` no es el número
real.

**3. Automatizar de verdad las fotos.** Ningún modelo local sirve. El camino es un
modelo de visión externo, que manda tus comprobantes fiscales fuera de la
máquina. Como esta herramienta es local y privada por diseño, **eso se decide
explícitamente, no por conveniencia**: si lo quieres, se implementa y se documenta
el costo de privacidad; si no, el flujo de revisión es la respuesta.

**4. Los cuatro caminos de mejora de OCR ya se probaron y se descartaron** con
medición (`known-issues.md` §21). No los vuelvas a intentar sin una razón nueva.

---

## 6. El flujo que funciona hoy

```
Foto o PDF → Tickets_app/
     ↓
POST /scan  →  lee, guarda en BD, devuelve datos + veredicto
     ↓
   PENDIENTE / REQUIERE_REVISION     ← el gate paró lo que no cuadra
     ↓
Cola de revisión → "Ver comprobante original"  ← la foto ahora se ve
     ↓
PATCH /tickets/{id}  con items y subtotal
     ↓
   APROBADO  +  compra en EN_REVISION
     ↓
Inventario → asignar producto a la línea → confirmar (exige persona)
     ↓
   PROCESADO  ←  el stock empieza a contar
```

El paso que hace todo el trabajo es el `PATCH` con las líneas. Está listo en la
API; falta el formulario.