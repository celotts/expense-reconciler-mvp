import hashlib
import importlib

import cv2
import numpy as np


def compute_sha256(file_bytes: bytes) -> str:
    """Calcula el hash SHA256 de los bytes del archivo original para control anti-duplicados."""
    return hashlib.sha256(file_bytes).hexdigest()


def preprocess_image_for_ocr(image_bytes: bytes) -> np.ndarray:
    """
    Preprocesa una imagen con OpenCV:
    1. Redimensiona manteniendo proporción a un ancho máximo de 1024px.
    2. Convierte a escala de grises.
    3. Aplica CLAHE (Contrast Limited Adaptive Histogram Equalization) para mejorar tickets de baja calidad.
    """
    nparr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img is None:
        raise ValueError("No se pudo decodificar la imagen con OpenCV.")

    height, width = img.shape[:2]
    max_width = 1024
    if width > max_width:
        ratio = max_width / float(width)
        new_height = max(1, int(round(height * ratio)))
        img = cv2.resize(img, (max_width, new_height), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def process_pdf_page_to_image(pdf_bytes: bytes, page_number: int = 0) -> bytes:
    """
    Extrae una página de un archivo PDF usando PyMuPDF (fitz) a 150 DPI y la convierte a bytes PNG.
    """
    if page_number < 0:
        raise ValueError(f"El número de página no puede ser negativo: {page_number}.")

    try:
        fitz_mod = importlib.import_module("fitz")
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF (fitz) no está instalado en el entorno para renderizar páginas PDF."
        ) from exc

    zoom = 150 / 72
    matrix = fitz_mod.Matrix(zoom, zoom)

    with fitz_mod.open(stream=pdf_bytes, filetype="pdf") as doc:
        if page_number >= len(doc):
            raise ValueError(f"La página {page_number} no existe en el PDF.")

        page = doc[page_number]
        pix = page.get_pixmap(matrix=matrix)
        return pix.tobytes("png")
