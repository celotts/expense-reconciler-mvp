"""Reloj del servidor, en un solo lugar.

Existe por dos razones concretas, no por estetica:

1. `datetime.utcnow()` esta deprecado desde Python 3.12 y devuelve un valor
   naive. Insertar un naive en una columna `TIMESTAMP WITH TIME ZONE` hace que
   Postgres lo interprete en el TimeZone de la sesion. Si ese TimeZone no es
   UTC, la fecha guardada se desplaza horas y todos los calculos derivados
   (antiguedad de la cola de revision, ventanas de conciliacion) salen
   corridos. Un error de horas es dificil de detectar porque todo se ve
   "casi bien".

2. Comparar un naive con un aware lanza TypeError. Una excepcion al calcular
   la antiguedad de la cola deja la pantalla en blanco sin explicacion.

La regla del proyecto es una sola: toda fecha que se persiste es aware, en UTC.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Instante actual, con zona horaria, en UTC."""
    return datetime.now(timezone.utc)


def como_utc(instante: datetime) -> datetime:
    """Normaliza un instante que viene de la base a UTC con zona.

    Hace falta porque la misma columna se lee de dos maneras segun el motor:

    - Postgres devuelve el `TIMESTAMP WITH TIME ZONE` con zona, en la zona de
      la sesion.
    - SQLite no tiene tipos con zona: devuelve el valor tal cual se guardo, y
      `aiosqlite` lo entrega naive aunque se haya insertado un aware.

    Restar un naive de un aware lanza TypeError. El sintoma es una pantalla en
    blanco al pedir la cola, sin error de validacion y sin nada que apunte al
    reloj: el fallo aparece en la capa de presentacion y la causa esta en el
    almacenamiento.

    Un naive que llega de una columna que se definio con zona se interpreta en
    UTC, no en hora local: es lo que se guardo, y desplazarlo otra vez seria
    restar dos veces la misma conversion. Por eso se fija UTC y no
    `astimezone()` a la zona local, que daria un numero de dias distinto
   dependiento de donde corra el servidor.
    """
    if instante.tzinfo is None:
        return instante.replace(tzinfo=timezone.utc)
    return instante.astimezone(timezone.utc)


def dias_desde(instante: datetime, *, desde: datetime | None = None) -> float:
    """Dias transcurridos desde `instante`, sin importar si trae zona.

    Devuelve un float, no un int, porque a las 23:59 de un ticket que se creo
    ayer hay 0.02 dias, y redondear a 0 haria que la pantalla dijera "hoy" para
    algo que envejecio ayer. Un dia fraccionario tambien deja ver que una cola
    con 12 horas de retraso es distinta de una con 12 dias.
    """
    origen = desde if desde is not None else utcnow()
    return (como_utc(origen) - como_utc(instante)).total_seconds() / 86400
