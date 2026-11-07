"""Los endpoints de los lotes de digitalizacion.

Router delgado a proposito: decide que se pide y como se responde, y nada mas.
La asignacion de tickets y archivos a un lote NO vive aqui: la hace
`scan_service` cuando lee, con este contrato:

- Un archivo solo entra a un lote ABIERTO o EN_REVISION. Un lote CERRADO no
  acepta mas archivos: el servicio tiene que comprobar `lote.estado` antes de
  escribir `lote_id` y rechazar con 409 si esta CERRADO.
- Al asignar, el servicio actualiza `lote.total_escaneado` e
  `lote.importe_total`. Esos dos son un CACHE; la cuenta en vivo esta en
  `GET /lotes/{id}`.

Sobre `company_id`, que viene del query o del cuerpo como en `scans.py`:
el usuario del token no lleva empresa (`UserModel` no tiene `company_id`) y
no hay multi-tenant, asi que el cliente dice a que empresa pertenece cada
lote. No es auth nueva y no es un permiso: es el mismo criterio que
`POST /scan` usa hoy.

Sobre la auditoria: cerrar y reabrir escriben en `auditoria` (la tabla que ya
existe, `0017`), con `modulo="lotes"`. No se inventa tabla nueva: la
reapertura es el evento que el contador tiene que poder explicar despues, y
"quien lo reabrio y por que" tiene que estar donde ya se busca todo lo demas.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import UsuarioActual
from app.core.time import utcnow
from app.models.auditoria import Auditoria
from app.models.company import CompanyModel
from app.models.lote import (
    ESTADO_ABIERTO,
    ESTADO_CERRADO,
    ESTADO_EN_REVISION,
    ESTADOS_LOTE,
    LoteModel,
    config_lote_para,
    periodo_de_fecha,
)
from app.models.scan_file import ScanFileModel
from app.models.ticket import TicketModel
from app.models.user import UserModel
from app.schemas.lote import (
    ComplementarioRequest,
    LoteCreate,
    LoteDetalleResponse,
    LoteResponse,
    ReabrirRequest,
)

router = APIRouter(tags=["Lotes"])

# Tope de la pagina de `GET /lotes`. Es el mismo numero que el tope por
# corrida del escaneo y por la misma razon: un `GET` sin tope sobre miles de
# lotes se come la memoria del proceso que sirve el resto de la API.
TOPE_PAGINA = 200


async def _lote_o_404(db: AsyncSession, lote_id: UUID) -> LoteModel:
    """El lote, o un 404 con nombre. Ver `_archivo_o_404` en `scans.py`."""
    lote = await db.get(LoteModel, lote_id)
    if lote is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El lote indicado no existe",
        )
    return lote


async def _empresa_o_404(db: AsyncSession, company_id: UUID) -> CompanyModel:
    """La empresa, o un 404. Es el mismo 404 que `POST /scan` devuelve."""
    empresa = await db.get(CompanyModel, company_id)
    if empresa is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La empresa indicada no existe",
        )
    return empresa


async def _siguiente_folio(
    db: AsyncSession,
    company_id: UUID,
    ejercicio: int,
    mes: int,
    prefijo: str,
) -> str:
    """El siguiente folio del mes: LOT-2026-10-001, -002, ...

    Se lee el maximo que ya existe y se suma uno. Dos creaciones a la vez
    pueden leer el mismo maximo: para eso esta la constraint unica
    (`ux_lotes_empresa_folio`), y el `POST` convierte el `IntegrityError` en
    un 409 que dice "reintenta". Un folio duplicado no se arregla con un
    numero mas grande en memoria: se arregla en la base.
    """
    patron = f"{prefijo}-{ejercicio:04d}-{mes:02d}-%"
    folios = (
        await db.execute(
            select(LoteModel.folio).where(
                LoteModel.company_id == company_id,
                LoteModel.folio.like(patron),
            )
        )
    ).scalars().all()
    maximo = 0
    for folio in folios:
        coincidencia = re.search(r"-(\d+)$", folio)
        if coincidencia:
            maximo = max(maximo, int(coincidencia.group(1)))
    return f"{prefijo}-{ejercicio:04d}-{mes:02d}-{maximo + 1:03d}"


def _auditar(
    db: AsyncSession,
    usuario: UserModel,
    accion: str,
    lote: LoteModel,
    detalles: dict,
) -> None:
    """Una fila en `auditoria` por un cambio de estado del lote.

    Misma forma que `POST /auditoria/evento`: `usuario_id` del token,
    `rol` del perfil, `modulo="lotes"`, y el detalle en JSON. Sin esto, una
    reapertura es un cambio de estado sin dueno.
    """
    db.add(
        Auditoria(
            usuario_id=usuario.id,
            rol=getattr(usuario, "perfil", None),
            accion=accion,
            modulo="lotes",
            registro_afectado=lote.id,
            detalles=json.dumps(
                {"folio": lote.folio, **detalles}, default=str
            ),
            fecha=utcnow(),
        )
    )


async def _conteos(db: AsyncSession, lote: LoteModel) -> dict:
    """Los conteos en vivo del detalle: archivos, tickets, importe, duplicados."""
    total_archivos = (
        await db.execute(
            select(func.count())
            .select_from(ScanFileModel)
            .where(ScanFileModel.lote_id == lote.id)
        )
    ).scalar_one()
    filas = (
        await db.execute(
            select(
                TicketModel.provider_name,
                TicketModel.total_amount,
                TicketModel.expense_date,
            ).where(TicketModel.lote_id == lote.id)
        )
    ).all()
    grupos = Counter(filas)
    duplicados = sum(cantidad - 1 for cantidad in grupos.values() if cantidad > 1)
    importe = sum(
        (fila.total_amount for fila in filas if fila.total_amount is not None),
        Decimal("0.00"),
    )
    return {
        "total_archivos": int(total_archivos),
        "total_tickets": len(filas),
        "importe_total_vivo": importe,
        "duplicados": duplicados,
    }


@router.get("", response_model=list[LoteResponse])
async def listar_lotes(
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
    company_id: UUID | None = Query(None),
    ejercicio: int | None = Query(None),
    mes: int | None = Query(None, ge=1, le=12),
    estado: str | None = Query(None),
    limit: int = Query(50, ge=1, le=TOPE_PAGINA),
    offset: int = Query(0, ge=0),
) -> list[LoteResponse]:
    """Los lotes, filtrados y paginados, del mas reciente al mas viejo."""
    del usuario
    consulta = select(LoteModel)
    if company_id is not None:
        consulta = consulta.where(LoteModel.company_id == company_id)
    if ejercicio is not None:
        consulta = consulta.where(LoteModel.ejercicio == ejercicio)
    if mes is not None:
        consulta = consulta.where(LoteModel.mes == mes)
    if estado is not None:
        if estado not in ESTADOS_LOTE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{estado!r} no es un estado de lote. Validos: "
                    + ", ".join(ESTADOS_LOTE)
                ),
            )
        consulta = consulta.where(LoteModel.estado == estado)
    lotes = (
        await db.execute(
            consulta.order_by(
                LoteModel.ejercicio.desc(),
                LoteModel.mes.desc(),
                LoteModel.folio.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return [LoteResponse.model_validate(lote) for lote in lotes]


@router.post("", response_model=LoteResponse, status_code=status.HTTP_201_CREATED)
async def crear_lote(
    datos: LoteCreate,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> LoteResponse:
    """Crea un lote con folio automatico.

    Sin `fecha_inicio`/`fecha_fin`, cubre el periodo completo del dia 1 del
    mes segun `tipo_periodo`. Si la empresa exige `total_esperado` en su
    `lote_config` y no viene, es 422: un lote sin expectativa no se puede
    declarar completo despues.
    """
    empresa = await _empresa_o_404(db, datos.company_id)
    config = config_lote_para(empresa)
    if config["exige_total_esperado"] and datos.total_esperado is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Esta empresa exige total_esperado para crear un lote",
        )

    if datos.fecha_inicio is None or datos.fecha_fin is None:
        _, _, fecha_inicio, fecha_fin = periodo_de_fecha(
            date(datos.ejercicio, datos.mes, 1), datos.tipo_periodo
        )
    else:
        fecha_inicio, fecha_fin = datos.fecha_inicio, datos.fecha_fin

    folio = await _siguiente_folio(
        db, datos.company_id, datos.ejercicio, datos.mes, config["prefijo_folio"]
    )
    lote = LoteModel(
        company_id=datos.company_id,
        folio=folio,
        nombre=datos.nombre,
        ejercicio=datos.ejercicio,
        mes=datos.mes,
        tipo_periodo=datos.tipo_periodo,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        estado=ESTADO_ABIERTO,
        total_esperado=datos.total_esperado,
        creado_por=usuario.email,
    )
    db.add(lote)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un lote con ese folio. Reintenta la creacion.",
        ) from exc
    await db.commit()
    await db.refresh(lote)
    return LoteResponse.model_validate(lote)


@router.get("/{lote_id}", response_model=LoteDetalleResponse)
async def detalle_lote(
    lote_id: UUID,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> LoteDetalleResponse:
    """Un lote con sus conteos en vivo (archivos, tickets, importe, duplicados)."""
    del usuario
    lote = await _lote_o_404(db, lote_id)
    conteos = await _conteos(db, lote)
    return LoteDetalleResponse.model_validate({**lote.__dict__, **conteos})


@router.post("/{lote_id}/cerrar", response_model=LoteResponse)
async def cerrar_lote(
    lote_id: UUID,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> LoteResponse:
    """Cierra un lote ABIERTO o EN_REVISION.

    Un lote CERRADO no acepta mas archivos: `scan_service` lo comprueba antes
    de asignar (ver el docstring del modulo). Cerrar un lote ya CERRADO es
    409, no un 200 que finja haber hecho algo.
    """
    lote = await _lote_o_404(db, lote_id)
    if lote.estado not in (ESTADO_ABIERTO, ESTADO_EN_REVISION):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No se puede cerrar un lote en estado {lote.estado}",
        )
    estado_anterior = lote.estado
    lote.estado = ESTADO_CERRADO
    lote.cerrado_at = utcnow()
    _auditar(
        db, usuario, "LOTE_CERRADO", lote, {"estado_anterior": estado_anterior}
    )
    await db.commit()
    await db.refresh(lote)
    return LoteResponse.model_validate(lote)


@router.post("/{lote_id}/reabrir", response_model=LoteResponse)
async def reabrir_lote(
    lote_id: UUID,
    datos: ReabrirRequest,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> LoteResponse:
    """Reabre un lote CERRADO. Exige motivo, y que la empresa lo permita.

    Si `lote_config.permite_reapertura` es falso, es 409: la empresa decidio
    que un cierre es definitivo, y un 422 diria que la peticion esta mal
    formada cuando lo que esta es prohibido por configuracion. El motivo
    queda en `lote.motivo` y en `auditoria`, que es donde se busca despues
    quien reabrio y por que.
    """
    lote = await _lote_o_404(db, lote_id)
    empresa = await _empresa_o_404(db, lote.company_id)
    if not config_lote_para(empresa)["permite_reapertura"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta empresa no permite reabrir lotes",
        )
    if lote.estado != ESTADO_CERRADO:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No se puede reabrir un lote en estado {lote.estado}",
        )
    lote.estado = ESTADO_ABIERTO
    lote.cerrado_at = None
    lote.motivo = datos.motivo
    _auditar(db, usuario, "LOTE_REABIERTO", lote, {"motivo": datos.motivo})
    await db.commit()
    await db.refresh(lote)
    return LoteResponse.model_validate(lote)


@router.post(
    "/{lote_id}/complementario",
    response_model=LoteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def crear_complementario(
    lote_id: UUID,
    usuario: UsuarioActual,
    datos: ComplementarioRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> LoteResponse:
    """Crea el lote complementario (o de alcance) de uno que ya existe.

    Hereda empresa, ejercicio, mes, fechas y tipo de periodo del padre, y se
    llama "Complemento de {folio}" salvo que se mande otro nombre. Nace
    ABIERTO con su propio folio secuencial del mes, aunque el padre este
    CERRADO: para eso existe, para lo que llego despues del cierre.
    """
    datos = datos or ComplementarioRequest()
    padre = await _lote_o_404(db, lote_id)
    empresa = await _empresa_o_404(db, padre.company_id)
    config = config_lote_para(empresa)
    folio = await _siguiente_folio(
        db, padre.company_id, padre.ejercicio, padre.mes, config["prefijo_folio"]
    )
    lote = LoteModel(
        company_id=padre.company_id,
        folio=folio,
        nombre=datos.nombre or f"Complemento de {padre.folio}",
        ejercicio=padre.ejercicio,
        mes=padre.mes,
        tipo_periodo=padre.tipo_periodo,
        fecha_inicio=padre.fecha_inicio,
        fecha_fin=padre.fecha_fin,
        estado=ESTADO_ABIERTO,
        lote_padre_id=padre.id,
        motivo=f"Complemento de {padre.folio}",
        creado_por=usuario.email,
    )
    db.add(lote)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un lote con ese folio. Reintenta la creacion.",
        ) from exc
    await db.commit()
    await db.refresh(lote)
    return LoteResponse.model_validate(lote)
