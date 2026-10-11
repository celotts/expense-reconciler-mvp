"""Cuantos intentos de login caben antes de frenar.

Existe porque sin esto el login es una funcion de fuerza bruta sin coste:
`scrypt` pone lento cada intento, pero 100 intentos por minuto contra una
cuenta con una contrasena de 8 caracteres da 100*~16 = 1.6 millones de
pruebas al dia, y una contrasena corta es corta.

QUE RESUELVE Y QUE NO

Limita por combinacion de (cuente, direccion IP), no por IP sola ni por cuenta
sola:

- Por cuenta sola, un atacante desde su maquina bloquea el acceso de la
  persona que intenta trabajar.
- Por IP sola, en una oficina todos comparten la salida a internet y veinte
  personas legitimas se bloquean entre ellas.

LIMITE CONOCIDO, Y ES REAL

El contador vive en memoria del proceso. En varias réplicas del API cada una
cuenta por separado, asi que el limite efectivo se multiplica por el numero de
replicas; y se reinicia en cada despliegue. Es la solucion correcta para una
sola instancia, que es como corre este proyecto hoy. Cuando corran mas de una,
esto tiene que pasar a la base o a Redis, y el cambio es local a este modulo:
la API del login no va a saber de donde sale el veredicto.
"""

from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from time import monotonic

# Intentos fallidos que se permiten por ventana.
MAX_INTENTOS = 5

# Ventana en segundos. Cinco intentos en un minuto es un error de tecleo; cinco
# en diez minutos ya es alguien probando.
VENTANA_SEGUNDOS = 60.0

# Cuanto tiempo se retiene una entrada (cuenta, IP) que ya no sirve: un bloqueo
# que expiro o una cola de intentos que se vacio. Sin este techo, las entradas
# de los que alguna vez intentsaron algo se quedan para siempre.
RETENCION_SEGUNDOS = 900.0

_intentos: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_bloqueados: dict[tuple[str, str], float] = {}
_candado = Lock()


def _clave(correo: str, ip: str) -> tuple[str, str]:
    return correo.strip().lower(), ip


def _purgar(hasta: float) -> None:
    """Saca lo que ya no sirve.

    Sin esto el diccionario crece sin limite: cada intento con un correo
    distinto deja una entrada que no se vuelve a mirar nunca. Eso es una fuga
    de memoria que ademas amplifica por si sola, porque basta con mandar
    correos distintos para que el proceso crezca.
    """
    for clave in [k for k, ts in _bloqueados.items() if ts <= hasta]:
        del _bloqueados[clave]
    for clave in [k for k, v in _intentos.items() if not v or v[-1] <= hasta]:
        del _intentos[clave]


def esta_bloqueado(correo: str, ip: str) -> int:
    """Segundos que quedan de bloqueo. 0 si no esta bloqueado."""
    clave = _clave(correo, ip)
    with _candado:
        ahora = monotonic()
        _purgar(ahora - RETENCION_SEGUNDOS)
        hasta = _bloqueados.get(clave)
        if hasta is None:
            return 0
        return max(0, int(hasta - ahora))


def anotar_intento_fallido(correo: str, ip: str) -> int:
    """Registra un fallo. Devuelve los segundos de bloqueo si acaba de bloquear."""
    clave = _clave(correo, ip)
    with _candado:
        ahora = monotonic()
        _purgar(ahora - RETENCION_SEGUNDOS)
        cola = _intentos[clave]
        cola.append(ahora)
        while cola and cola[0] <= ahora - VENTANA_SEGUNDOS:
            cola.popleft()

        if len(cola) < MAX_INTENTOS:
            return 0

        _bloqueados[clave] = ahora + VENTANA_SEGUNDOS
        _intentos.pop(clave, None)
        return int(VENTANA_SEGUNDOS)


def anotar_intento_exitoso(correo: str, ip: str) -> None:
    """Olvida los fallos de esa combinacion.

    Sin esto, meter la contrasena correcta cinco veces por un error de tecleo y
    luego acertar deja el par bloqueado hasta que expire la ventana. Quien se
    equivoco ya demostro que sabe la contrasena, y su penalizacion se
    reinicia.
    """
    clave = _clave(correo, ip)
    with _candado:
        _intentos.pop(clave, None)
        _bloqueados.pop(clave, None)


def reiniciar() -> None:
    """Solo para tests."""
    with _candado:
        _intentos.clear()
        _bloqueados.clear()
