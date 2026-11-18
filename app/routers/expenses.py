import asyncio
import base64
from decimal import Decimal
from typing import List
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

# Asumiendo la estructura actual del proyecto
from app.schemas import ExpenseReceiptSchema
from app.services.processor import compute_sha256, preprocess_image_for_ocr, process_pdf_page_to_image
from app.services.ai_client import OllamaVisionClient

router = APIRouter(prefix="/expenses", tags=["Expenses"])

# Semáforo global estricto para limitar la concurrencia a 3 tareas simultáneas en el Mac M3
concurrency_semaphore = asyncio.Semaphore(3)

ai_client = OllamaVisionClient()

@router.post("/upload-batch", response_model=List[dict])
async def upload_batch_expenses(files: List[UploadFile] = File(...)):
    """
    Sube y procesa un lote de archivos (PDFs o imágenes) de manera concurrente (máx 3 hilos),
    aplicando preprocesamiento OpenCV, extracción con Ollama Vision, validación estricta Pydantic
    y control anti-duplicados por hash SHA256.
    """
    results = []
    
    async def process_single_file(file: UploadFile):
        async with concurrency_semaphore:
            try:
                file_bytes = await file.read()
                
                # 1. Calcular hash SHA256 para control anti-duplicados
                file_hash = compute_sha256(file_bytes)
                
                # TODO: Verificar aquí en base de datos si el file_hash ya existe antes de procesar
                
                # 2. Ingesta y preprocesamiento según tipo de archivo
                filename = file.filename.lower()
                if filename.endswith(".pdf"):
                    # Extraer primera página del PDF a 150 DPI
                    image_bytes = process_pdf_page_to_image(file_bytes, page_number=0)
                elif filename.endswith((".jpg", ".jpeg", ".png", ".webp", ".heic")):
                    image_bytes = file_bytes
                else:
                    return {
                        "filename": file.filename,
                        "status": "error",
                        "detail": "Formato de archivo no soportado."
                    }
                
                # 3. Preprocesar con OpenCV (escala de grises, redimensionado, CLAHE)
                processed_img_array = preprocess_image_for_ocr(image_bytes)
                
                # Codificar imagen procesada a PNG y luego a Base64 para Ollama Vision
                success, encoded_img = cv2.imencode('.png', processed_img_array)
                if not success:
                    raise ValueError("Error al codificar la imagen procesada por OpenCV.")
                
                img_base64 = base64.b64encode(encoded_img.tobytes()).decode('utf-8')
                
                # 4. Invocación al modelo local qwen2.5-vl:7b en Ollama
                extracted_data = await ai_client.extract_receipt_data(img_base64)
                
                # Inyectar el hash en los datos extraídos
                extracted_data["hash_duplicado"] = file_hash
                
                # 5. Validación estricta con Pydantic v2 (Decimal y Model Validator)
                validated_receipt = ExpenseReceiptSchema(**extracted_data)
                
                # TODO: Validar si la fecha pertenece a un periodo contable cerrado (closed_accounting_periods)
                
                return {
                    "filename": file.filename,
                    "status": "success",
                    "data": validated_receipt.model_dump()
                }
                
            except Exception as e:
                return {
                    "filename": file.filename,
                    "status": "error",
                    "detail": str(e)
                }

    # Ejecutar todas las tareas en paralelo respetando el semáforo de concurrencia
    tasks = [process_single_file(file) for file in files]
    results = await asyncio.gather(*tasks)
    
    return results
