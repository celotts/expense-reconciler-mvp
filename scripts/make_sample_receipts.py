"""Genera comprobantes de prueba en las dos formas en que llegan de verdad.

Un PDF de un ticket aparece de dos maneras y el sistema las tiene que tratar
distinto:

  impreso  el PDF guarda texto. Un pdfplumber lo lee y sale exacto, gratis y
           en milisegundos. Mandarlo a la IA es tirar dinero y accuracies.

  escaneado  el PDF es una foto del papel dentro de una caja de PDF. No hay
           texto que leer: hay que mirar la pagina. Ahi si se necesita vision.

La distincion no se puede suponer por la extension del archivo: los dos son
.pdf. Se decide preguntando al archivo, no al nombre.

Salida: una carpeta con los dos, para usarlos a mano contra la API y como
material de prueba.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

# Lineas que el parser por reglas sabe leer. El formato importa: los labels
# en español son los que ancla el parser, asi que un comprobante de verdad se
# parece a esto, no a un formulario web.
COMPROBANTE = [
    ("Tiendas Ramirez SA de CV", 18),
    ("RFC: TRAM910101XXX", 12),
    ("FACTURA No: 00123", 12),
    ("FECHA EXPEDICION: 15/03/2025", 12),
    ("", 6),
    ("DESCRIPCION                IMPORTE", 12),
    ("Cafe en grano 1kg          250.00", 12),
    ("Refresco 600ml              35.50", 12),
    ("", 6),
    ("SUBTOTAL                    850.00", 12),
    ("IVA (16%)                   136.00", 12),
    ("TOTAL                     1,100.00", 13),
]


def _es_impreso(destino: Path) -> None:
    """PDF con texto real: lo que sale de un sistema que genera el CFDI."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Courier", size=11)
    for linea, tamano in COMPROBANTE:
        pdf.set_font("Courier", size=tamano)
        pdf.cell(0, 6, text=linea, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(destino))


def _es_escaneado(destino: Path) -> None:
    """PDF que solo tiene una imagen: el papel escaneado o fotografiado.

    Aqui el texto esta dibujado pixel a pixel. No hay nada que extraer con
    pdfplumber porque no existe una capa de texto: hay que mirar la pagina.
    """
    from PIL import Image, ImageDraw, ImageFont

    ancho, alto = 850, 1100
    imagen = Image.new("RGB", (ancho, alto), "white")
    dibujo = ImageDraw.Draw(imagen)

    try:
        fuente = ImageFont.load_default(size=26)
        fuente_chica = ImageFont.load_default(size=20)
    except TypeError:
        # Pillow < 10.1 no acepta size en load_default.
        fuente = fuente_chica = ImageFont.load_default()

    y = 90
    for linea, _ in COMPROBANTE:
        if linea:
            dibujo.text((60, y), linea, fill="black",
                        font=fuente if len(linea) > 30 else fuente_chica)
        y += 46

    # Una mancha de ruido: un escaner de verdad nunca da una pagina limpia.
    for i in range(0, alto, 900):
        dibujo.ellipse([(40, i), (ancho - 40, i + 4)], fill=(235, 235, 235))

    imagen.save(destino, "PDF", resolution=150.0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("destino", nargs="?", default="muestras",
                    help="carpeta donde escribir los .pdf")
    args = ap.parse_args()

    destino = Path(args.destino)
    destino.mkdir(parents=True, exist_ok=True)

    impreso = destino / "ticket_impreso.pdf"
    escaneado = destino / "ticket_escaneado.pdf"
    _es_impreso(impreso)
    _es_escaneado(escaneado)

    # Verificacion: el generador no sirve de nada si produce dos archivos que
    # se parecen. Se comprueba que uno tiene texto y el otro no, que es
    # justamente la distincion que el sistema tiene que detectar.
    import pdfplumber

    for ruta in (impreso, escaneado):
        with pdfplumber.open(ruta) as pdf:
            texto = "\n".join(p.extract_text() or "" for p in pdf.pages)
        print(f"{ruta}  ({ruta.stat().st_size:,} bytes, {len(texto)} chars de texto)")
        if "escaneado" in ruta.name and texto.strip():
            print("  ERROR: el escaneado salio con texto; no prueba el caso escaneado")
        if "impreso" in ruta.name and "TOTAL" not in texto:
            print("  ERROR: el impreso salio sin el total; no prueba el caso impreso")


if __name__ == "__main__":
    main()
