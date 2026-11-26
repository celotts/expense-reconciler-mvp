"""Escritura y lectura de membresias por empresa.

`company_id` NO va en el cuerpo: sale de `X-Company-ID` (`empresa_activa`), que
el servidor valida contra las membresias activas. Aceptarlo aqui seria pedirle
al cliente que repita una decision de seguridad que ya se tomo en la BD, y las
dos copias acabarian divergiendo. `extra="forbid"` hace que un cuerpo que reenvie
la fila entera reciba un 422 con el nombre del campo, en vez de un 200 que finge
haber guardado una empresa distinta.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import RolEmpresa


class MembresiaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    rol: RolEmpresa = RolEmpresa.OPERATOR
    activo: bool = True


class MembresiaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rol: RolEmpresa | None = None
    activo: bool | None = None

    @property
    def cambios(self) -> dict:
        # `exclude_unset`: la diferencia entre "no lo mandaron" y "lo mandaron
        # en null". Con `exclude_none`, un PATCH {"activo": false} borraria el
        # rol y el 200 llegaria igual.
        return self.model_dump(exclude_unset=True)


class MembresiaItemResponse(BaseModel):
    """Una fila de `usuarios_empresas` con la cuenta legible al lado."""

    company_id: UUID
    user_id: UUID
    email: str
    nombre: str
    rol: RolEmpresa
    activo: bool

    model_config = ConfigDict(from_attributes=True)


class ResetContrasenaAdminRequest(BaseModel):
    """La clave nueva que el administrador fija a otra cuenta.

    No lleva `contrasena_actual`: la de la cuenta afectada seria imposible de
    conocer, y pedirla convertiria el reset en un cambio propio. La firma es la
    contrasena de quien ADMINISTRA (`X-Contrasena-Actual`), igual que en el alta
    de cuentas. El valor nunca entra en la auditoria.
    """

    model_config = ConfigDict(extra="forbid")

    contrasena_nueva: str = Field(..., min_length=8, max_length=256)
