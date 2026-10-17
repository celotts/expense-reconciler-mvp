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

class TestElDiagnosticoNoDiceQueTodoEstaBien:
    """`faltan` era SIEMPRE la lista vacia con Ollama, y `disponible` daba true.

    MEDIDO el 2026-10-15 en esta maquina, con lo que respondia la API viva:

        {
          "configurado": {"modelo_texto": "moondream", "modelo_vision": "qwen2.5vl:3b"},
          "modelos_instalados": [],        <- no hay NINGUNO
          "faltan": [],                    <- y aun asi "no falta nada"
          "disponible": true               <- y "esta disponible"
        }

    Este endpoint se escribio para el caso exacto de "el modelo esta configurado
    y no existe", y no lo hacia: el calculo de `faltan` estaba cortocircuitado
    con un `pass` para Ollama y su `else` —el unico que hacia el calculo— era
    codigo muerto.

    ## POR QUE ESTO COSTO CARO Y NO SE NOTO

    `EstadoDeIa` en el front **solo aparece si hay algo que arreglar**. Con
    `faltan` vacio no aparecia nada, y las fotos se leian **solo con
    Tesseract**, que es el unico escalon que queda cuando no hay modelo
    instalado. El techo de exactitud del sistema paso a ser el del OCR sin que
    ninguna pantalla lo dijera, y el sintoma era "el sistema no lee bien los
    montos" — que es exactamente como se lee un problema de modelo ausente.

    Es el fallo que el propio archivo critica: un dato que se pide en una capa
    y se pierde en la siguiente, donde ademas **nada falla**.
    """

    @staticmethod
    def _con_modelos(monkeypatch, instalados, error=None):
        """`disponibilidad()` con la respuesta del proveedor controlada."""
        from app.services import ai_client as mod

        async def _fake(base_url):
            return instalados, error

        monkeypatch.setattr(mod, "_modelos_del_proveedor", _fake)

    @pytest.mark.anyio
    async def test_un_contenedor_sin_modelos_no_dice_que_esta_disponible(
        self, monkeypatch
    ):
        """La defensa. `disponible: true` con `modelos_instalados: []` es mentir."""
        from app.services.ai_client import disponibilidad

        self._con_modelos(monkeypatch, [])

        d = await disponibilidad()

        assert d["disponible"] is False, (
            "sin ningun modelo instalado no hay quien conteste una peticion: "
            "decir 'disponible' es la mitad de la verdad"
        )
        assert d["error"], "tiene que decir POR QUE no esta disponible"
        assert "moondream" in d["error"] or "qwen2.5vl" in d["error"]

    @pytest.mark.anyio
    async def test_los_modelos_configurados_que_faltan_se_dicen(self, monkeypatch):
        """El caso de esta maquina: los dos configurados, ninguno instalado."""
        from app.services.ai_client import disponibilidad

        self._con_modelos(monkeypatch, [])

        d = await disponibilidad()

        assert d["faltan"], (
            "`faltan` es la lista de lo que se configuro y no esta. Con "
            "`modelos_instalados: []` tiene que traer los dos nombres."
        )
        assert set(d["faltan"]) == {"moondream", "qwen2.5vl:3b"}
        assert all("ollama pull" in c for c in d["como_instalar"])

    @pytest.mark.anyio
    async def test_falta_el_que_no_esta_y_no_el_que_esta(self, monkeypatch):
        """Solo los que faltan, y el corte por etiqueta no da falsos positivos.

        `ollama list` devuelve siempre la etiqueta (`moondream:latest`) y la
        `.env` muchas veces no la trae. Sin el corte por etiqueta, un modelo que
        SI esta instalado se reportaria como faltante y el indicador se pondria
        rojo sin motivo.
        """
        from app.services.ai_client import disponibilidad

        self._con_modelos(monkeypatch, ["moondream:latest", "llama3.1:8b"])

        d = await disponibilidad()

        assert d["faltan"] == ["qwen2.5vl:3b"], (
            "el de texto esta instalado (con etiqueta) y el de vision no: solo "
            "el de vision puede estar en la lista"
        )
        assert d["disponible"] is True, (
            "con el modelo de texto instalado hay quien conteste: disponible es "
            "true aunque falte el de vision, y `faltan` dice cual"
        )

    @pytest.mark.anyio
    async def test_hay_modelos_pero_ninguno_de_los_configurados(self, monkeypatch):
        """El caso que hace_util el segundo corte, y el mas facil de confundir.

        MEDIDO el 2026-10-15 en el Ollama NATIVO de esta maquina: hay 25 modelos
        instalados, y ninguno es `moondream` ni `qwen2.5vl:3b`, que es lo que
        declara la `.env`. La lista de instalados esta llena y aun asi no hay
        quien conteste.

        Es el caso donde `disponible` se calcula **solo** con "el proveedor no dio
        error": el proveedor respondio perfecto, hay 25 nombres, y la respuesta
        seria `true`. Y lo que se afirma con eso es "la IA funciona" cuando la
        unica llamada que haria el sistema es a un modelo que no existe.
        """
        from app.services.ai_client import disponibilidad

        self._con_modelos(monkeypatch, ["llama3.1:8b", "nomic-embed-text:latest"])

        d = await disponibilidad()

        assert d["disponible"] is False, (
            "responder NO es lo mismo que poder contestar: los dos modelos "
            "configurados estan ausentes"
        )
        assert set(d["faltan"]) == {"moondream", "qwen2.5vl:3b"}

    @pytest.mark.anyio
    async def test_no_se_puede_afirmar_disponibilidad_sin_preguntar(self, monkeypatch):
        """`modelos_instalados: None` es "no se pudo preguntar", no "no hay".

        Y en ese caso `disponible` no se puede afirmar: no es lo mismo que una
        lista vacia, que si es una respuesta.
        """
        from app.services.ai_client import disponibilidad

        self._con_modelos(monkeypatch, None, error="no se pudo consultar http://ollama:11434")

        d = await disponibilidad()

        assert d["modelos_instalados"] is None
        assert d["disponible"] is False
        assert "no se pudo consultar" in d["error"]


