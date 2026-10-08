"""El login.

Tres decisiones que no son obvias:

**El mismo error para correo desconocido y para contrasena incorrecta.** Un
menso distinto ("ese correo no existe") convierte el login en un buscador de
correos dados de alta. Ademas se verifica la contrasena contra un hash de
trampa cuando el correo no existe, para que el tiempo de respuesta no
distinga los dos casos.

**No hay refresh token.** Un solo token de vida limitada. Un refresh token es
un segundo secreto de vida larga que alguien tiene que guardar en algun lado
y que sobrevive al cierre de sesion del navegador; para una herramienta
interna de uso diario, reintentar el login es mas barato que esa superficie.

**El token se devuelve con su vida en segundos**, no con la fecha de
caducidad. El cliente suma y sabe cuando va a pasar, sin interpretar una
fecha absoluta con la zona horaria del navegador.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.core.deps import Sesion, UsuarioActual
from app.core.rate_limit import (
    anotar_intento_exitoso,
    anotar_intento_fallido,
    esta_bloqueado,
)
from app.core.security import (
    crear_token,
    hash_de_trampa,
    verificar_contrasena,
)
from app.core.time import utcnow
from app.models.user import UserModel
from app.schemas.auth import LoginRequest, TokenResponse, UsuarioResponse

router = APIRouter()


def _credenciales_malas() -> HTTPException:
    # Un solo mensaje para las tres causas. Ver la nota del modulo.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Correo o contraseña incorrectos",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _ip(request: Request) -> str:
    """La direccion de quien intento entrar.

    Detras de un proxy, `request.client.host` es la direccion del proxy, o sea
    la misma para todo el mundo, y el limite por IP no limitaria nada. El
    `X-Forwarded-For` lo resuelve, con una salvedad que hay que decir: si
    cualquiera puede mandar esa cabecera, tambien puede mentir sobre su
    direccion y esquivar el limite. Por eso solo se usa cuando hay exactamente
    un proxy de confianza, y por eso el limite tambien esta por (cuenta, IP):
    aunque la IP se falsee, los intentos contra UNA cuenta se siguen contando.
    """
    cabecera = request.headers.get("x-forwarded-for", "")
    if cabecera:
        return cabecera.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def _demasiados_intentos(segundos: int) -> HTTPException:
    """El 429 del login.

    Es un 429 y no un 403 a proposito: el cliente lo distingue de "credenciales
    malas" y no reintenta en bucle. Y lleva `Retry-After` para que un cliente
    bien escrito sepa cuando volver en vez de adivinar.
    """
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            "Demasiados intentos de acceso. Espera "
            f"{segundos} segundos e intentalo de nuevo."
        ),
        headers={"Retry-After": str(segundos)},
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    tags=["Auth"],
    summary="Iniciar sesion",
)
async def login(datos: LoginRequest, request: Request, db: Sesion) -> TokenResponse:
    correo = datos.correo_normalizado
    ip = _ip(request)

    # Se mira ANTES de gastar un scrypt. Un scrypt cuesta ~100 ms y ~64 MB: si
    # el par ya esta bloqueado, la respuesta tiene que ser inmediata o el
    # bloqueo se vuelve un amplificador de denegacion de servicio en vez de
    # una proteccion.
    restantes = esta_bloqueado(correo, ip)
    if restantes:
        raise _demasiados_intentos(restantes)

    usuario = (
        await db.execute(select(UserModel).where(func.lower(UserModel.email) == correo))
    ).scalar_one_or_none()

    if usuario is None:
        # Se gasta el mismo tiempo que con una contrasena real. Sin esta
        # llamada, "no existe" responde en microsegundos y "existe" en ~100 ms,
        # y esa diferencia es un oraculo de que correos estan dados de alta.
        verificar_contrasena(datos.password, hash_de_trampa())
        bloqueados = anotar_intento_fallido(correo, ip)
        if bloqueados:
            raise _demasiados_intentos(bloqueados)
        raise _credenciales_malas()

    if not verificar_contrasena(datos.password, usuario.password_hash):
        bloqueados = anotar_intento_fallido(correo, ip)
        if bloqueados:
            raise _demasiados_intentos(bloqueados)
        raise _credenciales_malas()

    if not usuario.is_active:
        # Tampoco se dice "tu cuenta esta dada de baja" con este mismo codigo.
        # Lo diria el 403 mas adelante, cuando alguien intente usar un token
        # suyo. Aqui el login falla igual que con una contrasena mala.
        raise _credenciales_malas()

    # Acierto: se borran los intentos fallidos de esta combinacion. Meter la
    # contrasena correcta cinco veces por un error de tecleo y luego acertar
    # no debe dejar a la persona esperando a que expire el bloqueo.
    anotar_intento_exitoso(correo, ip)

    token, segundos = crear_token(str(usuario.id), usuario.email)

    usuario.last_login_at = utcnow()
    await db.commit()
    await db.refresh(usuario)

    return TokenResponse(
        access_token=token,
        expires_in=segundos,
        user=UsuarioResponse.model_validate(usuario),
    )


@router.get(
    "/me",
    response_model=UsuarioResponse,
    tags=["Auth"],
    summary="Quien soy",
)
async def yo(usuario: UsuarioActual) -> UsuarioResponse:
    """Existe para que el cliente confirme su sesion al arrancar.

    La alternativa habitual es fiarse del token guardado en el navegador. Un
    token puede seguir siendo criptograficamente valido con la cuenta dada de
    baja, o haber caducado mientras la pestana estaba cerrada: este endpoint es
    lo que dice la verdad, y por eso se llama en cada carga de la aplicacion.
    """
    return UsuarioResponse.model_validate(usuario)
