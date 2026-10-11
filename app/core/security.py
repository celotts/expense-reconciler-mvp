"""Primitivas de autenticacion: hash de contrasena y token firmado.

Dos decisiones que conviene tener a la vista, porque las dos son la razon de
que este archivo exista en vez de un `import jwt`:

**No hay dependencias nuevas.** `hashlib.scrypt` y `hmac` son de la libreria
estandar. Anadir una libreria de JWT o de hashing por token resuelve el
problema en cinco lineas e introduce una cadena de suministro mas que
auditar, actualizar y que puede entrar con una vulnerabilidad. Para lo que
hace falta aqui (firmar un token con una clave del servidor y comparar
contrasenas contra un hash) la libreria estandar alcanza.

**El hash de la contrasena no es reversible y lleva sal.** scrypt con los
parametros de abajo cuesta memoria a proposito: hace caro adivinar una
contrasena por fuerza bruta, que es lo que pasa con un SHA-256 solo si el
archivo de hashes se filtra. La sal es por usuario, asi que dos contrasenas
iguales dan hashes distintos y un ataque de tabla precalculada no sirve.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.core.config import settings

# Coste de memoria de scrypt. 2**14 es el minimo recomendado por la
# documentacion de OWASP para_PASSWORD_HASHING. Sube con la memoria de la
# maquina, no con el numero de usuarios: el objetivo es que un atacante con
# una GPU dedicated no pueda probar miles de contrasenas por segundo.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_LLAVE_LEN = 32
_SAL_LEN = 16

# Version del formato del hash. Si algun dia se cambian los parametros, esta
# cadena cambia con ellos y `verificar` rechaza los hashes viejos en vez de
# compararlos con parametros que ya no son los que se usaron.
_VERSION_HASH = "scrypt-v1"


class ErrorDeToken(Exception):
    """El token no sirve. El motivo se lo da al que llama, nunca al cliente:

    distinguir "caducado" de "mal formado" en el mensaje HTTP ayuda a quien
    depura y no ayuda a nadie que quiera falsificar uno."""


class TokenCaducado(ErrorDeToken):
    pass


def _b64e(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode("ascii").rstrip("=")


def _b64d(texto: str) -> bytes:
    # El padding se quita al codificar y hay que ponerlo al decodificar.
    relleno = "=" * (-len(texto) % 4)
    return base64.urlsafe_b64decode(texto + relleno)


# ---------------------------------------------------------------------------
# Contrasenas
# ---------------------------------------------------------------------------


def hashear_contrasena(contrasena: str) -> str:
    """Devuelve `scrypt-v1$n$r$p$salt$hash`, todo en base64url.

    El formato lleva sus propios parametros para que verificar no dependa de
    las constantes de este modulo. Si mañana suben N a 2**16, los hashes
    viejos se siguen validando con N=2**14 y se pueden migrar uno por uno. Sin
    eso, subir el coste invalida a todos los usuarios de golpe.
    """
    sal = secrets.token_bytes(_SAL_LEN)
    derivada = hashlib.scrypt(
        contrasena.encode("utf-8"),
        salt=sal,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_LLAVE_LEN,
        maxmem=132 * 1024 * 1024,
    )
    return "$".join(
        [
            _VERSION_HASH,
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            _b64e(sal),
            _b64e(derivada),
        ]
    )


def verificar_contrasena(contrasena: str, guardado: str) -> bool:
    """Compara en tiempo constante. `hmac.compare_digest` y no `==` porque `==`
    sale en cuanto encuentra la primera diferencia: el tiempo de respuesta
    filtra cuantos caracteres correctos lleva alguien probando."""
    try:
        version, n, r, p, sal_b64, esperado_b64 = guardado.split("$")
        if version != _VERSION_HASH:
            return False
        esperado = _b64d(esperado_b64)
        derivada = hashlib.scrypt(
            contrasena.encode("utf-8"),
            salt=_b64d(sal_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(esperado),
            maxmem=132 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        # Hash mal formado o de otra version: no es una contrasena valida, y
        # no es una excepcion que le sirva de nada a quien llama.
        return False

    return hmac.compare_digest(derivada, esperado)


_TRAMPA_CACHEADA: str | None = None


def hash_de_trampa() -> str:
    """Un hash de una contrasena que nadie tiene, para verificar contra el
    cuando el correo no existe.

    Sin esto, un login con un correo inexistente responde al instante y uno con
    correo existente tarda lo que tarda scrypt. Esa diferencia delata que
    correos hay dados de alta, y a eso solo le sirve a alguien que este
    juntando correos para probar contrasenas.

    Se cachea porque calcularlo cuesta lo mismo que una verificacion real, y
    sin cache cada intento con correo inexistente multiplica por dos el coste
    de scrypt: seria un amplificador de denegacion de servicio.
    """
    global _TRAMPA_CACHEADA
    if _TRAMPA_CACHEADA is None:
        _TRAMPA_CACHEADA = hashear_contrasena(secrets.token_urlsafe(32))
    return _TRAMPA_CACHEADA


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


def crear_token(usuario_id: str, email: str, expira_en_minutos: int | None = None) -> tuple[str, int]:
    """Firma un token HS256. Devuelve el token y los segundos de vida.

    El `exp` va DENTRO del token firmado, no en un header. Un header se puede
    cambiar sin invalidar la firma; un claim no.
    """
    minutos = (
        expira_en_minutos
        if expira_en_minutos is not None
        else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    ahora = int(time.time())
    expira = ahora + minutos * 60

    cabecera = {"alg": "HS256", "typ": "JWT"}
    cuerpo = {
        "sub": usuario_id,
        "email": email,
        "iat": ahora,
        "exp": expira,
        # Marca el tipo de token. Un dia que haya refresh tokens, un access
        # token no puede pasar por el endpoint de refresh: sin esto, serviria
        # un token de vida corta como si fuera de vida larga.
        "typ": "access",
    }

    partes = [
        _b64e(json.dumps(cabecera, separators=(",", ":")).encode("utf-8")),
        _b64e(json.dumps(cuerpo, separators=(",", ":")).encode("utf-8")),
    ]
    firma = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        ".".join(partes).encode("ascii"),
        hashlib.sha256,
    ).digest()
    partes.append(_b64e(firma))

    return ".".join(partes), expira - ahora


def leer_token(token: str) -> dict[str, Any]:
    """Verifica la firma y la vigencia. Devuelve los claims.

    Raise:
        ErrorDeToken: si la firma no cuadra o el formato no es el esperado.
        TokenCaducado: si la firma cuadra pero ya paso el `exp`.
    """
    if not token:
        raise ErrorDeToken("token vacio")

    partes = token.split(".")
    if len(partes) != 3:
        raise ErrorDeToken("el token no tiene tres partes")

    cabecera_b64, cuerpo_b64, firma_b64 = partes

    try:
        firma = _b64d(firma_b64)
        esperada = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            f"{cabecera_b64}.{cuerpo_b64}".encode("ascii"),
            hashlib.sha256,
        ).digest()
    except (ValueError, TypeError) as exc:
        raise ErrorDeToken("el token no es base64 valido") from exc

    if not hmac.compare_digest(firma, esperada):
        raise ErrorDeToken("la firma no cuadra")

    try:
        cabecera = json.loads(_b64d(cabecera_b64))
        cuerpo = json.loads(_b64d(cuerpo_b64))
    except (ValueError, TypeError) as exc:
        raise ErrorDeToken("el token no es JSON valido") from exc

    # Se comprueba DESPUES de la firma a proposito. Si `alg` fuera "none" o
    # algo fuera de la lista, pasaria sin verificar nada, porque esa parte la
    # controla el atacante. Verificar primero significa que solo un token
    # firmado por nosotros puede decirnos que algoritmo usa.
    if not isinstance(cabecera, dict) or cabecera.get("alg") != "HS256":
        raise ErrorDeToken("algoritmo no soportado")

    if not isinstance(cuerpo, dict):
        raise ErrorDeToken("el cuerpo del token no es un objeto")

    if cuerpo.get("typ") != "access":
        raise ErrorDeToken("no es un token de acceso")

    expira = cuerpo.get("exp")
    if not isinstance(expira, int):
        raise ErrorDeToken("el token no lleva exp")

    if expira <= int(time.time()):
        raise TokenCaducado("el token ya caduco")

    if not cuerpo.get("sub"):
        raise ErrorDeToken("el token no lleva sub")

    return cuerpo


def segundos_vividos(token: str) -> int:
    """Cuanto le queda al token. Para que el cliente avise antes de que caduque
    en vez de descubrirlo en la siguiente peticion fallida."""
    return int(leer_token(token)["exp"] - time.time())