class TestUnErrorDelProveedorSeDice:
    """Un 500 sin cuerpo no dice nada, y no hay forma de actuar sobre "nada".

    MEDIDO el 2026-10-15 con `IMG_4409.heic` y `qwen2.5vl:3b`: la llamada a
    vision devolvio 500 dos veces seguidas y la app registra

        Server error '500 Internal Server Error' for url 'http://ollama:11434/api/chat'

    El 500 de Ollama puede ser contexto excedido, memoria, o el modelo que no
    cabe: **el mensaje lo dice y el codigo no**. `raise_for_status()` tira una
    excepcion que solo lleva la URL, asi que el motivo se perdia.

    Lo que se perdio fue tiempo: con dos 500 sin pista, lo unico que se puede
    hacer es repetir a ciegas. La tercera llamada SI funciono y devolvio
    `279.93` —la verdad— sin que nadie supiera por que las dos anteriores no.
    """

    @pytest.mark.anyio
    async def test_el_cuerpo_del_error_se_registra(self, monkeypatch, caplog):
        """El motivo del proveedor tiene que quedar escrito."""
        import httpx

        from app.services import ai_client as mod

        class _Respuesta:
            status_code = 500
            text = '{"error":"the request exceeds the available context size"}'

            def raise_for_status(self):
                raise httpx.HTTPStatusError(
                    "500 Server Error", request=None, response=None
                )

        class _Cliente:
            async def post(self, *_a, **_kw):
                return _Respuesta()

            async def aclose(self):
                pass

        cliente = mod.AIClient()
        cliente._ollama_client = _Cliente()

        with caplog.at_level("ERROR"):
            with pytest.raises(httpx.HTTPStatusError):
                await cliente._ollama_chat_completion(
                    [{"role": "user", "content": "hola"}], model="qwen2.5vl:3b",
                    temperature=0.1, max_tokens=10, response_format=None,
                )

        escrito = "\n".join(r.getMessage() for r in caplog.records)
        assert "exceeds the available context size" in escrito, (
            "el cuerpo del proveedor es lo unico que distingue 'contexto "
            "excedido' de 'sin memoria' de 'modelo que no cabe'. Registrar solo "
            "el 500 deja las tres como el mismo fallo."
        )
