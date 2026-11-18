import fitz  # PyMuPDF
import cv2
import numpy as np
import hashlib
from fastapi import UploadFile
from typing import List, Optional

def process_pdf(file: UploadFile) -> List[bytes]:
    document = fitz.open(file.file)
    pages = []
    for page_num in range(len(document)):
        page = document.load_page(page_num)
        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))  # 150 DPI
        pages.append(pix.tobytes("png"))
    return pages

def process_image(file: UploadFile) -> bytes:
    image_bytes = await file.read()
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    image = clahe.apply(image)
    return cv2.imencode('.png', image)[1].tobytes()

def calculate_sha256(file: UploadFile) -> str:
    sha256 = hashlib.sha256()
    while chunk := file.file.read(8192):
        sha256.update(chunk)
    return sha256.hexdigest()
