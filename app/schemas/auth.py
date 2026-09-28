"""Lo que entra y sale del login.

`UserResponse` no tiene un campo `password_hash` ni `password`, y no por
descuido: el dia que se anada un campo al modelo y se use `from_attributes` con
este schema, el hash sigue fuera porque el schema no lo pide. Un campo que no
esta declarado, no sale. Esa es la razon de que la forma de salida se escriba a
mano en vez de exponer el modelo entero.
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# El mismo limite que el backend, aplicado antes de gastar un scrypt. Un
# campo de correo de 500 caracteres no es un correo, y mandarlo a la base
# primero es trabajo gratis para quien este probando.
_CORREO_MAXIMO = 254

# Cuanto mas corto el campo, mas facil que alguien lo complete con "a" y no
# note que esta mal. Cuatro es el minimo razonable.
_CONTRASENA_MINIMA = 8


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., max_length=_CORREO_MAXIMO)
    # min_length es la primera linea; `servidor.py` tiene la segunda, que es la
    # que de verdad importa: una contrasena corta se acepta en el endpoint si
    # el cliente no valida. Las dos hacen falta, y estan en archivos distintos a
    # proposito para que quitar una en el cliente no la quite en el servidor.
    password: str = Field(..., min_length=1, max_length=256)

    # El correo se guarda en minuscula. "Ana@Empresa.mx" y "ana@empresa.mx" son
    # el mismo correo y con UNIQUE en la base serian dos cuentas si no se
    # normalizan antes de consultar. La normalizacion va aqui, en el borde, y
    # no repartida entre el endpoint y el servicio.
    @property
    def correo_normalizado(self) -> str:
        return self.email.strip().lower()


class UsuarioResponse(BaseModel):
    id: UUID
    email: str
    nombre: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # Segundos, no el instante de caducidad. El cliente lo suma a `iat` y ya
    # sabe cuando va a caducar sin tener que interpretar una fecha absoluta
    # con la zona horaria del navegador, que es donde se cuelan los errores de
    # una hora.
    expires_in: int
    user: UsuarioResponse
