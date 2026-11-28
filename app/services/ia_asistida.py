"""Explicador de ticket: IA asistida, de solo lectura.

Este modulo NO escribe en la base, NO aprueba tickets y NO corrige datos. Toma
una fila que ya existe y propone un texto para que una persona lo lea. La unica
comprobacion que el codigo puede hacer sin volver a preguntarle al modelo es
que los numeros que cita aparezcan literalmente en los datos que se le dieron:
un explicador que inventa un total es peor que un explicador que no responde.

No se le pasa `raw_text`: ademas de ser evidencia extensa y ruidosa, contiene
folios y numeros sueltos que el modelo podria citar como si fueran del ticket.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.models.ticket import TicketModel
from app.schemas.ia_asistida import ExplicacionTicketResponse

logger = logging.getLogger(__name__)

# Dos separadores seguidos son validos en un importe mexicano (`1,234.50`):
# con `?` el regex se comia solo `1,234` y el resto (`50`) parecia un numero
# inventado, asi que una explicacion correcta se descartaba.
_NUMERO_RE = re.compile(r"\d+(?:[.,]\d+)*")
_MILES_RE = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_MILES_PUNTO_RE = re.compile(r"^\d{1,3}(?:\.\d{3})+$")


def _normalizar_numero(token: str) -> str | None:
    """Unifica `1,234.50`, `1234.50` y `1234,50` a `1234.50`.

    Se permiten los dos separadores porque el modelo puede citar el importe con
    el formato que quiera; lo que no se permite es que cite un numero que no
    estaba. Un token sin sentido se descarta, no se adivina.
    """
    texto = token.strip()
    if "," in texto and "." in texto:
        decimal = "," if texto.rfind(",") > texto.rfind(".") else "."
        miles = "." if decimal == "," else ","
        texto = texto.replace(miles, "").replace(decimal, ".")
    elif "," in texto:
        texto = texto.replace(",", "") if _MILES_RE.match(texto) else texto.replace(",", ".")
    elif "." in texto and _MILES_PUNTO_RE.match(texto):
        texto = texto.replace(".", "")
    try:
        numero = Decimal(texto)
    except InvalidOperation:
        return None
    if not numero.is_finite():
        return None
    return format(numero.normalize(), "f")


def _numeros_de(texto: str) -> set[str]:
    return {
        normalizado
        for token in _NUMERO_RE.findall(texto)
        if (normalizado := _normalizar_numero(token)) is not None
    }


def _importe(valor: Any) -> str:
    if valor is None:
        return "no disponible"
    return format(Decimal(str(valor)).normalize(), "f")


class ExplicadorIATicket:
    """Propone una explicacion y marca si los numeros citados son verificables."""

    def __init__(self, cliente: Any = None, habilitado: bool | None = None):
        self.cliente = cliente
        self.habilitado = (
            settings.IA_ASISTIDA_HABILITADA if habilitado is None else habilitado
        )

    @staticmethod
    def datos_verificables(ticket: TicketModel) -> list[str]:
        """Los datos que se le muestran al modelo, sin evidencia ni identificadores."""
        return [
            f"comercio: {ticket.provider_name or 'no disponible'}",
            f"rfc: {ticket.provider_tax_id or 'no disponible'}",
            f"folio: {ticket.folio or 'no disponible'}",
            f"fecha: {ticket.expense_date.isoformat() if ticket.expense_date else 'no disponible'}",
            f"subtotal: {_importe(ticket.subtotal)}",
            f"iva: {_importe(ticket.tax_amount)}",
            f"ieps: {_importe(ticket.ieps_amount)}",
            f"total: {_importe(ticket.total_amount)}",
            f"estado de la lectura: {ticket.extraction_status or 'no disponible'}",
            f"motivos de la lectura: {ticket.validation_errors or 'ninguno'}",
        ]

    async def explicar(self, ticket: TicketModel) -> ExplicacionTicketResponse:
        datos = self.datos_verificables(ticket)
        ticket_id: UUID = ticket.id
        if not self.habilitado:
            return ExplicacionTicketResponse(
                ticket_id=ticket_id,
                estado="no_disponible",
                motivo="La explicacion con IA no esta habilitada en este servidor.",
                datos_verificables=datos,
            )

        cliente = self.cliente
        if cliente is None:
            # Se importa aqui para que la funcion nueva no cargue el cliente al
            # arrancar: con IA asistida apagada no debe haber ni una llamada.
            from app.services.ai_client import ai_client

            cliente = ai_client

        mensajes = [
            {
                "role": "system",
                "content": (
                    "Explicador de comprobantes en espanol, de solo lectura. "
                    "Explica en 2 a 4 frases por que la lectura del ticket tiene "
                    "ese estado y que datos la sostienen. Puedes citar SOLO "
                    "numeros que aparezcan literalmente en los datos que te dan. "
                    "No des consejos fiscales, no afirmes que el gasto es correcto, "
                    "no inventes fechas ni menciones identificadores."
                ),
            },
            {"role": "user", "content": "\n".join(datos)},
        ]

        try:
            respuesta = await cliente.chat_completion(
                mensajes, temperature=0.0, max_tokens=180
            )
            texto = " ".join(str(respuesta.content or "").split())
            modelo = str(getattr(respuesta, "model", "") or "") or None
        except Exception as exc:
            # El cuerpo del proveedor puede contener rutas internas: no se
            # devuelve al cliente. Tampoco se toca el ticket: la lectura queda
            # exactamente como estaba antes de la llamada.
            logger.warning(
                "La IA asistida no respondio (%s); el ticket no se cambio",
                type(exc).__name__,
                exc_info=True,
            )
            return ExplicacionTicketResponse(
                ticket_id=ticket_id,
                estado="no_disponible",
                motivo="El modelo no respondio. La lectura del ticket no se ha cambiado.",
                datos_verificables=datos,
            )

        if not texto:
            return ExplicacionTicketResponse(
                ticket_id=ticket_id,
                estado="descartada",
                motivo="El modelo devolvio una explicacion vacia.",
                datos_verificables=datos,
                modelo=modelo,
            )

        inventados = sorted(_numeros_de(texto) - _numeros_de("\n".join(datos)))
        if inventados:
            return ExplicacionTicketResponse(
                ticket_id=ticket_id,
                estado="descartada",
                motivo=(
                    "La explicacion cito numeros que no aparecen en los datos "
                    f"del ticket: {', '.join(inventados)}."
                ),
                datos_verificables=datos,
                modelo=modelo,
            )

        return ExplicacionTicketResponse(
            ticket_id=ticket_id,
            estado="disponible",
            explicacion=texto,
            datos_verificables=datos,
            modelo=modelo,
        )
