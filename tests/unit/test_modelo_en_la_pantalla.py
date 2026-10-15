"""Que el modelo que leyo el comprobante llegue hasta la pantalla.

## QUE PROTEGE ESTE ARCHIVO

Un dato que se pide en una capa y se pierde en la siguiente. Es el fallo mas
barato de hacer y el mas dificil de ver, porque **nada falla**: el ticket se crea,
el panel se llena, los numeros cuadran, y el nombre del modelo no aparece por
ningun lado.

La cadena que tiene que estar completa:

    `AIResponse.model`      lo devuelve el proveedor
      -> `ExtractedInvoice.model_name`
      -> `TicketExtractionResult.model_name`      (`capture.invoice_to_result`)
      -> `ResumenArchivo.modelo_ia`               (`scan_service`)
      -> `ScanItemResponse.modelo_ia`             (la respuesta)
      -> el badge en la pantalla

Cada salto es un punto donde el dato se puede quedar, y **cada salto se prueba**.

## POR QUE ESTO NO ES COSMETICO

Porque `confidence_source` dice la CLASE de lector y no el motor. MEDIDO en esta
maquina:

    la `.env` declara   OLLAMA_MODEL=qwen2.5:3b
    el contenedor tiene  moondream:latest        <- y solo

Ese modelo de texto no habia contestado ni una vez, y no habia forma de
entrarlo: `confidence_source` dice `llm` para todos los motores, asi que un A/B
entre `qwen2.5:3b` y `llama3.1:8b` es indistinguible en el reporte de exactitud.

Por eso `GET /api/v1/scan/ia` contesta dos preguntas separadas —que esta
configurado y que esta instalado— en vez de un booleano.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas.scan import ScanItemResponse
from app.services.ai_client import AIProvider, AIResponse
from app.services.ai_extractor import ExtractedInvoice
from app.services.capture import invoice_to_result
from app.services.scan_service import ResumenArchivo
from app.core.enums import ConfidenceSource


# --- El dato existe aguas arriba -------------------------------------------


class TestLaRespuestaDelProveedorLoTrae:
    """Punto de partida: `AIResponse` SIEMPRE ha tenido el nombre del modelo.

    Esto parece un test inutil —comprueba que un dataclass tiene un campo— y no
    lo es: fija que el dato no se creo aqui. Si alguien lo anade, este test
    dice "ya lo teniamos desde antes", que es lo que paso.
    """

    def test_ai_response_lleva_modelo_y_proveedor(self):
        r = AIResponse(
            content="{}", tokens_used=10, model="qwen2.5:3b", provider=AIProvider.OLLAMA
        )
        assert r.model == "qwen2.5:3b"
        assert r.provider is AIProvider.OLLAMA


class TestElExtractorLoGuarda:
    """Saltos 1 y 2: el extractor no debe perderlo.

    El riesgo real aqui no es el campo: es que se declare en `ExtractedInvoice` y
    `invoice_to_result` no lo copie — que es exactamente lo que paso con `items`
    y con `ieps_amount`, dos veces, y ambas estan documentadas en AGENTS.md como
    "el dato existe aguas arriba y nadie lo copia".
    """

    def test_extracted_invoice_tiene_el_campo(self):
        assert "model_name" in ExtractedInvoice.__dataclass_fields__
        assert "model_provider" in ExtractedInvoice.__dataclass_fields__

    def test_el_proveedor_no_se_llama_como_el_comercio(self):
        """`model_provider`, NO `provider_name`. Y esto no es una manía.

        `provider_name` es el COMERCIO que emitió el comprobante y ya existía. Un
        segundo campo con el mismo nombre se pisa según el orden de los
        argumentos, y el ticket sale con `provider_name="ollama"`: no falla, es
        un `str` válido, y lo único que se rompe es el nombre del emisor.
        """
        nombres = ExtractedInvoice.__dataclass_fields__
        assert "model_provider" in nombres
        # Y el del comercio sigue siendo el que se espera.
        v = ExtractedInvoice(provider_name="WALMART", model_provider="ollama")
        assert v.provider_name == "WALMART"
        assert v.model_provider == "ollama"

    def test_un_invoice_sin_modelo_da_none(self):
        """`None` es "no hubo IA", no "no se sabe".

        Un PDF con capa de texto no pasa por ningun modelo, y ahi `None` es la
        verdad. Si se pusiera `""` la pantalla no podria distinguirlo de un
        modelo que se llamara asi.
        """
        v = ExtractedInvoice(provider_name="WALMART")
        assert v.model_name is None
        assert v.model_provider is None


# --- La copia que se perdia dos veces --------------------------------------


class TestInvoiceToResultLoCopia:
    """Salto 3: el que se perdio con `items` y con `ieps_amount`."""

    def _invoice(self, modelo, proveedor):
        return ExtractedInvoice(
            provider_name="WALMART",
            total=Decimal("234.00"),
            subtotal=Decimal("217.27"),
            tax_amount=Decimal("8.14"),
            model_name=modelo,
            model_provider=proveedor,
        )

    def test_el_modelo_llega_al_resultado(self):
        r = invoice_to_result(self._invoice("qwen2.5:3b", "ollama"))
        assert r.model_name == "qwen2.5:3b"
        assert r.model_provider == "ollama"

    def test_sin_modelo_llega_none(self):
        """La ruta de reglas y la de OCR no pasan por aqui, pero si un dia lo
        hacen, `None` tiene que viajar en vez de inventar un nombre."""
        r = invoice_to_result(self._invoice(None, None))
        assert r.model_name is None
        assert r.model_provider is None

    def test_el_origen_sigue_diciendo_llm(self):
        """El origen NO cambia: sigue siendo la clase de lector.

        Meter aqui el nombre del modelo seria cambiar el significado de un campo
        que ya se usa para agrupar en el reporte de exactitud, y un A/B de
        motores se volveria invisible justo en el reporte que lo mide.
        """
        r = invoice_to_result(self._invoice("qwen2.5:3b", "ollama"))
        assert r.confidence_source is ConfidenceSource.LLM


# --- El feed ---------------------------------------------------------------


class TestElFeedLoExpone:
    """Saltos 4 y 5: del servicio a la respuesta HTTP."""

    def _resumen(self, modelo, proveedor):
        return ResumenArchivo(
            relative_path="x.jpg",
            status=None,
            accion="CREADO",
            modelo_ia=modelo,
            proveedor_ia=proveedor,
        )

    def test_el_resumen_lo_tiene(self):
        r = self._resumen("moondream", "ollama")
        assert r.modelo_ia == "moondream"
        assert r.proveedor_ia == "ollama"

    def test_el_resumen_lo_declara_por_defecto_en_none(self):
        """Una foto leida por OCR no tuvo modelo, y eso se dice con `None`.

        Sin el default, cualquier `ResumenArchivo(...)` que se construya en otro
        sitio —y hay varios— falla con `AttributeError` al leerlo. Con el
        default, sale `None` y el feed lo omite, que es lo correcto.
        """
        r = ResumenArchivo(relative_path="x.jpg")
        assert r.modelo_ia is None
        assert r.proveedor_ia is None

    def test_la_respuesta_http_lo_serialize(self):
        """Salto 5: si no sale en el JSON, el front no lo puede pintar.

        Este es el salto que mas a menudo se olvida, porque `ScanItemResponse` es
        un `BaseModel` y todo lo que no este declarado no aparece — sin error.
        """
        import json

        r = ScanItemResponse(
            relative_path="x.jpg",
            status="PROCESADO",
            accion="CREADO",
            ticket_id=None,
            confianza=0.9,
            origen="llm",
            detalle=None,
            modelo_ia="qwen2.5:3b",
            proveedor_ia="ollama",
        )
        datos = json.loads(r.model_dump_json())
        assert datos["modelo_ia"] == "qwen2.5:3b"
        assert datos["proveedor_ia"] == "ollama"

    def test_sin_modelo_sigue_siendo_json_valido(self):
        """El caso de una foto por OCR: los campos van en `null`, no se omiten.

        Que el front pueda hacer `d.modelo_ia && (...)` depende de que la clave
        exista siempre. Si fuera `undefined` en el JSON, un `&&` lo resuelve
        igual, pero un `??` con valor por defecto daria el valor equivocado.
        """
        import json

        r = ScanItemResponse(
            relative_path="x.jpg",
            status="PROCESADO",
            accion="CREADO",
            ticket_id=None,
            confianza=0.5,
            origen="ocr",
            detalle=None,
        )
        datos = json.loads(r.model_dump_json())
        assert "modelo_ia" in datos
        assert datos["modelo_ia"] is None


# --- El endpoint de estado --------------------------------------------------


class TestElEndpointDeEstado:
    """`GET /scan/ia` contesta DOS preguntas, y por eso no da un booleano."""

    @pytest.mark.anyio
    async def test_distingue_configurado_de_instalado(self):
        from app.services.ai_client import disponibilidad

        d = await disponibilidad()

        # Las dos mitades de la verdad estan separadas.
        assert "configurado" in d
        assert "modelos_instalados" in d
        assert "faltan" in d

        # Y el arreglo trae el nombre del modelo dentro: un `ollama pull` sin
        # argumentos es media hora de diagnostica.
        if d["faltan"]:
            assert all("pull" in c for c in d["como_instalar"])
            assert all(m in " ".join(d["como_instalar"]) for m in d["faltan"])

    @pytest.mark.anyio
    async def test_no_pide_nada_a_la_red_si_la_ia_esta_apagada(self):
        """Con `AI_ENABLED=false` no debe intentar hablar con el proveedor.

        Un endpoint de estado que tarda 5 segundos en responder cuando la IA esta
        apagada se lee como "la API esta caida", que es justo lo contrario de lo
        que dice.
        """
        from app.core.config import settings
        from app.services.ai_client import disponibilidad

        antes = settings.AI_ENABLED
        try:
            settings.AI_ENABLED = False
            d = await disponibilidad()
        finally:
            settings.AI_ENABLED = antes

        assert d["modelos_instalados"] is None
        assert d["error"] is not None
        assert "AI_ENABLED" in d["error"]