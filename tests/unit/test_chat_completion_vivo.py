"""La ruta viva de IA tiene un cliente que sepa hablar con Ollama.

Medido el 2026-11-27: `AI_ENABLED=true` y `chat_completion` no existia en el
cliente module-level (`OllamaVisionClient`). `AIExtractor` lo llamaba de todos
modos, asi que la cascada moria con `AttributeError` antes de pedirle nada al
modelo. Y como tampoco tenia `_provider`, el extractor creia que el proveedor
era `openai` y pedia `gpt-4o` a un servidor Ollama.

Un test que solo mirara `hasattr` no caza la segunda mitad: por eso aqui se
comprueban las dos y el payload que efectivamente sale hacia el proveedor.
"""

import logging

import httpx
import pytest

from app.core.config import settings


class _Respuesta:
    content = b'{"message":{"content":"{\\"total\\": 1}"},"eval_count":12,"model":"qwen2.5vl:3b"}'

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "message": {"content": '{"total": 1}'},
            "eval_count": 12,
            "model": "qwen2.5vl:3b",
        }


class _ClienteFalso:
    captura = None

    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, json):
        type(self).captura = {"url": url, "payload": json, "timeout": self.timeout}
        return _Respuesta()


class _RespuestaConError:
    text = '{"error":"the request exceeds the available context size"}'

    def raise_for_status(self):
        raise httpx.HTTPStatusError("500", request=None, response=None)


class _ClienteConError:
    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, _url, **_kwargs):
        return _RespuestaConError()


@pytest.mark.anyio
async def test_el_cliente_vivo_habla_ollama(monkeypatch):
    from app.services import ai_client as mod

    assert hasattr(mod.ai_client, "chat_completion"), (
        "la cascada llama a chat_completion; sin el, cada lectura con IA muere "
        "con AttributeError antes de consultar al modelo"
    )
    assert mod.ai_client._provider.value == "ollama"
    assert mod.ai_client.base_url == settings.OLLAMA_BASE_URL

    monkeypatch.setattr(mod.httpx, "AsyncClient", _ClienteFalso)
    respuesta = await mod.ai_client.chat_completion(
        messages=[{"role": "user", "content": "hola"}],
        model="qwen2.5vl:3b",
        temperature=0.0,
        max_tokens=1200,
        response_format={"type": "json_object"},
    )

    captura = _ClienteFalso.captura
    assert captura["url"] == settings.OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
    assert captura["payload"]["model"] == "qwen2.5vl:3b"
    assert captura["payload"]["options"]["temperature"] == 0.0
    assert captura["payload"]["options"]["num_predict"] == 1200
    assert captura["payload"]["options"]["keep_alive"] == settings.OLLAMA_KEEP_ALIVE
    assert captura["payload"]["format"] == "json"
    assert captura["timeout"] == settings.OLLAMA_TIMEOUT
    assert respuesta.content == '{"total": 1}'
    assert respuesta.tokens_used == 12
    assert respuesta.model == "qwen2.5vl:3b"
    assert respuesta.provider.value == "ollama"


@pytest.mark.anyio
async def test_el_extractor_pide_los_modelos_configurados_no_los_de_openai():
    from app.services.ai_extractor import AIExtractor

    extractor = AIExtractor()
    assert extractor._provider == "ollama"
    assert extractor._chat_model == settings.OLLAMA_MODEL
    assert extractor._vision_model == settings.OLLAMA_VISION_MODEL


@pytest.mark.anyio
async def test_un_500_del_proveedor_vivo_se_registra_con_cuerpo(monkeypatch, caplog):
    from app.services import ai_client as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", _ClienteConError)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(httpx.HTTPStatusError):
            await mod.ai_client.chat_completion(
                messages=[{"role": "user", "content": "hola"}], model="qwen2.5vl:3b"
            )

    escrito = "\n".join(r.getMessage() for r in caplog.records)
    assert "exceeds the available context size" in escrito, (
        "sin el cuerpo del 500, contexto agotado y falta de memoria son el "
        "mismo fallo y solo queda repetir a ciegas"
    )
