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

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

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
    """La forma de salida de un usuario. NUNCA el hash.

    Que no haya un campo `password_hash` ni un `password` no es descuido: el dia
    que se anada un campo al modelo y se use `from_attributes` con este schema, el
    hash sigue fuera porque el schema no lo pide. Un campo que no esta declarado,
    no sale.

    Es el MISMO schema para `GET /auth/me` y para `GET /usuarios`, y se comparte a
    proposito: si el listado de cuentas usara otro, ese otro seria el que
    acabaria con el `from_attributes` sobre el modelo entero.
    """

    id: UUID
    email: str
    nombre: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class UsuarioUpdate(BaseModel):
    """Lo que se puede cambiar de una cuenta. Todo opcional.

    `email` NO es editable, y la razon no es que "no haga falta": cambiar el
    correo de una cuenta rompe el rate limit del login, que lleva la cuenta dentro
    de la clave (`rate_limit.esta_bloqueado(correo, ip)`). Con el correo cambiado,
    los intentos de fuerza bruta que se estaban contando para `ana@empresa.mx`
    pasan a contarse para `otra@empresa.mx` y el bloqueo no protege nada. Es un
    detalle de implementacion del rate limit que se vuelve una decision de API.

    Y `is_active` a `false` es la baja. No hay `DELETE` de usuario y no lo va a
    haber: `is_active` es lo que `get_current_user` comprueba en cada peticion, y
    una baja tiene que surtir efecto inmediato sin borrar la fila.
    """

    model_config = ConfigDict(extra="forbid")

    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None


class CambioContrasenaRequest(BaseModel):
    """El cuerpo de cambiar la contrasena.

    `contrasena_actual` es obligatoria y no opcional: cambiar la propia
    contrasena sin escribir la vieja deja que cualquiera con un token robado tome
    la cuenta de forma permanente —el atacante rotaria la clave y el dueno ya no
    podria entrar a recuperarla—. Es el mismo argumento que hace obligatorio el
    header `X-Contrasena-Actual` en `deps.get_current_user_verificado`, y aqui se
    repite porque son dos puertas distintas a cosas distintas: el header prueba
    quien eres, este campo prueba que no teambles a ti mismo.

    El minimo de 8 es el de `scripts/crear_usuario.py`. Se aplica en los dos lados
    —el cliente valida y el servidor tambien— por el motivo que dice
    `LoginRequest`: el que se olvide de una de las dos capas es el que se traga un
    `min_length=1`.
    """

    model_config = ConfigDict(extra="forbid")

    contrasena_actual: str = Field(..., min_length=1, max_length=256)
    contrasena_nueva: str = Field(..., min_length=_CONTRASENA_MINIMA, max_length=256)

    @field_validator("contrasena_nueva")
    @classmethod
    def _no_sale_la_actual(cls, valor: str, info) -> str:
        # No es un requirement de complejidad —"Contrasena1" no es mejor que
        # "contrasena"— sino que la nueva sea literalmente la que ya tenias. Sin
        # esta comprobacion el endpoint responde 200 y no cambia nada, y quien lo
        # pidio se va creyendo que la rotacion funciono.
        actual = info.data.get("contrasena_actual")
        if actual is not None and valor == actual:
            raise ValueError("La contrasena nueva es igual a la actual")
        return valor


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # Segundos, no el instante de caducidad. El cliente lo suma a `iat` y ya
    # sabe cuando va a caducar sin tener que interpretar una fecha absoluta
    # con la zona horaria del navegador, que es donde se cuelan los errores de
    # una hora.
    expires_in: int
    user: UsuarioResponse
