from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class AIProvider(str, Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"


@dataclass
class AIResponse:
    content: str
    tokens_used: int = 0
    model: str = ""
    provider: AIProvider | str = AIProvider.OLLAMA


class OllamaVisionClient:
    # La ruta viva (`ai_extractor.AIExtractor`) decide que modelos pedir segun
    # este atributo. Sin el, caia al default `openai` y pedia `gpt-4o` a Ollama.
    _provider = AIProvider.OLLAMA

    def __init__(
        self,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        # La URL y el modelo vienen de la configuracion, no de un literal: el
        # cliente module-level se crea al importar, dentro del contenedor Ollama
        # esta en `http://ollama:11434` y `localhost` no es el proveedor.
        self.base_url = (
            base_url
            or getattr(settings, "OLLAMA_BASE_URL", None)
            or "http://localhost:11434"
        )
        self.model_name = (
            model_name
            or getattr(settings, "OLLAMA_VISION_MODEL", None)
            or "qwen2.5-vl:7b"
        )

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        response_format: Any = None,
    ) -> AIResponse:
        """Complecion usada por la cascada viva de captura.

        `AIExtractor` llama a este metodo tanto para texto como para vision y
        espera un `AIResponse` (`.content`, `.model`, `.provider`), no el dict
        crudo que devuelve `extract_receipt_data`. Antes el metodo no existia:
        con `AI_ENABLED=true` cada lectura que llegaba al modelo moria con
        `AttributeError` antes de hacer una sola peticion.

        El timeout es el configurado, no los 60 s del metodo heredado: una foto
        de ticket tarda mas que eso en una maquina de 16 GB y un timeout corto
        convertia la lentitud en un fallo de red.
        """
        target_model = model or self.model_name
        url = f"{self.base_url.rstrip('/')}/api/chat"
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "keep_alive": getattr(settings, "OLLAMA_KEEP_ALIVE", "5m"),
            },
            "stream": False,
        }
        if response_format == "json" or (
            isinstance(response_format, dict)
            and response_format.get("type") == "json_object"
        ):
            payload["format"] = "json"

        async with httpx.AsyncClient(timeout=getattr(settings, "OLLAMA_TIMEOUT", 600.0)) as client:
            response: Any = None
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json() if response.content else {}
                message = data.get("message") if isinstance(data, dict) else None
                content = message.get("content", "") if isinstance(message, dict) else ""
                return AIResponse(
                    content=str(content),
                    tokens_used=int(data.get("eval_count") or 0) if isinstance(data, dict) else 0,
                    model=str(data.get("model") or target_model) if isinstance(data, dict) else target_model,
                    provider=AIProvider.OLLAMA,
                )
            except httpx.HTTPStatusError as exc:
                cuerpo = ""
                for fuente in (response, getattr(exc, "response", None)):
                    if fuente is not None and getattr(fuente, "text", None):
                        cuerpo = str(fuente.text)
                        break
                logger.error(
                    "Error HTTP del proveedor Ollama (%s) en %s: %s - cuerpo: %s",
                    target_model,
                    url,
                    exc,
                    cuerpo,
                )
                raise

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


