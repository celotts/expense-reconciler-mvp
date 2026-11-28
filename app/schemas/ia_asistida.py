"""La explicacion que propone la IA, con su veredicto de verificacion.

La respuesta NO es una afirmacion: `estado` dice si el texto paso la unica
comprobacion que puede hacer el codigo sin volver a preguntarle al modelo, que
es que cada numero citado este literalmente en los datos del ticket. La persona
sigue decidiendo.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExplicacionTicketResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID
    estado: Literal["disponible", "descartada", "no_disponible"]
    explicacion: str = Field(
        default="",
        description="Texto propuesto. Vacio cuando no hay explicacion usable.",
    )
    motivo: str | None = Field(
        default=None,
        description="Por que no se puede usar la explicacion, si no se puede.",
    )
    datos_verificables: list[str] = Field(
        default_factory=list,
        description="Datos literales que se le pasaron al modelo, sin raw_text.",
    )
    modelo: str | None = None
