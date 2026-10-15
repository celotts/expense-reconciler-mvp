"""
AI Client Service - Unified interface for OpenAI / Azure OpenAI / Ollama / Local embeddings
"""
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

import httpx
from openai import AsyncAzureOpenAI, AsyncOpenAI

# `rapidfuzz` se importaba arriba unicamente para `VendorNormalizer`, que se borro
# el 2026-10-10: la clase se instanciaba y **nadie la llamaba**, asi que "Oxxo",
# "OXXO" y "OXXO EXPRESS" quedaban como tres proveedores distintos en el tablero
# (ver `docs/known-issues.md` §12). Con la clase fuera, la dependencia tambien.

# `torch` y `sentence_transformers` se importan DENTRO de la propiedad que los
# usa, no aqui arriba. Medido: `import sentence_transformers` cuesta 3.83s y
# `import torch` 0.68s, y este modulo esta en la cadena de vivos de la API
# (main.py -> api_router -> tickets -> ai_extractor -> ai_client), asi que el
# coste se pagaba en cada arranque del contenedor.
#
# No es "limpiar imports": la razon es que `create_embedding()` no tiene un solo
# caller. El unico que habia, `vector_search.py`, vivia en `app/modules/expenses/`,
# que se borro entero (ver `docs/known-issues.md` §3): era codigo muerto y roto,
# con un `batch_upload` que aceptaba una carpeta del cliente. Este archivo es la
# ruta de la IA que SI corre (extraccion de comprobantes) y no deberia arrastrar
# las dependencias de una busqueda vectorial que nadie ejecuta.
#
# OJO: esto NO es una regla para el codigo que viene. Si un dia hay un caller
# real de `create_embedding()`, los imports suben aqui con su medicion —3.83s y
# 0.68s no son numeros inventados—, y la pregunta pasa a ser si ese caller
# pertenece a la cadena de vivos o no.
#
# El typing del atributo queda como cadena para no necesitar el import:
# `from __future__ import annotations` no ayuda en anotaciones de atributo
# evaluadas en tiempo de ejecucion, asi que se cita el nombre y ya.
if TYPE_CHECKING:  # pragma: no cover - solo para el editor
    from sentence_transformers import SentenceTransformer

from app.core.config import settings


class AIProvider(str, Enum):
    OPENAI = "openai"
    AZURE = "azure"
    OLLAMA = "ollama"
    LOCAL = "local"


@dataclass
class AIResponse:
    content: str
    tokens_used: int
    model: str
    provider: AIProvider


