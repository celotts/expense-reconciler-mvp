# Set de pruebas de digitalización

Este directorio es el set con el que se verificó la lectura de **PDF, imágenes y XML**
por el endpoint `POST /api/v1/expenses/digitalizar-directorio`. Los archivos binarios
se generan con un script: el contenido está escrito en texto en
`scripts/generar_set_pruebas_digitalizacion.py`, y el diff de un JPEG no diría nada.

```bash
# dentro del contenedor (donde están fpdf2 y Pillow)
docker compose exec expense-api python scripts/generar_set_pruebas_digitalizacion.py
docker compose exec expense-api python scripts/inspeccionar_set_pruebas.py --con-ocr
```

`--con-ocr` enciende **solo Tesseract**; el modelo queda apagado para que la medición
no mezcle una llamada de red de minutos por foto. Sin esa bandera se mide también el
camino sin OCR.

## Qué archivos entran y cuáles se nombran

La extensión decide solo si el archivo entra a la cola; el **lector** lo decide el
`magic` de los bytes (`app/core/archivo_real.py`). Por eso un `.jpg` con bytes de PDF
se lee como PDF y un `.HEIC` que por sus bytes es JPEG se lee como imagen.

Extensiones permitidas (la misma lista que devuelve `GET /api/v1/scan/config`):

`.pdf` · `.jpg` `.jpeg` · `.png` · `.gif` · `.webp` · `.bmp` · `.tif` `.tiff` ·
`.heic` `.heif` · `.psd` · `.xml`

Todo lo demás (`.docx`, `.xlsx`, `.txt`, …) **no se calla**: vuelve en
`archivos_no_permitidos` con su nombre, su extensión y su motivo, y nunca aparece
en `archivos`. Un symlink tampoco se lee; se reporta.

## Especificación y resultado medido

Resultados de `inspeccionar_set_pruebas.py --con-ocr`. `bytes` es el tipo detectado,
`lector` es quién leyó; **no son lo mismo**.

| Caso | Archivo | Qué prueba | bytes | lector | Total | Fecha |
|---|---|---|---|---|---|---|
| ideal | `ideal/01_factura_con_texto.pdf` | PDF con capa de texto, sin OCR ni modelo | `pdf` | `pdf_text` | 1100.00 | 2025-03-15 |
| ideal | `ideal/02_ticket_con_partidas.pdf` | subtotal + IVA = total con partidas | `pdf` | `pdf_text` | 110.20 | 2025-03-16 |
| ideal | `ideal/03_cfdi.xml` | CFDI por firma XML, no por foto | `xml` | `xml` | 1100.00 | 2025-03-15 |
| ideal | `ideal/04_foto_requiere_ocr.jpg` | foto real leída por Tesseract | `image` | `ocr` | 1100.00 | 2025-03-15 |
| borde | `borde/01_extension_mayuscula.PDF` | extensión en mayúsculas | `pdf` | `pdf_text` | 1100.00 | 2025-03-15 |
| borde | `borde/02_jpg_con_bytes_pdf.jpg` | el nombre miente, los bytes no | `pdf` | `pdf_text` | 1100.00 | 2025-03-15 |
| borde | `borde/03_heic_que_es_jpeg.HEIC` | cámara que nombró JPEG como HEIC | `image` | `ocr` | 1100.00 | 2025-03-15 |
| borde | `borde/04_subtotal_con_guion.pdf` | `Sub-Total` no compite con `Total` | `pdf` | `pdf_text` | 1100.00 | 2025-03-15 |
| borde | `borde/05_copia_otro_nombre.pdf` | dedup por SHA-256, no por nombre | `pdf` | `pdf_text` | 1100.00 | 2025-03-15 |
| borde | `borde/06_fecha_con_hora.pdf` | hora pegada cuenta como marca de comprobante | `pdf` | `pdf_text` | 1100.00 | 2026-10-06 |
| borde | `borde/subcarpeta/07_en_subcarpeta.pdf` | escaneo recursivo | `pdf` | `pdf_text` | 1100.00 | 2025-03-15 |
| fallido | `fallido/01_no_permitido.docx` | rechazado por extensión, con nombre | — | — | — | — |
| fallido | `fallido/02_no_permitido.xlsx` | rechazado por extensión, con nombre | — | — | — | — |
| fallido | `fallido/03_notas.txt` | rechazado por extensión, con nombre | — | — | — | — |
| fallido | `fallido/04_enlace.pdf` | symlink: se reporta, no se lee | — | — | — | — |
| fallido | `fallido/05_pdf_corrupto.pdf` | PDF que no abre: error visible, total 0 | `pdf` | `llm` | 0.00 | — |
| fallido | `fallido/06_xml_sin_firma.xml` | sin `<?xml` en el byte 0 no hay firma | — | — | — | — |

`--gigante` agrega `fallido/07_gigante.pdf` (26 MB, `truncate`) para probar el tope
por bytes. Se omite por defecto para no versionar ceros.

## La foto y la guarda de imagen casi en blanco

La primera versión de `ideal/04_foto_requiere_ocr.jpg` era un JPEG de 900×1200 con
la fuente por defecto de Pillow: solo **0.12 %** de píxeles oscuros. `tesseract` por
línea de comandos la leía, pero `app.services.ocr.leer_imagen()` devolvía `texto=''`
porque `ocr.py` descarta imágenes con menos de 0.5 % de tinta (evita 16 llamadas a
Tesseract sobre una foto del techo). El set parecía probar OCR y no lo hacía.

La corrección fue en el **artefacto**, no en la defensa: 800×600 con fuente 22 da
~1.1 % de tinta, y ahora `leer_imagen()` devuelve las seis líneas y confianza 0.9475.
No se bajó `_FRACCION_OSCUROS_MINIMA`.

## Lo que el set NO mide

- El CFDI con conceptos reales: el XML del set es sintético y no trae `Conceptos`,
  así que no se verifican líneas de inventario desde XML.
- La exactitud del OCR sobre fotos de la máquina: esas las mide
  `scripts/medir_precision_ocr.py` con la verdad rotulada a mano. Cinco fotos son
  una anécdota, no una medición con intervalo de confianza.
- La conciliación: estos archivos prueban la LECTURA. Para probar el cruce con el
  banco hay que cargar el extracto y ejecutar la conciliación aparte.
