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
