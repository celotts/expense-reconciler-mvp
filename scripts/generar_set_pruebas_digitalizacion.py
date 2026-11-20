#!/usr/bin/env python3
"""Genera el set de archivos para probar la digitalizacion de un directorio.

Por que un script y no archivos binarios versionados
---------------------------------------------------
Un PDF, un JPEG y un HEIC son binarios: revisar un diff de ellos no dice nada,
y un `git add` los cuelga sin que nadie sepa que contienen. Aqui el contenido
esta escrito en TEXTO y el script lo materializa. Si cambia una regla del
parser, se cambia el texto y se vuelve a correr.

Donde se generan
----------------
`test-files/set-digitalizacion/`, con tres carpetas:

* `ideal/`    — lo que el API puede leer y afirmar sin que nadie lo corrija.
* `borde/`    — lo que se lee pero pone a prueba una decision concreta.
* `fallido/`  — lo que NO entra, con su nombre y su motivo.

Uso (dentro del contenedor, que es donde estan fpdf2 y Pillow):

    docker compose exec expense-api python scripts/generar_set_pruebas_digitalizacion.py
    docker compose exec expense-api python scripts/generar_set_pruebas_digitalizacion.py --gigante

`--gigante` crea un archivo de 26 MB con `truncate` (no ocupa 26 MB reales)
para probar el tope por bytes. Se omite por defecto para no versionar ceros.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "test-files" / "set-digitalizacion"
TOPE_BYTES = 25 * 1024 * 1024

COMPROBANTE = """TIENDAS RAMIREZ SA DE CV
RFC: TRAM910101XXX
Fecha: 2025/03/15
Subtotal: 948.28
IVA (16%): 151.72
TOTAL: 1100.00
"""

COMPROBANTE_CON_PARTIDAS = """PAPELERIA CENTRAL SA DE CV
RFC: PCE010101AB1
Fecha: 2025/03/16
Cuaderno 3 x 25.00 = 75.00
Lapiz 2 x 10.00 = 20.00
Subtotal: 95.00
IVA (16%): 15.20
TOTAL: 110.20
"""


def _pdf(ruta: Path, texto: str) -> None:
    """PDF con capa de texto, latin-1 por la fuente Helvetica de fpdf2."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for linea in texto.splitlines():
        pdf.cell(0, 8, linea.encode("latin-1", errors="replace").decode("latin-1"), new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(ruta))


def _imagen(ruta: Path, texto: str) -> None:
    """JPEG real con el comprobante dibujado (necesita OCR para leerse).

    La letra es GRANDE y el cuadro ESTRECHO a proposito. Medido con la primera
    version (900x1200, fuere por defecto de Pillow ~11 px): solo 0.12% de los
    pixeles eran tinta, por debajo del 0.5% que `ocr.py` exige para llamar a
    Tesseract. El binario leia el papel, pero `leer_imagen()` salia en 50 ms con
    texto vacio y el set de pruebas parecia probar OCR cuando no lo hacia.

    Con 800x600 y fuente 22 la tinta es ~1.1%: una foto de ticket de verdad,
    no una pagina en blanco con siete palabras. No se bajo la guarda de
    `ocr.py` —esa defensa evita 16 llamadas a Tesseract sobre una foto del
    techo—; se arreglo el artefacto que la disparaba por error.
    """
    img = Image.new("RGB", (800, 600), "white")
    dibujo = ImageDraw.Draw(img)
    fuente = ImageFont.load_default(size=22)
    y = 30
    for linea in texto.splitlines():
        dibujo.text((30, y), linea, fill="black", font=fuente)
        y += 70
    img.save(ruta, "JPEG", quality=90)


