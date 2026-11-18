import hashlib
import io
import fitz  # PyMuPDF
import cv2
import numpy as np
from PIL import Image

def compute_sha256(file_bytes: bytes) -> str:
    """Calcula el hash SHA256 de los bytes del archivo original para control anti-duplicados."""
    sha256_hash = hashlib.sha256()
    sha256_hash.update(file_bytes)
    return sha256_hash.hexdigest()

def preprocess_image_for_ocr(image_bytes: bytes) -> np.ndarray:
    """
    Preprocesa una imagen con OpenCV:
    1. Redimensiona manteniendo proporción a un ancho máximo de 1024px.
    2. Convierte a escala de grises.
    3. Aplica CLAHE (Contrast Limited Adaptive Histogram Equalization) para mejorar tickets de baja calidad.
    """
    # Convertir bytes a imagen OpenCV (numpy array)
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        raise ValueError("No se pudo decodificar la imagen con OpenCV.")

    # 1. Redimensionar manteniendo proporción (ancho máximo 1024px)
    height, width = img.shape[:2]
    max_width = 1024
    if width > max_width:
        ratio = max_width / float(width)
        new_height = int(height * ratio)
        img = cv2.resize(img, (max_width, new_height), interpolation=cv2.INTER_AREA)

    # 2. Convertir a escala de grises
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 3. Aplicar CLAHE (Contrast Limited Adaptive Histogram Equalization)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    processed = clahe.apply(gray)

    return processed

def process_pdf_page_to_image(pdf_bytes: bytes, page_number: int = 0) -> bytes:
    """
    Extrae una página de un archivo PDF usando PyMuPDF (fitz) a 150 DPI y la convierte a bytes PNG.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if page_number >= len(doc):
        raise ValueError(f"La página {page_number} no existe en el PDF.")
    
    page = doc[page_number]
    # Configurar zoom para 150 DPI (el valor base por defecto es 72 DPI, 150/72 ≈ 2.083)
    zoom = 150 / 72
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix)
    
    # Retornar los bytes de la imagen generada
    return pix.tobytes("png")
