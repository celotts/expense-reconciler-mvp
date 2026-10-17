"""El catalogo de cuentas de gasto, y la cuenta que tiene cada gasto.

## QUE HACE ESTE SERVICIO Y POR QUE NO ES UN CRUD MAS

Tres cosas, y la tercera es la que no se ve:

1. **El catalogo.** Alta, edicion y baja. Sin borrado: una cuenta que ya tiene
   gastos contabilizados se da de baja con `activo=false`, porque borrarla deja
   el reporte de un mes ya cerrado sin poder decir en que cuenta estaba el gasto.
   Es la regla 24 aplicada a la tabla que no existia.

2. **La asignacion del gasto**, y aqui esta lo importante: al asignar
   `tipo_gasto_id` se escribe **el mismo `category` de texto en la misma
   operacion**. No es una comodidad: `analitica.py`, `reporte_cierre.py` y la
   exportacion a CONTPAQI agrupan por ese texto, y si el id y el texto pueden
   separarse el cierre dice una cosa y la pantalla otra. Una copia con un solo
   escritor y un test que muere si se separan es un cache; dos que se escriben
   solas es una segunda verdad.

3. **La validacion de empresa.** No hay multi-tenancy (`get_current_user` no
   recibe `company_id`), asi que comparar aqui es la ultima linea antes de que
   una cuenta de una empresa acabe en el reporte de otra.

## Y LA AUTORIZACION, QUE ESTA SEPARADA A PROPOSITO

Autorizar un gasto NO es lo mismo que haberlo leido bien: `ExtractionStatus`
responde "como se leyo el papel" y `AUTO_APROBADO` significa que el automatismo
se responsabilizo, no que alguien lo aprobo. Son dos preguntas, dos columnas y
esta funcion es la que responde la segunda.

Quien autoriza sale del token que ya resolvio el router, nunca del cuerpo
(regla 23).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.categorias import CATEGORIAS
from app.core.enums import EstadoAutorizacion
from app.models.ticket import TicketModel
from app.models.tipo_gasto import TipoGastoModel


class ErrorDeTipoGasto(ValueError):
    """Lo que el operador hizo mal al dar de alta o asignar una cuenta."""


# --- El catalogo ---------------------------------------------------------


def _clave_normalizada(clave: str) -> str:
    """La clave tal como se guarda: mayusculas y sin espacios de sobra.

    El `CHECK (clave = upper(clave))` de la base existe para que un `psql` no
    pueda meter una clave que el servicio nunca produciria. Aqui se normaliza
    porque el usuario escribe "renta" y "Renta" son la misma cuenta: si se
    guardaran asi, el cierre las contaria como dos.
    """
    limpio = (clave or "").strip().upper()
    if not limpio:
        raise ErrorDeTipoGasto("La clave de la cuenta no puede quedar vacia.")
    return limpio


def _nombre_normalizado(nombre: str) -> str:
    limpio = (nombre or "").strip()
    if not limpio:
        raise ErrorDeTipoGasto("El nombre de la cuenta no puede quedar vacio.")
    return limpio


async def asegurar_catalogo(db: AsyncSession, company_id: UUID) -> int:
    """Siembra las cuentas basicas de una empresa. Idempotente.

    ## POR QUE NO ESTA EN SQL

    Porque el catalogo es POR EMPRESA y una migracion no sabe que empresa va a
    existir: sembrarlo en SQL seria sembrarlo para una empresa inventada. Las
    columnas si estan en `init.sql` y en la migracion; los DATOS son la unica
    cosa que depende de quien este usando el sistema.

    ## POR QUE ES IDEMPOTENTE Y NO "INSERT OR FAIL"

    Porque se llama desde la creacion de empresa (donde un fallo de red deja la
    empresa creada sin catalogo) y desde el boton de la pantalla. Con
    `ON CONFLICT DO NOTHING` se puede llamar las veces que haga falta sin que la
    segunda sea un error, y lo que devuelve es cuantas anadio de verdad, que es
    lo que la pantalla dice.
    """
    existentes = await db.execute(
        select(TipoGastoModel.clave).where(TipoGastoModel.company_id == company_id)
    )
    ya_estan = {fila[0] for fila in existentes.scalars()}
    faltantes = [c for c in CATEGORIAS if c.clave not in ya_estan]
    for categoria in faltantes:
        db.add(
            TipoGastoModel(
                id=uuid.uuid4(),
                company_id=company_id,
                clave=categoria.clave,
                nombre=categoria.etiqueta,
                variable=categoria.variable,
                activo=True,
            )
        )
    if faltantes:
        await db.commit()
    return len(faltantes)


async def listar(
    db: AsyncSession, company_id: UUID, *, incluir_inactivos: bool = False
) -> list[TipoGastoModel]:
    """Las cuentas de la empresa, en el orden en que se leen mejor.

    Las activas primero y despues por nombre, no por clave: quien esta eligiendo
    una cuenta esta buscando "Combustible", no "COMBUSTIBLE". Las dadas de baja
    al final y solo si se piden, porque un gasto viejo puede apuntar a una cuenta
    que ya no esta en el catalogo y esa fila tiene que poder seguir mostrandose.
    """
    consulta = select(TipoGastoModel).where(TipoGastoModel.company_id == company_id)
    if not incluir_inactivos:
        consulta = consulta.where(TipoGastoModel.activo.is_(True))
    consulta = consulta.order_by(TipoGastoModel.activo.desc(), TipoGastoModel.nombre)
    return list((await db.execute(consulta)).scalars().all())


async def obtener(db: AsyncSession, company_id: UUID, tipo_id: UUID) -> TipoGastoModel:
    """Una cuenta, o `None`.

    El `company_id` va DENTRO del filtro y no se compara despues, a proposito: si
    la cuenta fuera de otra empresa, filtrarla aqui devuelve `None` y la pantalla
    dice "no existe", que es la respuesta correcta. Comparar despues deja una
    fila ajena en memoria durante el tiempo que dura el trabajo.
    """
    resultado = await db.execute(
        select(TipoGastoModel).where(
            TipoGastoModel.id == tipo_id, TipoGastoModel.company_id == company_id
        )
    )
    return resultado.scalar_one_or_none()


async def crear(
    db: AsyncSession,
    company_id: UUID,
    *,
    clave: str,
    nombre: str,
    variable: bool = True,
) -> TipoGastoModel:
    clave_limpia = _clave_normalizada(clave)
    nombre_limpio = _nombre_normalizado(nombre)

    repetida = await db.execute(
        select(TipoGastoModel.id).where(
            TipoGastoModel.company_id == company_id,
            TipoGastoModel.clave == clave_limpia,
        )
    )
    if repetida.scalar_one_or_none() is not None:
        raise ErrorDeTipoGasto(f"La cuenta {clave_limpia} ya existe.")

    tipo = TipoGastoModel(
        id=uuid.uuid4(),
        company_id=company_id,
        clave=clave_limpia,
        nombre=nombre_limpio,
        variable=variable,
        activo=True,
    )
    db.add(tipo)
    try:
        await db.commit()
    except IntegrityError as exc:
        # El indice unico `(company_id, clave)` tambien salta aqui, y sin esto
        # el error le llegaria al operador como un 500 de SQL. La comprobacion de
        # arriba es la que da el mensaje bueno; esta es la que aguanta la carrera
        # entre dos altas simultaneas.
        await db.rollback()
        raise ErrorDeTipoGasto(f"La cuenta {clave_limpia} ya existe.") from exc
    await db.refresh(tipo)
    return tipo


async def actualizar(
    db: AsyncSession,
    company_id: UUID,
    tipo_id: UUID,
    *,
    nombre: str | None = None,
    variable: bool | None = None,
    activo: bool | None = None,
) -> TipoGastoModel:
    """Edita una cuenta. La clave NO se edita.

    ## POR QUE LA CLAVE NO SE EDITA

    Porque es la que agrupa el cierre y la que va al archivo del contador, y las
    dos son cosas ya emitidas. Renombrar "COMBUSTIBLE" a "GASOLINA" cambiaria el
    historico de un mes que ya se cerro, que es exactamente el documento que debe
    quedarse igual. El nombre si se edita, porque es lo que ve la persona.

    ## `activo` Y POR QUE NO HAY BORRADO

    Regla 24: dar de baja es `activo=false` y la fila se queda. Un gasto ya
    contabilizado apunta a ella para siempre.
    """
    tipo = await obtener(db, company_id, tipo_id)
    if tipo is None:
        raise ErrorDeTipoGasto("Esa cuenta de gasto no existe.")

    if nombre is not None:
        tipo.nombre = _nombre_normalizada(nombre)
    if variable is not None:
        tipo.variable = variable
    if activo is not None:
        tipo.activo = activo

    await db.commit()
    await db.refresh(tipo)
    return tipo


# --- La cuenta de un gasto ----------------------------------------------


async def asignar_a_ticket(
    db: AsyncSession, company_id: UUID, ticket_id: UUID, tipo_gasto_id: UUID | None
) -> None:
    """Asigna (o quita) la cuenta de un gasto. Escribe el texto a la vez.

    ## POR QUE EL TEXTO SE ESCRIBE AQUÍ Y NO EN EL ROUTER

    Porque si cada quien escribe el id por un lado y el texto por otro, un dia
    queda un gasto con el id puesto y el texto viejo, y el cierre lo reporta en la
    cuenta anterior. Las dos columnas se escriben en la MISMA operacion, o no se
    escriben.

    ## POR QUE COMPARA LA EMPRESA DENTRO DEL SERVICIO

    Porque no hay multi-tenancy: `get_current_user` no recibe `company_id` y
    cualquiera puede mandar el de otra. Esta comparacion es la ultima linea antes
    de que una cuenta acabe en el reporte de otra empresa, y por eso vive aqui y
    no en el router.
    """
    ticket = await db.execute(
        select(TicketModel).where(
            TicketModel.id == ticket_id, TicketModel.company_id == company_id
        )
    )
    ticket = ticket.scalar_one_or_none()
    if ticket is None:
        raise ErrorDeTipoGasto("Ese ticket no existe.")

    if tipo_gasto_id is None:
        # Quitar la cuenta deja el gasto SIN CLASIFICAR. Y el texto tambien se
        # limpia: dejar la etiqueta de una cuenta que ya no esta seria el peor de
        # los dos mundos, un gasto que el reporte cuenta en una cuenta y la
        # pantalla muestra en otra.
        await db.execute(
            update(TicketModel)
            .where(TicketModel.id == ticket_id)
            .values(tipo_gasto_id=None, category=None)
        )
    else:
        tipo = await obtener(db, company_id, tipo_gasto_id)
        if tipo is None:
            raise ErrorDeTipoGasto("Esa cuenta de gasto no existe.")
        if not tipo.activo:
            raise ErrorDeTipoGasto(
                f"La cuenta {tipo.clave} esta dada de baja. Reactivala para usarla."
            )
        await db.execute(
            update(TicketModel)
            .where(TicketModel.id == ticket_id)
            .values(tipo_gasto_id=tipo.id, category=tipo.clave)
        )
    await db.commit()


async def asignar_a_producto(
    db: AsyncSession, company_id: UUID, producto_id: UUID, tipo_gasto_id: UUID | None
) -> None:
    """La cuenta por omision de un producto. No toca ningun gasto.

    Que NO se copie al ticket a proposito: lo mismo puede ir a despensa este mes y
    a un evento el siguiente, y heredarlo seria decidir por la persona en que
    cuenta va un gasto.
    """
    from app.models.inventario import ProductoModel

    producto = await db.execute(
        select(ProductoModel).where(
            ProductoModel.id == producto_id, ProductoModel.company_id == company_id
        )
    )
    producto = producto.scalar_one_or_none()
    if producto is None:
        raise ErrorDeTipoGasto("Ese producto no existe.")

    if tipo_gasto_id is not None:
        tipo = await obtener(db, company_id, tipo_gasto_id)
        if tipo is None:
            raise ErrorDeTipoGasto("Esa cuenta de gasto no existe.")
        producto.tipo_gasto_id = tipo.id
    else:
        producto.tipo_gasto_id = None
    await db.commit()


# --- La autorizacion ----------------------------------------------------


async def autorizar(
    db: AsyncSession,
    company_id: UUID,
    ticket_id: UUID,
    *,
    usuario: str,
    motivo: str | None = None,
) -> EstadoAutorizacion:
    """Aprueba un gasto. Quien aprueba sale del token (`usuario`), no del cuerpo.

    Un gasto RECHAZADA puede volver a aprobarse y uno APROBADA puede rechazarse: la
    decision se cambia, no se reescribe. El trigger de la base
    (`trg_tickets_autorizacion_inmutable`) prohibe UPDATE de las cuatro columnas de
    autorizacion, asi que ESTA operacion tiene que pasar por
    `_cambiar_estado_de_autorizacion`, que hace el UPDATE permitiendolo.
    """
    return await _cambiar_estado_de_autorizacion(
        db, company_id, ticket_id, EstadoAutorizacion.APROBADA, usuario, motivo
    )


async def rechazar(
    db: AsyncSession,
    company_id: UUID,
    ticket_id: UUID,
    *,
    usuario: str,
    motivo: str | None = None,
) -> EstadoAutorizacion:
    """Rechaza un gasto. El motivo es OBLIGATORIO (regla del enum).

    Sin motivo, un rechazo es una queja y no un dato: no se puede aprender de el
    ni corregir lo que señalo. La constraint `ck_tickets_autorizacion_firmada` lo
    prohibe igual que esta funcion, para que un UPDATE a mano tampoco lo evite.
    """
    if not (motivo or "").strip():
        raise ErrorDeTipoGasto("Para rechazar un gasto hay que decir por que.")
    return await _cambiar_estado_de_autorizacion(
        db, company_id, ticket_id, EstadoAutorizacion.RECHAZADA, usuario, motivo.strip()
    )


async def _cambiar_estado_de_autorizacion(
    db: AsyncSession,
    company_id: UUID,
    ticket_id: UUID,
    estado: EstadoAutorizacion,
    usuario: str,
    motivo: str | None,
) -> EstadoAutorizacion:
    """El unico camino por el que se mueve la autorizacion.

    ## POR QUE HACE FALTA UN CAMINO PROPIO

    Porque el trigger de la base prohibe que se REESCRIBA una autorizacion, y
    cambiar el estado ES reescribirla. La salida no es aflojar el trigger —eso
    dejaria pasar el UPDATE de cualquiera— sino una bandera de sesion de Postgres:

        SET LOCAL sesion.autorizacion_permitida = 'si'

    que el trigger lee y que `SET LOCAL` vuelve a bajar sola al cerrar la
    transaccion. Si alguien llama a `db.execute(update(TicketModel)...)` sobre
    `estado_autorizacion` por fuera de aqui, el trigger lo rechaza.

    ## POR QUE EL `SET LOCAL` VA DENTRO DE LA MISMA TRANSACCION

    Porque si fuera una sesion normal habria que acordarse de limpiarla, y el
    camino que olvida limpiar es el que deja la tabla sin proteccion para lo que
    venga despues. Con `SET LOCAL` no hay nada que acordar: dura lo que la
    transaccion, ni un statement mas.
    """
    resultado = await db.execute(
        select(TicketModel).where(
            TicketModel.id == ticket_id, TicketModel.company_id == company_id
        )
    )
    ticket = resultado.scalar_one_or_none()
    if ticket is None:
        raise ErrorDeTipoGasto("Ese ticket no existe.")

    ahora = datetime.now(timezone.utc)
    await db.execute(text("SET LOCAL sesion.autorizacion_permitida = 'si'"))
    await db.execute(
        update(TicketModel)
        .where(TicketModel.id == ticket_id)
        .values(
            estado_autorizacion=estado.value,
            autorizado_por=usuario,
            autorizado_at=ahora,
            motivo_rechazo=motivo if estado is EstadoAutorizacion.RECHAZADA else None,
        )
    )
    await db.commit()
    return estado


async def pendientes_de_autorizar(
    db: AsyncSession, company_id: UUID, *, limite: int = 200
) -> list[TicketModel]:
    """La cola de autorizacion: los gastos que todavia nadie ha decidido.

    Solo los que siguen `PENDIENTE`. Un gasto sin cuenta se incluye y la pantalla
    lo avisa: autorizar el monto sin saber en que cuenta va es una autorizacion a
    medias, y el cierre lo detecta despues como gasto sin clasificar.
    """
    consulta = (
        select(TicketModel)
        .where(
            TicketModel.company_id == company_id,
            TicketModel.estado_autorizacion == EstadoAutorizacion.PENDIENTE.value,
        )
        .order_by(TicketModel.created_at.desc())
        .limit(limite)
    )
    return list((await db.execute(consulta)).scalars().all())


async def conteo_por_estado(db: AsyncSession, company_id: UUID) -> dict[str, int]:
    """Cuantos hay de cada estado. Sale del grupo, no de contar tres veces.

    Tres `count()` por estado serian tres viajes a la base y, peor, tres cifras
    que pueden no sumar lo mismo que la pantalla de al lado.
    """
    resultado = await db.execute(
        select(TicketModel.estado_autorizacion, func.count())
        .where(TicketModel.company_id == company_id)
        .group_by(TicketModel.estado_autorizacion)
    )
    conteo = {estado.value: 0 for estado in EstadoAutorizacion}
    for estado, cantidad in resultado.all():
        conteo[estado] = cantidad
    return conteo


__all__ = [
    "ErrorDeTipoGasto",
    "asegurar_catalogo",
    "listar",
    "obtener",
    "crear",
    "actualizar",
    "asignar_a_ticket",
    "asignar_a_producto",
    "autorizar",
    "rechazar",
    "pendientes_de_autorizar",
    "conteo_por_estado",
]