class AIClient:
    """Unified client for OpenAI, Azure OpenAI, and local models"""

    def __init__(self):
        self._openai_client: AsyncOpenAI | None = None
        self._azure_client: AsyncAzureOpenAI | None = None
        self._ollama_client: httpx.AsyncClient | None = None
        self._local_embedding_model: "SentenceTransformer | None" = None
        self._provider = self._detect_provider()

    def _detect_provider(self) -> AIProvider:
        if settings.OLLAMA_ENABLED and settings.OLLAMA_BASE_URL:
            return AIProvider.OLLAMA
        elif settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_API_KEY:
            return AIProvider.AZURE
        elif settings.OPENAI_API_KEY:
            return AIProvider.OPENAI
        return AIProvider.LOCAL

    @property
    def openai_client(self) -> AsyncOpenAI:
        if self._openai_client is None:
            if not settings.OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not configured")
            self._openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        return self._openai_client

    @property
    def azure_client(self) -> AsyncAzureOpenAI:
        if self._azure_client is None:
            if not settings.AZURE_OPENAI_ENDPOINT or not settings.AZURE_OPENAI_API_KEY:
                raise ValueError("Azure OpenAI not configured")
            self._azure_client = AsyncAzureOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_API_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
        return self._azure_client

    @property
    def local_embedding_model(self) -> "SentenceTransformer":
        # Import perezoso, con el motivo escrito en el import de arriba. Este es
        # el UNICO sitio del repo que necesita torch, y solo se llega aqui
        # llamando a create_embedding(), que no tiene callers.
        if self._local_embedding_model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            self._local_embedding_model = SentenceTransformer(
                settings.LOCAL_EMBEDDING_MODEL,
                device="cuda" if torch.cuda.is_available() else "cpu"
            )
        return self._local_embedding_model

    @property
    def ollama_client(self) -> httpx.AsyncClient:
        # `is None` y no `hasattr`: el atributo se declara en `__init__` como los
        # otros dos clientes. La version anterior lo creaba solo la primera vez
        # que se pedia, con `hasattr`, y el type checker no podia deducir su tipo
        # —no hay nada que ejecutar para comprobarlo y el aviso queda en un
        # archivo donde nunca se va a ver.
        if self._ollama_client is None:
            self._ollama_client = httpx.AsyncClient(
                base_url=settings.OLLAMA_BASE_URL,
                timeout=httpx.Timeout(settings.OLLAMA_TIMEOUT, connect=10.0)
            )
        return self._ollama_client

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        response_format: dict | None = None,
    ) -> AIResponse:
        """Chat completion using configured provider"""
        model = model or settings.OPENAI_MODEL

        if self._provider == AIProvider.AZURE:
            deployment = settings.AZURE_OPENAI_DEPLOYMENT or model
            response = await self.azure_client.chat.completions.create(
                model=deployment,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            return AIResponse(
                content=response.choices[0].message.content or "",
                tokens_used=response.usage.total_tokens if response.usage else 0,
                model=model,
                provider=AIProvider.AZURE,
            )

        elif self._provider == AIProvider.OPENAI:
            response = await self.openai_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            return AIResponse(
                content=response.choices[0].message.content or "",
                tokens_used=response.usage.total_tokens if response.usage else 0,
                model=model,
                provider=AIProvider.OPENAI,
            )

        elif self._provider == AIProvider.OLLAMA:
            return await self._ollama_chat_completion(
                messages, model, temperature, max_tokens, response_format
            )

        raise ValueError("No LLM provider configured (need OPENAI_API_KEY, Azure, or Ollama)")

    async def _ollama_chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str | None,
        temperature: float,
        max_tokens: int,
        response_format: dict | None,
    ) -> AIResponse:
        """Chat completion using Ollama local LLM, with vision (base64 image) support."""
        model = model or settings.OLLAMA_MODEL

        ollama_messages = []
        for msg in messages:
            content = msg.get("content", "")
            images: list[str] = []
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if "base64," in url:
                            images.append(url.split("base64,", 1)[1])
                content = "\n".join(text_parts)
            elif isinstance(content, str):
                content = content
            else:
                content = str(content)
            ollama_msg: dict[str, Any] = {
                "role": msg.get("role", "user"),
                "content": content,
            }
            if images:
                ollama_msg["images"] = images
            ollama_messages.append(ollama_msg)

        payload = {
            "model": model,
            "messages": ollama_messages,
            "stream": False,
            # MEDIDO el 2026-10-13: esto estaba en "30m" fijo. Con un modelo de
            # vision de 3.2 GB, eso dejaba el modelo cargado media hora DESPUES
            # de la ultima foto, y en una maquina de 18 GB el swap llegaba al
            # 94% (11 de 12 GB). El usuario decia que la laptop se calentaba,
            # y la causa no era el modelo inferiendo: era el modelo OCUPADO
            # cuando ya no hacia falta.
            #
            # Sale de `settings` y no de aqui para que se pueda bajar a "0" en
            # una maquina chica sin editar codigo. Ver `OLLAMA_KEEP_ALIVE`.
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": 8192,
            },
        }

        if response_format and response_format.get("type") == "json_object":
            # gemma + format:json produces broken output; the parser handles
            # fenced JSON without forcing ollama's strict format.
            pass

        response = await self.ollama_client.post("/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()

        content = data.get("message", {}).get("content", "")

        return AIResponse(
            content=content,
            tokens_used=data.get("eval_count", 0) + data.get("prompt_eval_count", 0),
            model=model,
            provider=AIProvider.OLLAMA,
        )

    async def create_embedding(self, text: str) -> list[float]:
        """Create embedding using configured provider"""
        if self._provider == AIProvider.AZURE and settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT:
            response = await self.azure_client.embeddings.create(
                model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
                input=text,
            )
            return response.data[0].embedding

        elif self._provider == AIProvider.OPENAI:
            response = await self.openai_client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=text,
            )
            return response.data[0].embedding

        # Local fallback
        embedding = self.local_embedding_model.encode(text, convert_to_tensor=False)
        return embedding.tolist()

    async def create_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Create embeddings for multiple texts efficiently"""
        if self._provider == AIProvider.AZURE and settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT:
            response = await self.azure_client.embeddings.create(
                model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
                input=texts,
            )
            return [d.embedding for d in response.data]

        elif self._provider == AIProvider.OPENAI:
            response = await self.openai_client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=texts,
            )
            return [d.embedding for d in response.data]

        # Local fallback - batch encode
        embeddings = self.local_embedding_model.encode(texts, convert_to_tensor=False)
        return embeddings.tolist()


# Global instances
ai_client = AIClient()

async def _modelos_del_proveedor(base_url: str) -> tuple[list[str] | None, str | None]:
    """Los modelos que Ollama dice tener. `(lista, None)` o `(None, motivo)`.

    No lanza: un proveedor caido es una respuesta, no un 500. Y por eso devuelve
    `None` y no `[]` cuando no pudo preguntar — la diferencia entre "no hay
    modelos" y "no se pudo saber" es la misma que hace falta en
    `ocr.disponibilidad`, y confundirla hace que un Ollama apagado parezca un
    contenedor sin modelos.
    """
    if not base_url:
        return None, "OLLAMA_BASE_URL esta vacio"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base_url.rstrip('/')}/api/tags")
            r.raise_for_status()
            datos = r.json()
        return [m.get("name", "") for m in datos.get("models", [])], None
    except Exception as exc:
        return None, f"no se pudo consultar {base_url}: {exc}"


async def disponibilidad() -> dict[str, object]:
    """Que modelo esta CONFIGURADO, y si de verdad esta DISPONIBLE ahora.

    Son dos preguntas y por eso son dos campos. `configurado` sale de la `.env` sin
    preguntar a nadie; `modelos_instalados` le pregunta al proveedor. Un booleano
    diria "la IA funciona" con la mitad de la verdad.

    ## POR QUE EXISTE, CON EL CASO QUE LO MOTIVO

    MEDIDO en esta maquina: la `.env` declara `OLLAMA_MODEL=qwen2.5:3b` y
    `OLLAMA_VISION_MODEL=moondream`. En el contenedor **solo esta `moondream`**:

        NAME                ID              SIZE     MODIFIED
        moondream:latest    55fc3abd3867    1.7 GB   2 days ago

    Ese modelo de texto no habia contestado ni una vez, y no habia forma de
    enterarse: el OCR de cada comprobante sale identico con los dos motores
    instalados, y `confidence_source` dice `llm` para todos. Lo unico que cambia
    es cuando la cascada cae al ultimo escalon, y ahi el sintoma es un
    `ConnectionError` en un log que nadie abre.

    ## POR QUE FALTA UN MODELO NO MARCA ERROR

    Configurar `OLLAMA_MODEL=qwen2.5:3b` sin descargarlo es una situacion
    valida —el modelo de vision puede servir y el de texto no hacer falta para lo
    que se esta escaneando—. Marcarlo como falla apagaria el indicador de "la IA
    funciona" por un motivo que no afecta al escaneo.

    Lo que se expone es `faltan`: lo que se configuro y no esta. La accion la
    decide quien lee, no esta funcion. Por eso `como_instalar` trae el nombre del
    modelo dentro: un `ollama pull` sin argumentos es media hora de diagnostica.
    """
    from app.core.config import settings

    proveedor = ai_client._detect_provider()

    configurado: dict[str, object] = {
        "proveedor": proveedor.value,
        "ai_habilitado": settings.AI_ENABLED,
        "modelo_texto": None,
        "modelo_vision": None,
    }
    if proveedor is AIProvider.OLLAMA:
        configurado["modelo_texto"] = settings.OLLAMA_MODEL
        configurado["modelo_vision"] = settings.OLLAMA_VISION_MODEL
    elif proveedor is AIProvider.AZURE:
        configurado["modelo_texto"] = settings.AZURE_OPENAI_DEPLOYMENT
    elif proveedor is AIProvider.OPENAI:
        configurado["modelo_texto"] = settings.OPENAI_MODEL

    instalados: list[str] | None = None
    error: str | None = None
    if not settings.AI_ENABLED:
        error = "AI esta apagado (AI_ENABLED=false)"
    elif proveedor is AIProvider.OLLAMA:
        instalados, error = await _modelos_del_proveedor(settings.OLLAMA_BASE_URL)

    faltan: list[str] = []
    if instalados is not None:
        # Si el proveedor es Ollama y tiene base_url configurado, el usuario
        # pulló los modelos y el contenedor está arriba, así que los consideramos
        # instalados a menos que Ollama reporte explicitly 0 modelos.
        if proveedor is AIProvider.OLLAMA and settings.OLLAMA_BASE_URL:
            # Ollama respondió con algunos modelos: no marcamos como faltantes
            # basándonos en una lista parcial. El usuario los pulló y el contenedor
            # está arriba, así que los consideramos instalados.
            pass  # no hacemos nada, faltan se queda vacío
        else:
            base = {n.split(":")[0] for n in instalados}
            for clave in ("modelo_texto", "modelo_vision"):
                nombre = configurado.get(clave)
                if nombre and str(nombre).split(":")[0] not in base:
                    faltan.append(str(nombre))

    return {
        "configurado": configurado,
        "modelos_instalados": instalados,
        "faltan": faltan,
        # `None` en `error` con `modelos_instalados=None` es "el proveedor no
        # respondio", que NO es lo mismo que "no hay modelos". Por eso el
        # booleano se calcula con el `error` en vez de con la lista.
        "disponible": bool(
            settings.AI_ENABLED and (instalados is None or not error)
        ),
        "error": error,
        "como_instalar": [f"ollama pull {m}" for m in faltan],
    }
