import json
import logging
from enum import Enum
from typing import Any, Dict

import httpx

logger = logging.getLogger(__name__)


class AIProvider(str, Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"


class OllamaVisionClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model_name: str = "qwen2.5-vl:7b",
    ):
        self.base_url = base_url
        self.model_name = model_name

    async def extract_receipt_data(self, image_base64: str) -> Dict[str, Any]:
        """
        Envía la imagen procesada al modelo local en Ollama con temperature 0.0
        y formato JSON estricto para extraer los datos de la factura/recibo.
        """
        url = f"{self.base_url}/api/chat"

        prompt = (
            "Eres un extractor experto de datos contables y fiscales para México y Chile. "
            "Analiza la imagen del ticket o factura proporcionada y extrae la información solicitada "
            "estrictamente en el siguiente formato JSON puro, sin texto adicional ni bloques markdown:\n"
            "{\n"
            '  "country": "MEX o CHL",\n'
            '  "comercio": "Nombre del establecimiento",\n'
            '  "identificacion_fiscal": "RFC o RUT del comercio",\n'
            '  "fecha": "YYYY-MM-DD",\n'
            '  "items": [\n'
            "    {\n"
            '      "name": "Nombre del producto",\n'
            '      "price": 0.00,\n'
            '      "quantity": 1\n'
            "    }\n"
            "  ],\n"
            '  "subtotal": 0.00,\n'
            '  "tax": 0.00,\n'
            '  "total": 0.00,\n'
            '  "metodo_pago": "Efectivo / Tarjeta / Transferencia",\n'
            '  "link_o_folio_facturacion": null\n'
            "}"
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_base64],
                }
            ],
            "format": "json",
            "options": {
                "temperature": 0.0,
            },
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                result_data = response.json()

                content_str = result_data.get("message", {}).get("content", "{}")
                parsed_json = json.loads(content_str)
                return parsed_json

            except httpx.HTTPStatusError as e:
                logger.error(
                    "Error HTTP al comunicarse con Ollama: %s - %s",
                    e.response.status_code,
                    e.response.text,
                )
                raise RuntimeError(
                    f"Fallo en el servicio de IA local: {e.response.text}"
                ) from e
            except httpx.RequestError as e:
                logger.error("Error de conexión con Ollama: %s", e)
                raise RuntimeError(
                    "No se pudo conectar con el servicio de IA local (Ollama)."
                ) from e
            except json.JSONDecodeError as e:
                logger.error("El modelo no devolvió un JSON válido: %s", e)
                raise ValueError(
                    "La respuesta de Ollama no pudo ser decodificada como JSON."
                ) from e


ai_client = OllamaVisionClient()