def generar(gigante: bool = False) -> list[str]:
    creados: list[str] = []

    def pdf(relativo: str, texto: str) -> None:
        ruta = DESTINO / relativo
        ruta.parent.mkdir(parents=True, exist_ok=True)
        _pdf(ruta, texto)
        creados.append(relativo)

    def imagen(relativo: str, texto: str) -> None:
        ruta = DESTINO / relativo
        ruta.parent.mkdir(parents=True, exist_ok=True)
        _imagen(ruta, texto)
        creados.append(relativo)

    def bytes_(relativo: str, contenido: bytes) -> None:
        ruta = DESTINO / relativo
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(contenido)
        creados.append(relativo)

    # --- ideal -------------------------------------------------------------
    # Estos tres los lee el API sin OCR y sin modelo: los bytes dicen pdf/xml y
    # el texto trae subtotal + IVA = total. El gate puede responsabilizarse.
    pdf("ideal/01_factura_con_texto.pdf", COMPROBANTE)
    pdf("ideal/02_ticket_con_partidas.pdf", COMPROBANTE_CON_PARTIDAS)
    bytes_(
        "ideal/03_cfdi.xml",
        (
            "<?xml version='1.0' encoding='UTF-8'?>\n"
            "<Comprobante>\n"
            "  <Emisor>TIENDAS RAMIREZ SA DE CV RFC: TRAM910101XXX</Emisor>\n"
            "  <Fecha>Fecha: 2025/03/15</Fecha>\n"
            "  <Subtotal>Subtotal: 948.28</Subtotal>\n"
            "  <Impuestos>IVA (16%): 151.72</Impuestos>\n"
            "  <Total>TOTAL: 1100.00</Total>\n"
            "</Comprobante>\n"
        ).encode("utf-8"),
    )
    # La foto real SI depende de Tesseract: con OCR apagado se guarda como
    # lectura incompleta, no como un comprobante que el sistema afirme.
    imagen("ideal/04_foto_requiere_ocr.jpg", COMPROBANTE)

    # --- borde -------------------------------------------------------------
    pdf("borde/01_extension_mayuscula.PDF", COMPROBANTE)
    # El nombre dice JPG, los bytes dicen PDF. Gana el lector de PDF: es la
    # defensa "el file_type del cliente no elige la ruta de lectura".
    ruta_pdf = DESTINO / "ideal/01_factura_con_texto.pdf"
    bytes_("borde/02_jpg_con_bytes_pdf.jpg", ruta_pdf.read_bytes())
    # Al reves que el anterior: extension HEIC, bytes JPEG. Es el caso real de
    # una camara que nombro `IMG_4253 2.HEIC` un JPEG.
    imagen("borde/03_heic_que_es_jpeg.HEIC", COMPROBANTE)
    pdf(
        "borde/04_subtotal_con_guion.pdf",
        COMPROBANTE.replace("Subtotal: 948.28", "Sub-Total: 948.28"),
    )
    pdf("borde/05_copia_otro_nombre.pdf", COMPROBANTE)
    pdf(
        "borde/06_fecha_con_hora.pdf",
        COMPROBANTE.replace("Fecha: 2025/03/15", "06/10/2026 11:30:21 211.36 36 30"),
    )
    pdf("borde/subcarpeta/07_en_subcarpeta.pdf", COMPROBANTE)

    # --- fallido -----------------------------------------------------------
    bytes_("fallido/01_no_permitido.docx", b"No soy un comprobante")
    bytes_("fallido/02_no_permitido.xlsx", b"No soy un comprobante")
    bytes_("fallido/03_notas.txt", b"RFC: TRAM910101XXX TOTAL: 1100.00\n")
    bytes_("fallido/05_pdf_corrupto.pdf", b"%PDF-1.7\nesto no es un PDF valido\n")
    # Sin `<?xml` ni `<!DOCTYPE` en el byte 0 no hay firma: NO_SOPORTADO, aunque
    # el contenido parezca un CFDI.
    bytes_(
        "fallido/06_xml_sin_firma.xml",
        ("\n" + (DESTINO / "ideal/03_cfdi.xml").read_text()).encode("utf-8"),
    )

    enlace = DESTINO / "fallido/04_enlace.pdf"
    if not enlace.is_symlink():
        enlace.symlink_to("../ideal/01_factura_con_texto.pdf")
    creados.append("fallido/04_enlace.pdf -> ideal/01_factura_con_texto.pdf")

    if gigante:
        gran = DESTINO / "fallido/07_gigante.pdf"
        gran.parent.mkdir(parents=True, exist_ok=True)
        with gran.open("wb") as manejador:
            manejador.truncate(TOPE_BYTES + 1)
        creados.append(f"fallido/07_gigante.pdf ({TOPE_BYTES + 1} bytes)")

    return creados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gigante", action="store_true", help="crea tambien el archivo de 26 MB"
    )
    args = parser.parse_args()
    creados = generar(args.gigante)
    print(f"Generados {len(creados)} archivos en {DESTINO}:")
    for relativo in creados:
        print(f"  {relativo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
