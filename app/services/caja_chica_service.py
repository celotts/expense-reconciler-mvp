"""Reglas de negocio de la caja chica.

El saldo se mueve con una actualizacion condicional en la MISMA transaccion que
el movimiento. `SELECT ... FOR UPDATE` se usa en Postgres; en SQLite se confia
en la condicion `WHERE ... AND saldo_actual + delta >= 0`, que es la que decide
si la operacion ocurre. Las dos variantes comparten el criterio: si la fila no
se actualizo, no se escribe el movimiento y se devuelve un conflicto.

Eso evita el fallo caro: dos gastos que leen el mismo saldo y escriben dos filas
dejando el saldo por debajo de cero, con cada movimiento "valido" por separado.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EstadoCaja, OrigenFondo, RolEmpresa, TipoMovimientoCaja
from app.core.time import utcnow
from app.models.bank_transaction import BankTransactionModel
from app.models.caja_chica import CajaChicaModel, MovimientoCajaModel
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.models.user import UserModel
from app.schemas.caja_chica import (
    CajaChicaCreate,
    CajaChicaUpdate,
    ConciliacionCajaResponse,
    MovimientoCajaCreate,
    ReposicionCruzada,
)
from app.services.auditoria import registrar_auditoria

_CENTAVOS = Decimal("0.01")


class ErrorCaja(Exception):
    """Un conflicto de negocio, con el codigo HTTP que le corresponde."""

    def __init__(self, detail: str, status_code: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _centavos(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(_CENTAVOS, rounding=ROUND_HALF_UP)


async def _auditar(
    db: AsyncSession,
    *,
    accion: str,
    registro_id: UUID,
    usuario_id: UUID,
    rol: RolEmpresa,
    detalles: dict,
) -> None:
    """Escribe el rastro en la MISMA transaccion que la operacion.

    Pasar `db` no es comodidad de tests: `registrar_auditoria` con sesion
    explicita propaga el error en vez de tragarlo, de modo que una caja no quede
    confirmada sin su rastro. Quien firma (`usuario_id`) y el rol salen de la
    dependencia, nunca del cuerpo (regla 23 de `AGENTS.md`).
    """
    await registrar_auditoria(
        accion=accion,
        modulo="caja_chica",
        usuario_id=str(usuario_id),
        rol=rol.value if isinstance(rol, RolEmpresa) else str(rol),
        registro_afectado=str(registro_id),
        detalles=detalles,
        db=db,
    )


async def _existe(db: AsyncSession, modelo, identificador: UUID) -> bool:
    return (
        await db.execute(select(modelo.id).where(modelo.id == identificador))
    ).scalar_one_or_none() is not None


async def crear_caja(
    db: AsyncSession,
    datos: CajaChicaCreate,
    usuario_id: UUID,
    *,
    company_id: UUID,
    rol: RolEmpresa,
) -> CajaChicaModel:
    # La empresa activa NO se toma del cuerpo: el cuerpo lo escribe el cliente y
    # por tanto puede mentir. La dependencia `empresa_activa` la resolvio en BD.
    if datos.company_id != company_id:
        raise ErrorCaja("La empresa de la caja no coincide con la empresa activa")
    if rol.es_operador and datos.responsable_id != usuario_id:
        raise ErrorCaja("Un operador solo puede abrir cajas a su cargo", 403)
    if not await _existe(db, CompanyModel, datos.company_id):
        raise ErrorCaja("La empresa no existe", 404)
    if not await _existe(db, UserModel, datos.responsable_id):
        raise ErrorCaja("El responsable no existe", 404)

    caja = CajaChicaModel(
        company_id=company_id,
        responsable_id=datos.responsable_id,
        nombre=datos.nombre,
        monto_inicial=_centavos(datos.monto_inicial),
        # El saldo nace igual al monto inicial: una caja recien abierta no puede
        # mostrar un saldo que nadie puso.
        saldo_actual=_centavos(datos.monto_inicial),
        umbral_alerta=_centavos(datos.umbral_alerta),
        estado=EstadoCaja.ABIERTA.value,
        opened_at=utcnow(),
    )
    db.add(caja)
    await db.flush()

    # La apertura es el primer movimiento del kardex. `saldo_actual` ya nacio
    # igual al monto inicial: la fila NO vuelve a sumarse, porque eso contaria
    # dos veces el mismo dinero. El movimiento es la EVIDENCIA de quien puso ese
    # fondo y cuando, y por eso lleva al que abre la caja como firmante.
    apertura = MovimientoCajaModel(
        company_id=company_id,
        caja_chica_id=caja.id,
        ticket_id=None,
        tipo=TipoMovimientoCaja.APERTURA.value,
        origen_fondo=None,
        banco_nombre=None,
        referencia_bancaria=None,
        monto=_centavos(datos.monto_inicial),
        notas=f"Apertura de la caja {datos.nombre}",
        created_by=usuario_id,
        created_at=utcnow(),
    )
    db.add(apertura)
    await _auditar(
        db,
        accion="crear_caja",
        registro_id=caja.id,
        usuario_id=usuario_id,
        rol=rol,
        detalles={
            "company_id": str(company_id),
            "nombre": datos.nombre,
            "monto_inicial": str(_centavos(datos.monto_inicial)),
            "responsable_id": str(datos.responsable_id),
        },
    )
    await db.commit()
    await db.refresh(caja)
    return caja


async def listar_cajas(
    db: AsyncSession,
    company_id: UUID,
    *,
    usuario_id: UUID | None = None,
    rol: RolEmpresa | None = None,
) -> list[CajaChicaModel]:
    stmt = select(CajaChicaModel).where(CajaChicaModel.company_id == company_id)
    if rol is not None and rol.es_operador and usuario_id is not None:
        stmt = stmt.where(CajaChicaModel.responsable_id == usuario_id)
    stmt = stmt.order_by(CajaChicaModel.opened_at)
    return list((await db.execute(stmt)).scalars().all())


async def obtener_caja(
    db: AsyncSession,
    caja_id: UUID,
    *,
    company_id: UUID,
    usuario_id: UUID | None = None,
    rol: RolEmpresa | None = None,
) -> CajaChicaModel:
    caja = (
        await db.execute(select(CajaChicaModel).where(CajaChicaModel.id == caja_id))
    ).scalar_one_or_none()
    # 404 y no 403: un 403 confirmaria que la caja existe en otra empresa.
    if caja is None or caja.company_id != company_id:
        raise ErrorCaja("La caja chica no existe", 404)
    if rol is not None and rol.es_operador and caja.responsable_id != usuario_id:
        raise ErrorCaja("Solo puedes operar las cajas a tu cargo", 403)
    return caja


async def actualizar_caja(
    db: AsyncSession,
    caja_id: UUID,
    datos: CajaChicaUpdate,
    *,
    company_id: UUID,
    usuario_id: UUID,
    rol: RolEmpresa,
) -> CajaChicaModel:
    caja = await obtener_caja(
        db, caja_id, company_id=company_id, usuario_id=usuario_id, rol=rol
    )
    cambios = datos.model_dump(exclude_unset=True)
    if caja.estado == EstadoCaja.CERRADA.value and cambios:
        raise ErrorCaja("Una caja cerrada no se puede modificar")
    if (
        rol is not None
        and rol.es_operador
        and "responsable_id" in cambios
        and cambios["responsable_id"] != usuario_id
    ):
        raise ErrorCaja("Un operador no puede traspasar la caja a otra persona", 403)
    for campo, valor in cambios.items():
        if campo == "responsable_id" and not await _existe(db, UserModel, valor):
            raise ErrorCaja("El responsable no existe", 404)
        setattr(caja, campo, valor)
    if cambios:
        await _auditar(
            db,
            accion="actualizar_caja",
            registro_id=caja.id,
            usuario_id=usuario_id,
            rol=rol,
            detalles={
                "company_id": str(caja.company_id),
                "nombre": caja.nombre,
                "cambios": [
                    {"campo": campo, "antes": str(getattr(caja, campo))}
                    for campo in cambios
                ],
            },
        )
    await db.commit()
    await db.refresh(caja)
    return caja


async def listar_movimientos(
    db: AsyncSession,
    caja_id: UUID,
    *,
    company_id: UUID,
    usuario_id: UUID | None = None,
    rol: RolEmpresa | None = None,
) -> list[MovimientoCajaModel]:
    await obtener_caja(
        db, caja_id, company_id=company_id, usuario_id=usuario_id, rol=rol
    )
    stmt = (
        select(MovimientoCajaModel)
        .where(MovimientoCajaModel.caja_chica_id == caja_id)
        .order_by(MovimientoCajaModel.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


async def cambiar_estado(
    db: AsyncSession,
    caja_id: UUID,
    estado: EstadoCaja,
    *,
    closed_at: datetime | None = None,
    efectivo_contado: Decimal | None = None,
    company_id: UUID,
    usuario_id: UUID,
    rol: RolEmpresa,
) -> CajaChicaModel:
    caja = await obtener_caja(
        db, caja_id, company_id=company_id, usuario_id=usuario_id, rol=rol
    )
    actual = EstadoCaja(caja.estado)
    if actual is EstadoCaja.CERRADA:
        raise ErrorCaja("Una caja cerrada no se puede reabrir")
    if efectivo_contado is not None and estado is not EstadoCaja.CERRADA:
        raise ErrorCaja("El efectivo contado solo se registra al cerrar", 422)

    if estado is EstadoCaja.CERRADA:
        if actual is not EstadoCaja.EN_REVISION:
            raise ErrorCaja("Solo se puede cerrar una caja en revision")
        caja.closed_at = closed_at or utcnow()
        caja.estado = estado.value
        if efectivo_contado is not None:
            # La diferencia la calcula el servidor con Decimal: dos clientes
            # redondeando por su cuenta dirian cosas distintas del mismo cierre.
            # No bloquea: el cierre es un hecho y la diferencia es un hallazgo.
            contado = _centavos(efectivo_contado)
            saldo = _centavos(caja.saldo_actual)
            caja.efectivo_contado = contado
            caja.diferencia_cierre = _centavos(contado - saldo)
            cierre = MovimientoCajaModel(
                company_id=caja.company_id,
                caja_chica_id=caja.id,
                ticket_id=None,
                tipo=TipoMovimientoCaja.CIERRE.value,
                origen_fondo=None,
                banco_nombre=None,
                referencia_bancaria=None,
                # El cierre NO mueve dinero: solo documenta el conteo. Un monto
                # distinto de cero se restaria de nuevo al sumar el kardex.
                monto=Decimal("0.00"),
                notas=(
                    f"Conteo {contado}; saldo {saldo}; "
                    f"diferencia {caja.diferencia_cierre}"
                ),
                created_by=usuario_id,
                created_at=utcnow(),
            )
            db.add(cierre)
    elif estado is EstadoCaja.EN_REVISION:
        if actual is not EstadoCaja.ABIERTA:
            raise ErrorCaja("Solo se puede enviar a revision una caja abierta")
        caja.estado = estado.value
    elif estado is EstadoCaja.ABIERTA:
        if actual is not EstadoCaja.EN_REVISION:
            raise ErrorCaja("Solo se puede reabrir una caja en revision")
        caja.estado = estado.value
    else:
        raise ErrorCaja("Estado de caja no reconocido", 422)

    accion = {
        EstadoCaja.CERRADA: "cerrar_caja",
        EstadoCaja.EN_REVISION: "enviar_caja_a_revision",
        EstadoCaja.ABIERTA: "reabrir_caja",
    }[estado]
    await _auditar(
        db,
        accion=accion,
        registro_id=caja.id,
        usuario_id=usuario_id,
        rol=rol,
        detalles={
            "company_id": str(caja.company_id),
            "nombre": caja.nombre,
            "estado_anterior": actual.value,
            "estado_nuevo": estado.value,
            "efectivo_contado": str(caja.efectivo_contado)
            if caja.efectivo_contado is not None
            else None,
            "diferencia_cierre": str(caja.diferencia_cierre)
            if caja.diferencia_cierre is not None
            else None,
        },
    )
    await db.commit()
    await db.refresh(caja)
    return caja


async def registrar_movimiento(
    db: AsyncSession,
    caja_id: UUID,
    datos: MovimientoCajaCreate,
    usuario_id: UUID,
    *,
    company_id: UUID,
    rol: RolEmpresa,
) -> MovimientoCajaModel:
    """Escribe un movimiento y mueve el saldo de forma atomica.

    El orden importa: primero se decide si el saldo puede cambiar (UPDATE
    condicional) y solo si la fila cambio se INSERTA el movimiento. Al reves,
    un movimiento sin saldo seria un kardex que cuenta dinero que no hay.
    """
    caja = await obtener_caja(
        db, caja_id, company_id=company_id, usuario_id=usuario_id, rol=rol
    )
    if datos.company_id is not None and datos.company_id != company_id:
        raise ErrorCaja("La empresa del movimiento no coincide con la de la caja")

    estado = EstadoCaja(caja.estado)
    if estado is EstadoCaja.CERRADA:
        raise ErrorCaja("La caja esta cerrada; no acepta movimientos")
    # La apertura la escribe crear_caja, que siembra el saldo. Aceptarla aqui
    # daria una segunda puerta para sumar dinero sin una caja nueva.
    if datos.tipo is TipoMovimientoCaja.APERTURA:
        raise ErrorCaja("La apertura solo se registra al crear la caja", 422)
    # El schema ya lo rechaza; esta es la segunda capa para quien llame al
    # servicio directamente sin pasar por la API.
    if datos.origen_fondo is not OrigenFondo.BANCO and (
        datos.banco_nombre or datos.referencia_bancaria
    ):
        raise ErrorCaja(
            "Los datos de banco solo valen si el fondo viene de banco", 422
        )
    if datos.tipo is TipoMovimientoCaja.CIERRE:
        if estado is not EstadoCaja.EN_REVISION:
            raise ErrorCaja("El cierre solo se registra con la caja en revision")
    elif estado is not EstadoCaja.ABIERTA:
        raise ErrorCaja("La caja no esta abierta; no acepta movimientos")

    ticket = None
    if datos.ticket_id is not None:
        ticket = (
            await db.execute(select(TicketModel).where(TicketModel.id == datos.ticket_id))
        ).scalar_one_or_none()
        if ticket is None:
            raise ErrorCaja("El ticket no existe", 404)
        if ticket.company_id != caja.company_id:
            raise ErrorCaja("El ticket pertenece a otra empresa")

    saldo_antes = caja.saldo_actual
    monto = _centavos(datos.monto)
    if datos.tipo.suma:
        delta = monto
    elif datos.tipo in (TipoMovimientoCaja.GASTO_TICKET, TipoMovimientoCaja.CIERRE, TipoMovimientoCaja.AJUSTE):
        delta = -monto if (datos.resta or datos.tipo is not TipoMovimientoCaja.AJUSTE) else monto
    else:
        raise ErrorCaja("Tipo de movimiento no reconocido", 422)

    # `FOR UPDATE` solo donde existe. SQLite no lo soporta y ahi la condicion
    # del UPDATE es la que hace de cerrojo.
    try:
        es_postgres = db.get_bind().dialect.name == "postgresql"
    except Exception:
        es_postgres = False
    if es_postgres:
        caja = (
            await db.execute(
                select(CajaChicaModel).where(CajaChicaModel.id == caja_id).with_for_update()
            )
        ).scalar_one()

    stmt = (
        update(CajaChicaModel)
        .where(
            and_(
                CajaChicaModel.id == caja_id,
                CajaChicaModel.saldo_actual + delta >= 0,
            )
        )
        .values(saldo_actual=CajaChicaModel.saldo_actual + delta)
        .execution_options(synchronize_session="fetch")
    )
    resultado = await db.execute(stmt)
    if resultado.rowcount != 1:
        # La condicion no se cumplio: otro movimiento gano la carrera o el saldo
        # no alcanza. `rowcount=0` significa que el UPDATE no toco ninguna fila,
        # asi que no hay nada que deshacer. No se llama a `rollback()` aqui:
        # eso expiraria TODOS los objetos de la sesion a mitad de la peticion,
        # incluido el usuario que acaba de resolver la empresa activa, y la
        # respuesta 409 se caeria con un `MissingGreenlet`. El cierre de la
        # sesion al terminar el request hace el rollback que haga falta.
        raise ErrorCaja(
            f"Saldo insuficiente en la caja: hay {saldo_antes} y se pidio "
            f"{'restar' if delta < 0 else 'sumar'} {monto}"
        )

    movimiento = MovimientoCajaModel(
        company_id=caja.company_id,
        caja_chica_id=caja.id,
        ticket_id=datos.ticket_id,
        tipo=datos.tipo.value,
        origen_fondo=datos.origen_fondo.value if datos.origen_fondo else None,
        banco_nombre=datos.banco_nombre,
        referencia_bancaria=datos.referencia_bancaria,
        monto=monto,
        notas=datos.notas,
        created_by=usuario_id,
        created_at=utcnow(),
    )
    db.add(movimiento)
    # Vincular el ticket a la caja es parte del mismo hecho que el gasto: si se
    # hiciera despues, un ticket podria quedar contado en dos cajas.
    if ticket is not None and datos.tipo is TipoMovimientoCaja.GASTO_TICKET:
        ticket.caja_chica_id = caja.id
    # El UUID se asigna al flush; la auditoria necesita el id REAL, no un None
    # que luego nadie pueda buscar.
    await db.flush()
    await _auditar(
        db,
        accion="registrar_movimiento_caja",
        registro_id=movimiento.id,
        usuario_id=usuario_id,
        rol=rol,
        detalles={
            "company_id": str(caja.company_id),
            "caja_chica_id": str(caja.id),
            "nombre": caja.nombre,
            "tipo": datos.tipo.value,
            "monto": str(monto),
            "origen_fondo": datos.origen_fondo.value if datos.origen_fondo else None,
            "ticket_id": str(datos.ticket_id) if datos.ticket_id else None,
        },
    )
    await db.commit()
    await db.refresh(movimiento)
    await db.refresh(caja)
    return movimiento


async def conciliar_reposiciones_bancarias(
    db: AsyncSession,
    caja_id: UUID,
    *,
    company_id: UUID,
    usuario_id: UUID | None = None,
    rol: RolEmpresa | None = None,
) -> ConciliacionCajaResponse:
    """Cruza las reposiciones desde banco de una caja contra `bank_transactions`.

    Es solo lectura. NO marca `is_reconciled` en el banco: esa columna responde
    "¿este cargo se cruzo con un ticket?", una pregunta distinta de "¿de donde
    salio el efectivo que se metio a la caja?". Escribirla aqui crearia dos
    verdades de "conciliado" y el tablero contaria el mismo peso dos veces.
    """
    await obtener_caja(
        db, caja_id, company_id=company_id, usuario_id=usuario_id, rol=rol
    )
    stmt = (
        select(MovimientoCajaModel)
        .where(
            MovimientoCajaModel.caja_chica_id == caja_id,
            MovimientoCajaModel.company_id == company_id,
            MovimientoCajaModel.tipo == TipoMovimientoCaja.REPOSICION.value,
            MovimientoCajaModel.origen_fondo == OrigenFondo.BANCO.value,
        )
        .order_by(MovimientoCajaModel.created_at)
    )
    reposiciones = list((await db.execute(stmt)).scalars().all())
    movimientos_banco = list(
        (
            await db.execute(
                select(BankTransactionModel).where(
                    BankTransactionModel.company_id == company_id
                )
            )
        ).scalars().all()
    )

    ya_usados: set[UUID] = set()
    filas: list[ReposicionCruzada] = []
    for repo in reposiciones:
        par = None
        monto = _centavos(repo.monto)
        referencia = (repo.referencia_bancaria or "").strip().upper()
        for banco in movimientos_banco:
            if banco.id in ya_usados:
                continue
            if abs(_centavos(banco.amount)) != monto:
                continue
            ref_banco = (banco.reference or "").strip().upper()
            # Si la reposicion nombra una referencia, tiene que coincidir: dos
            # cargos del mismo monto no son la misma salida de dinero.
            if referencia and ref_banco and referencia != ref_banco:
                continue
            par = banco
            break
        if par is not None:
            ya_usados.add(par.id)
        filas.append(
            ReposicionCruzada(
                movimiento_id=repo.id,
                fecha=repo.created_at,
                monto=monto,
                banco_nombre=repo.banco_nombre,
                referencia_bancaria=repo.referencia_bancaria,
                bank_transaction_id=par.id if par else None,
                fecha_banco=par.transaction_date if par else None,
                descripcion_banco=par.description if par else None,
                resultado="CRUZADA" if par else "SIN_CRUCE",
            )
        )

    cruzadas = sum(1 for fila in filas if fila.resultado == "CRUZADA")
    total_cruzado = _centavos(
        sum((fila.monto for fila in filas if fila.resultado == "CRUZADA"), Decimal("0.00"))
    )
    total_sin_cruzar = _centavos(
        sum((fila.monto for fila in filas if fila.resultado == "SIN_CRUCE"), Decimal("0.00"))
    )
    advertencia = None
    if not reposiciones:
        advertencia = "Esta caja no tiene reposiciones desde banco para cruzar"
    elif cruzadas != len(filas):
        advertencia = (
            f"{len(filas) - cruzadas} reposicion(es) no tienen cargo bancario "
            "que cuadre; revisa el monto y la referencia"
        )
    return ConciliacionCajaResponse(
        company_id=company_id,
        caja_chica_id=caja_id,
        reposiciones=filas,
        cruzadas=cruzadas,
        sin_cruzar=len(filas) - cruzadas,
        total_cruzado=total_cruzado,
        total_sin_cruzar=total_sin_cruzar,
        advertencia=advertencia,
    )