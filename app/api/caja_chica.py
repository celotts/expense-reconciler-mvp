"""API de caja chica.

Los routers son adaptadores finos: aqui no se decide si un gasto cabe en el
saldo ni si una caja puede cerrarse. Eso vive en `caja_chica_service.py`, y el
motivo es concreto: el mismo servicio lo va a llamar el escaneo cuando un ticket
se marque como pagado con caja. Si la regla estuviera aqui, habria dos copias.

Lo que SI decide aqui es la empresa y el rol, y lo hace por dependencia:
`EmpresaActiva` se resuelve en BD con la cabecera `X-Company-ID` y `RolEnEmpresa`
lee el rol EN ESA empresa. Ningun endpoint recibe la empresa del cuerpo sin
contrastarla. Un servicio que se llamara desde otro sitio (el escaneo) recibe la
empresa como argumento obligatorio, asi que no hay camino sin ella.

El prefijo se declara en `api_router.py`, no aqui: el mismo error que ya se
pago con `/categorias` y `/dashboard` (prefijo duplicado = 404 silencioso).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import EmpresaActiva, RolEnEmpresa, UsuarioActual
from app.core.enums import EstadoCaja
from app.schemas.caja_chica import (
    CajaChicaConMovimientos,
    CajaChicaCreate,
    CajaChicaResponse,
    CajaChicaUpdate,
    CierreCajaRequest,
    ConciliacionCajaResponse,
    MovimientoCajaCreate,
    MovimientoCajaResponse,
)
from app.services import caja_chica_service
from app.services.caja_chica_service import ErrorCaja

router = APIRouter(tags=["Caja chica"])


def _fallar(error: ErrorCaja) -> None:
    """Traduce un conflicto de negocio sin duplicar reglas en el router."""
    raise HTTPException(status_code=error.status_code, detail=error.detail)


def _empresa_del_filtro(filtro: UUID | None, activa: UUID) -> None:
    """El filtro de empresa sigue en la URL por compatibilidad, pero no manda."""
    if filtro is not None and filtro != activa:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La empresa del filtro no coincide con la empresa activa",
        )


@router.post("/", response_model=CajaChicaResponse, status_code=status.HTTP_201_CREATED)
async def crear_caja(
    datos: CajaChicaCreate,
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    db: AsyncSession = Depends(get_db),
) -> CajaChicaResponse:
    try:
        return await caja_chica_service.crear_caja(
            db, datos, usuario.id, company_id=company_id, rol=rol
        )
    except ErrorCaja as error:
        _fallar(error)


@router.get("/", response_model=list[CajaChicaResponse])
async def listar_cajas(
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    company_id_filtro: UUID | None = Query(None, alias="company_id"),
    db: AsyncSession = Depends(get_db),
) -> list[CajaChicaResponse]:
    _empresa_del_filtro(company_id_filtro, company_id)
    return await caja_chica_service.listar_cajas(
        db, company_id, usuario_id=usuario.id, rol=rol
    )


@router.get(
    "/{caja_id}/conciliacion-bancaria", response_model=ConciliacionCajaResponse
)
async def conciliacion_bancaria(
    caja_id: UUID,
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    db: AsyncSession = Depends(get_db),
) -> ConciliacionCajaResponse:
    """Reposiciones de la caja cruzadas contra movimientos del banco.

    El prefijo lleva dos segmentos, asi que no choca con `/{caja_id}`; aun asi
    se declara antes para que nadie tenga que acordarse de la trampa de FastAPI.
    """
    try:
        return await caja_chica_service.conciliar_reposiciones_bancarias(
            db, caja_id, company_id=company_id, usuario_id=usuario.id, rol=rol
        )
    except ErrorCaja as error:
        _fallar(error)


@router.get("/{caja_id}", response_model=CajaChicaConMovimientos)
async def obtener_caja(
    caja_id: UUID,
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    db: AsyncSession = Depends(get_db),
) -> CajaChicaConMovimientos:
    try:
        caja = await caja_chica_service.obtener_caja(
            db, caja_id, company_id=company_id, usuario_id=usuario.id, rol=rol
        )
        movimientos = await caja_chica_service.listar_movimientos(
            db, caja_id, company_id=company_id, usuario_id=usuario.id, rol=rol
        )
        return CajaChicaConMovimientos.model_validate(
            {**caja.__dict__, "movimientos": movimientos}
        )
    except ErrorCaja as error:
        _fallar(error)


@router.patch("/{caja_id}", response_model=CajaChicaResponse)
async def actualizar_caja(
    caja_id: UUID,
    datos: CajaChicaUpdate,
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    db: AsyncSession = Depends(get_db),
) -> CajaChicaResponse:
    try:
        return await caja_chica_service.actualizar_caja(
            db,
            caja_id,
            datos,
            company_id=company_id,
            usuario_id=usuario.id,
            rol=rol,
        )
    except ErrorCaja as error:
        _fallar(error)


@router.get("/{caja_id}/movimientos", response_model=list[MovimientoCajaResponse])
async def listar_movimientos(
    caja_id: UUID,
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    db: AsyncSession = Depends(get_db),
) -> list[MovimientoCajaResponse]:
    try:
        return await caja_chica_service.listar_movimientos(
            db, caja_id, company_id=company_id, usuario_id=usuario.id, rol=rol
        )
    except ErrorCaja as error:
        _fallar(error)


@router.post(
    "/{caja_id}/movimientos",
    response_model=MovimientoCajaResponse,
    status_code=status.HTTP_201_CREATED,
)
async def registrar_movimiento(
    caja_id: UUID,
    datos: MovimientoCajaCreate,
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    db: AsyncSession = Depends(get_db),
) -> MovimientoCajaResponse:
    try:
        return await caja_chica_service.registrar_movimiento(
            db,
            caja_id,
            datos,
            usuario.id,
            company_id=company_id,
            rol=rol,
        )
    except ErrorCaja as error:
        _fallar(error)


@router.post("/{caja_id}/en-revision", response_model=CajaChicaResponse)
async def enviar_a_revision(
    caja_id: UUID,
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    db: AsyncSession = Depends(get_db),
) -> CajaChicaResponse:
    try:
        return await caja_chica_service.cambiar_estado(
            db,
            caja_id,
            EstadoCaja.EN_REVISION,
            company_id=company_id,
            usuario_id=usuario.id,
            rol=rol,
        )
    except ErrorCaja as error:
        _fallar(error)


@router.post("/{caja_id}/reabrir", response_model=CajaChicaResponse)
async def reabrir_caja(
    caja_id: UUID,
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    db: AsyncSession = Depends(get_db),
) -> CajaChicaResponse:
    try:
        return await caja_chica_service.cambiar_estado(
            db,
            caja_id,
            EstadoCaja.ABIERTA,
            company_id=company_id,
            usuario_id=usuario.id,
            rol=rol,
        )
    except ErrorCaja as error:
        _fallar(error)


@router.post("/{caja_id}/cerrar", response_model=CajaChicaResponse)
async def cerrar_caja(
    caja_id: UUID,
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    datos: CierreCajaRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> CajaChicaResponse:
    try:
        return await caja_chica_service.cambiar_estado(
            db,
            caja_id,
            EstadoCaja.CERRADA,
            closed_at=datos.closed_at if datos else None,
            efectivo_contado=datos.efectivo_contado if datos else None,
            company_id=company_id,
            usuario_id=usuario.id,
            rol=rol,
        )
    except ErrorCaja as error:
        _fallar(error)
