from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    pregunta_o_caso: str = Field(..., min_length=1)
    proveedor_id: str | None = None
    monto_referencia: Decimal | None = None


class ToolCallRecord(BaseModel):
    tool_name: str
    args_hash: str
    summary: str


class AgentDiagnosis(BaseModel):
    estado: Literal["ok", "requiere_revision", "limite_alcanzado", "error"]
    diagnostico: str
    hallazgos: list[str] = Field(default_factory=list)
    pasos_ejecutados: int = Field(default=0, ge=0)
