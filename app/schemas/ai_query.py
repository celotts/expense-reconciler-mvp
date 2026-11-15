"""Schemas del endpoint de consultas en lenguaje natural."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AiQueryRequest(BaseModel):
    """Una pregunta en lenguaje natural y el tenant contra el que se responde.

    `tenant_id` es la empresa. El servidor NUNCA se lo cree a ciegas: pasa
    por `resolver_empresa`, que lo contrasta con la empresa del usuario
    autenticado (y con el rol, si el usuario no tiene empresa). Pedir la
    empresa de otro es 404, no 403: no se confirma que exista.
    """

    model_config = ConfigDict(extra="forbid")

    pregunta: str = Field(..., min_length=3, max_length=500)
    tenant_id: UUID
    max_filas: int = Field(default=100, ge=1, le=200)


class AiQueryResponse(BaseModel):
    pregunta: str
    tenant_id: UUID
    sql: str
    columnas: list[str]
    filas: list[dict[str, Any]]
    cantidad_filas: int
    truncado: bool
    modelo: str
