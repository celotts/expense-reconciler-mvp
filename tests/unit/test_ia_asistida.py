"""El explicador propone; el codigo comprueba los numeros que cita.

MEDIDO: un modelo que devuelve un total que no esta en los datos del ticket no
puede llegar a la pantalla como explicacion usable. La defensa no es que el
modelo "no deba inventar": es que si inventa, el texto se descarta.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.ia_asistida import ExplicacionTicketResponse
from app.services.ai_client import AIResponse
from app.services.ia_asistida import ExplicadorIATicket, _normalizar_numero


class ClienteFalso:
    def __init__(self, contenido: str | None = None, fallar: bool = False):
        self.contenido = contenido
        self.fallar = fallar
        self.llamadas: list[list[dict]] = []

    async def chat_completion(self, messages, **_kwargs) -> AIResponse:
        self.llamadas.append(messages)
        if self.fallar:
            raise RuntimeError("el proveedor murio con un traceback interno")
        return AIResponse(content=self.contenido or "", model="modelo-de-prueba")


def _ticket() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        provider_name="Grupo Comercial DSW",
        provider_tax_id="GCD170101GS6",
        folio="114348-202000-901102-6113534",
        expense_date=date(2026, 10, 16),
        subtotal=Decimal("193.65"),
        tax_amount=Decimal("0.00"),
        ieps_amount=None,
        total_amount=Decimal("193.65"),
        extraction_status="AUTO_APROBADO",
        validation_errors=None,
        raw_text="EVIDENCIA QUE NO DEBE VIAJAR AL MODELO 987654321",
    )


@pytest.mark.asyncio
async def test_apagado_devuelve_no_disponible_y_no_envia_evidencia():
    cliente = ClienteFalso("El total es 193.65")
    respuesta = await ExplicadorIATicket(cliente=cliente, habilitado=False).explicar(_ticket())
    assert respuesta.estado == "no_disponible"
    assert respuesta.explicacion == ""
    assert cliente.llamadas == []
    unidos = " ".join(respuesta.datos_verificables)
    assert "987654321" not in unidos
    assert "EVIDENCIA" not in unidos


@pytest.mark.asyncio
async def test_explicacion_con_numeros_presentes_es_usable():
    cliente = ClienteFalso("La lectura quedo automatica: subtotal 193.65 y total 193.65.")
    respuesta = await ExplicadorIATicket(cliente=cliente, habilitado=True).explicar(_ticket())
    assert respuesta.estado == "disponible"
    assert respuesta.explicacion.startswith("La lectura")
    assert respuesta.modelo == "modelo-de-prueba"
    prompt = " ".join(m["content"] for m in cliente.llamadas[0])
    assert "987654321" not in prompt


@pytest.mark.asyncio
async def test_numero_inventado_se_descarta_aunque_el_texto_suene_bien():
    cliente = ClienteFalso("El total correcto segun el papel es 193.66, no 193.65.")
    respuesta = await ExplicadorIATicket(cliente=cliente, habilitado=True).explicar(_ticket())
    assert respuesta.estado == "descartada"
    assert respuesta.explicacion == ""
    assert "193.66" in respuesta.motivo


@pytest.mark.asyncio
async def test_se_acepta_el_mismo_numero_con_otro_separador_decimal():
    cliente = ClienteFalso("El comercio cobro 193,65 segun el total leido.")
    respuesta = await ExplicadorIATicket(cliente=cliente, habilitado=True).explicar(_ticket())
    assert respuesta.estado == "disponible", respuesta.motivo


@pytest.mark.asyncio
async def test_miles_no_se_cuentan_como_nuevo_numero():
    cliente = ClienteFalso("El total leido fue 1,234.50 y el subtotal 1234.50.")
    ticket = _ticket()
    ticket.total_amount = Decimal("1234.50")
    ticket.subtotal = Decimal("1234.50")
    respuesta = await ExplicadorIATicket(cliente=cliente, habilitado=True).explicar(ticket)
    assert respuesta.estado == "disponible", respuesta.motivo


@pytest.mark.asyncio
async def test_fallo_del_proveedor_no_devuelve_su_traceback():
    cliente = ClienteFalso(fallar=True)
    respuesta = await ExplicadorIATicket(cliente=cliente, habilitado=True).explicar(_ticket())
    assert respuesta.estado == "no_disponible"
    assert "traceback" not in (respuesta.motivo or "")
    assert "no se ha cambiado" in (respuesta.motivo or "")


@pytest.mark.asyncio
async def test_respuesta_vacia_se_descarta():
    cliente = ClienteFalso("   ")
    respuesta = await ExplicadorIATicket(cliente=cliente, habilitado=True).explicar(_ticket())
    assert respuesta.estado == "descartada"
    assert "vacia" in (respuesta.motivo or "")


def test_normalizacion_no_inventa_numero_de_basura():
    assert _normalizar_numero("193.65") == "193.65"
    assert _normalizar_numero("193,65") == _normalizar_numero("193.65")
    assert _normalizar_numero("1,234.50") == _normalizar_numero("1234.5")
    assert _normalizar_numero("no-numero") is None


def test_la_respuesta_no_lleva_el_id_del_ticket():
    # El id no se le pasa al modelo; un UUID citado en la explicacion seria un
    # numero inventado y por eso se descartaria. El campo solo llega al JSON
    # para que quien lo lea sepa de que ticket habla.
    respuesta = ExplicacionTicketResponse(
        ticket_id=uuid4(), estado="no_disponible", motivo="sin modelo"
    )
    assert respuesta.ticket_id is not None
    assert "ticket_id" not in respuesta.explicacion