class AIClient:
    """Cliente de compatibilidad para completions locales y carga perezosa de embeddings."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        embedding_model_name: str = "all-MiniLM-L6-v2",
    ):
        self.base_url = (
            base_url
            or getattr(settings, "OLLAMA_BASE_URL", None)
            or "http://localhost:11434"
        )
        self.model = model or getattr(settings, "OLLAMA_MODEL", "llama3.1:8b")
        self.embedding_model_name = embedding_model_name
        self._local_embedding_model: Any = None
        self._ollama_client: Any = None

    @property
    def local_embedding_model(self) -> Any:
        if self._local_embedding_model is None:
            from sentence_transformers import SentenceTransformer

            self._local_embedding_model = SentenceTransformer(self.embedding_model_name)
        return self._local_embedding_model

    async def _ollama_chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        response_format: Any = None,
    ) -> dict[str, Any]:
        target_model = model or self.model
        url = f"{self.base_url.rstrip('/')}/api/chat"
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "stream": False,
        }
        if response_format == "json" or (
            isinstance(response_format, dict) and response_format.get("type") == "json_object"
        ):
            payload["format"] = "json"

        client = self._ollama_client
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(timeout=60.0)

        response: Any = None
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            cuerpo = ""
            for fuente in (response, getattr(exc, "response", None)):
                if fuente is not None and getattr(fuente, "text", None):
                    cuerpo = str(fuente.text)
                    break
            logger.error(
                "Error HTTP del proveedor Ollama (%s) en %s: %s - cuerpo: %s",
                target_model,
                url,
                exc,
                cuerpo,
            )
            raise
        finally:
            if owns_client and hasattr(client, "aclose"):
                await client.aclose()


ai_client = OllamaVisionClient()


async def _modelos_del_proveedor(base_url: str) -> tuple[list[str] | None, str | None]:
    """Consulta `/api/tags` del proveedor y devuelve `(modelos, error)`."""
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json() if response.content else {}
        modelos = [
            item.get("name", "")
            for item in data.get("models", [])
            if isinstance(item, dict) and item.get("name")
        ]
        return modelos, None
    except Exception as exc:
        logger.warning("No se pudieron listar modelos en %s: %s", url, exc)
        return None, f"no se pudo consultar {base_url}: {exc}"


def _esta_instalado(modelo_configurado: str, modelos_instalados: list[str]) -> bool:
    """Compara nombres de modelos respetando etiquetas (`:latest`, `:tag`, etc.)."""
    base_cfg = modelo_configurado.split(":", 1)[0]
    for instalado in modelos_instalados:
        if instalado == modelo_configurado or instalado.startswith(f"{modelo_configurado}:"):
            return True
        if ":" not in modelo_configurado and instalado.split(":", 1)[0] == base_cfg:
            return True
    return False


async def disponibilidad() -> Dict[str, Any]:
    """Reporta configuración, modelos instalados, faltantes y disponibilidad real."""
    modelo_texto = getattr(settings, "OLLAMA_MODEL", "llama3.1:8b")
    modelo_vision = getattr(settings, "OLLAMA_VISION_MODEL", ai_client.model_name)
    base_url = (
        getattr(settings, "OLLAMA_BASE_URL", None)
        or ai_client.base_url
        or "http://localhost:11434"
    )

    configurado = {
        "modelo_texto": modelo_texto,
        "modelo_vision": modelo_vision,
    }
    modelos_configurados = list(dict.fromkeys([m for m in (modelo_texto, modelo_vision) if m]))

    if not getattr(settings, "AI_ENABLED", True):
        return {
            "available": False,
            "disponible": False,
            "provider": AIProvider.OLLAMA.value,
            "model": modelo_vision,
            "base_url": base_url,
            "configurado": configurado,
            "modelos_instalados": None,
            "faltan": [],
            "como_instalar": [],
            "error": "AI_ENABLED=false: el servicio de IA está desactivado",
        }

    instalados, error_red = await _modelos_del_proveedor(base_url)

    if instalados is None:
        mensaje_error = error_red or f"no se pudo consultar {base_url}"
        return {
            "available": False,
            "disponible": False,
            "provider": AIProvider.OLLAMA.value,
            "model": modelo_vision,
            "base_url": base_url,
            "configurado": configurado,
            "modelos_instalados": None,
            "faltan": [],
            "como_instalar": [],
            "error": mensaje_error,
        }

    faltan = [m for m in modelos_configurados if not _esta_instalado(m, instalados)]
    como_instalar = [f"ollama pull {m}" for m in faltan]
    hay_alguno_instalado = len(faltan) < len(modelos_configurados)

    error_msg: Optional[str] = None
    if not hay_alguno_instalado:
        error_msg = (
            "Ninguno de los modelos configurados está instalado en Ollama "
            f"(faltan: {', '.join(faltan)})"
        )

    return {
        "available": hay_alguno_instalado,
        "disponible": hay_alguno_instalado,
        "provider": AIProvider.OLLAMA.value,
        "model": modelo_vision,
        "base_url": base_url,
        "configurado": configurado,
        "modelos_instalados": instalados,
        "installed_models": instalados,
        "faltan": faltan,
        "como_instalar": como_instalar,
        "error": error_msg,
    }
