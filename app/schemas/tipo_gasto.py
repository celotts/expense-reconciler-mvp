"""Lo que entra y lo que sale del catalogo de cuentas.

`extra="forbid"` en todo lo que se escribe, por la regla 23 y sus vecinas: un
cliente que reenvia la fila entera —porque la leyo de la pantalla— tiene que
recibir un 422 con el nombre del campo, no un 200 que finge haber guardado lo que
le mandaron.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TipoGastoCreate(BaseModel):
    """Alta de una cuenta.

    `clave` es el codigo que agrupa el cierre; `nombre` es lo que ve la persona.
    Se separan porque quien da de alta una cuenta piensa en la palabra que va a
    leer en el reporte, no en un codigo.
    """

    clave: str = Field(min_length=1, max_length=50)
    nombre: str = Field(min_length=1, max_length=120)
    variable: bool = True

    model_config = ConfigDict(extra="forbid")


class TipoGastoUpdate(BaseModel):
    """Edicion de una cuenta.

    NO lleva `clave`: renombrar el codigo cambiaria el historico de un mes que ya
    se cerro, y ese documento tiene que quedarse igual. El nombre si se cambia,
    porque es lo que ve la persona.

    `exclude_unset` y no `exclude_none` (regla 22): con `exclude_none`, un
    `PATCH {"nombre": "x"}` de una cuenta variable la volveria fija y el 200
    llegaría igual.
    """

    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    variable: bool | None = None
    activo: bool | None = None

    model_config = ConfigDict(extra="forbid")


class TipoGastoResponse(BaseModel):
    """Una cuenta, como la ve la pantalla."""

    id: UUID
    company_id: UUID
    clave: str
    nombre: str
    variable: bool
    activo: bool
    # Cuantos gastos y cuantos productos la usan. Lo que hace que dar de baja una
    # cuenta en uso sea una decision informada y no una sorpresa: la pantalla
    # puede decir "38 gastos la usan" antes de pedir la confirmacion.
    gastos: int = 0
    productos: int = 0


class AsignarTipoGastoRequest(BaseModel):
    """Ponerle (o quitarle) una cuenta a un gasto o a un producto.

    `tipo_gasto_id = None` deja el gasto SIN CLASIFICAR, que es una respuesta
    valida y no un error: significa "todavia no lo se".
    """

    tipo_gasto_id: UUID | None = None

    model_config = ConfigDict(extra="forbid")


class AsignarLoteRequest(BaseModel):
    """Ponerle (o quitarle) la cuenta a varios tickets a la vez.

    Es la misma decision que `AsignarTipoGastoRequest` y un solo destino para
    todos; el lote existe para no hacer un viaje por ticket cuando la barra de
    seleccion tiene 200. No lleva `company_id` ni quien firma (regla 23).
    """

    ticket_ids: list[UUID] = Field(min_length=1, max_length=500)
    tipo_gasto_id: UUID | None = None

    model_config = ConfigDict(extra="forbid")


class AsignarLoteResponse(BaseModel):
    """Cuantos se escribieron y cuantos se pidieron.

    Si `actualizados` es menor que `pedidos`, algunos ids no estaban en la
    empresa activa. El front lo dice en vez de fingir que clasifico todo.
    """

    actualizados: int
    pedidos: int


class AutorizarRequest(BaseModel):
    """Aprobar un gasto.

    NO lleva `autorizado_por`: sale del token (regla 23). Aceptarlo del cuerpo
    seria una puerta para autorizar en nombre de otra persona.
    """

    model_config = ConfigDict(extra="forbid")


class RechazarRequest(BaseModel):
    """Rechazar un gasto. El motivo es obligatorio y lo dice el schema, no solo
    el servicio: el 422 sale antes de tocar la base."""

    motivo: str = Field(min_length=3, max_length=500)

    model_config = ConfigDict(extra="forbid")


class AutorizacionResponse(BaseModel):
    """Como quedo la autorizacion, con quien y cuando."""

    ticket_id: UUID
    estado: str
    autorizado_por: str | None = None
    autorizado_at: datetime | None = None
    motivo_rechazo: str | None = None


class ConteoAutorizacion(BaseModel):
    """Cuantos hay de cada estado. Para la cabecera de la pantalla."""

    pendientes: int
    aprobadas: int
    rechazadas: int