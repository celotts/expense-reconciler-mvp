from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import UsuarioActual
from app.core.enums import EstadoEmpresa
from app.models.company import CompanyModel
from app.schemas.company import (
    CompanyCreate,
    CompanyResponse,
    CompanyUpdate,
    DesactivarEmpresaRequest,
)
from app.services import empresas_service, tipos_gasto_service
from app.services.auditoria import registrar_auditoria, registrar_update_auditoria

router = APIRouter(tags=["Companies"])


async def _company_o_404(db: AsyncSession, company_id: UUID) -> CompanyModel:
    result = await db.execute(select(CompanyModel).where(CompanyModel.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La empresa no existe",
        )
    return company


def _conflicto(mensaje: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=mensaje)


async def _marcar_puede_eliminarse(
    db: AsyncSession,
    companies: list[CompanyModel],
) -> list[CompanyModel]:
    """El boton de la pantalla lee la misma decision que el cerrojo del servicio.

    Una sola consulta para las diez tablas de trabajo y todas las empresas de la
    pagina; calcularlo por separado en cada fila seria N+1 y, peor, dos fuentes
    de verdad si el boton y el servicio preguntaran distinto.
    """
    if not companies:
        return companies
    ids = {company.id for company in companies}
    con_datos = await empresas_service.empresas_con_datos(db, ids)
    activas = await empresas_service.cantidad_de_empresas_activas(db)
    hay_datos = await empresas_service.hay_datos_en_el_sistema(db)
    for company in companies:
        company.puede_eliminarse = await empresas_service.puede_eliminarse(
            db,
            company,
            ids_con_datos=con_datos,
            hay_datos=hay_datos,
            activas=activas,
        )
    return companies


async def _cambiar_estado(
    db: AsyncSession,
    company: CompanyModel,
    request: Request,
    usuario: UsuarioActual,
    nuevo: EstadoEmpresa,
    verificar,
    motivo: str | None = None,
) -> CompanyModel:
    """Un solo sitio para las tres transiciones, con firma y auditoria.

    La verificacion corre ANTES de escribir: si se contaran las activas despues
    del flush, desactivar la ultima daria el numero equivocado y el cerrojo no
    defenderia nada.
    """
    anterior = company.estado
    try:
        await verificar(db, company)
        cambio = empresas_service.cambiar_estado(
            db, company, nuevo,
            motivo=(motivo or "").strip() or None,
            usuario=usuario.email,
        )
    except (empresas_service.EmpresaConDatosError,
            empresas_service.UltimaEmpresaActivaError,
            empresas_service.EmpresaEliminadaError) as error:
        # `verificar` corre ANTES de escribir: no hay nada que deshacer. Un
        # rollback aqui expiraria al usuario de la request compartida en los
        # tests y, en produccion, tiraria trabajo ajeno de la misma sesion.
        raise _conflicto(str(error))
    if cambio:
        # `anterior` se capturo ANTES de escribir: leerlo despues daria el
        # mismo valor en los dos lados y la auditoria mentiria sobre el cambio.
        campos = ["estado"]
        valores_nuevos = {"estado": company.estado}
        if nuevo is EstadoEmpresa.INACTIVO and company.motivo_baja:
            campos.append("motivo_baja")
            valores_nuevos["motivo_baja"] = company.motivo_baja
        await registrar_update_auditoria(
            modelo="companies",
            registro_id=str(company.id),
            campos_modificados=campos,
            usuario_id=str(usuario.id),
            valores_anteriores={"estado": anterior},
            valores_nuevos=valores_nuevos,
            nombre=company.name,
            rol=usuario.perfil,
            ip=request.client.host if request.client else "desconocida",
            db=db,
        )
    await db.commit()
    await db.refresh(company)
    return (await _marcar_puede_eliminarse(db, [company]))[0]


@router.post("/", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
async def create_company(
    company_in: CompanyCreate,
    request: Request,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db)
) -> CompanyModel:
    """Create a new company, always in `activo`."""
    existing = await db.execute(
        select(CompanyModel).where(CompanyModel.tax_id == company_in.tax_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay una empresa con ese RFC"
        )

    company = CompanyModel(**company_in.model_dump())
    db.add(company)
    await db.commit()
    await db.refresh(company)
    # La empresa nace con su catalogo de cuentas. Si no, el primer dia el
    # desplegable de Tickets esta vacio y el usuario no tiene forma de saber
    # que le falta sembrar: la ausencia parece un bug del front. Es idempotente,
    # asi que un reintento tras un fallo de red no duplica nada.
    await tipos_gasto_service.asegurar_catalogo(db, company.id)
    await registrar_auditoria(
        accion="crear_empresa",
        modulo="companies",
        usuario_id=str(usuario.id),
        registro_afectado=str(company.id),
        detalles={"nombre": company.name, "tax_id": company.tax_id},
        rol=usuario.perfil,
        ip=request.client.host if request.client else "desconocida",
        resultado="exito",
        db=db,
    )
    return (await _marcar_puede_eliminarse(db, [company]))[0]


@router.get("/", response_model=list[CompanyResponse])
async def list_companies(
    skip: int = 0,
    limit: int = 100,
    incluir_inactivas: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[CompanyModel]:
    """Por omision SOLO activas.

    `incluir_inactivas` existe para poder reactivar una empresa desactivada:
    sin esa via, una baja reversible seria irreversible por la interfaz. La
    plantilla no lo pide, asi que ve solo las activas.
    """
    consulta = select(CompanyModel)
    if not incluir_inactivas:
        consulta = consulta.where(CompanyModel.estado == EstadoEmpresa.ACTIVO.value)
    result = await db.execute(consulta.offset(skip).limit(limit))
    return await _marcar_puede_eliminarse(db, list(result.scalars().all()))


@router.post("/{company_id}/desactivar", response_model=CompanyResponse)
async def desactivar_company(
    company_id: UUID,
    request: Request,
    usuario: UsuarioActual,
    datos: DesactivarEmpresaRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> CompanyModel:
    """Anular: `estado='inactivo'`, sin borrar ni una fila.

    La nota es opcional por contrato con Clients antiguos, pero se guarda con
    quien la escribe y cuando, para que la pantalla pueda decir por que no se
    procesa. Reactivar la limpia de la fila; la auditoria conserva el motivo.
    """
    company = await _company_o_404(db, company_id)
    return await _cambiar_estado(
        db, company, request, usuario,
        EstadoEmpresa.INACTIVO, empresas_service.verificar_desactivar,
        motivo=datos.motivo if datos else None,
    )


@router.post("/{company_id}/eliminar", response_model=CompanyResponse)
async def eliminar_company(
    company_id: UUID,
    request: Request,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> CompanyModel:
    """Soft delete: `estado='eliminado'`. Solo para empresas sin datos."""
    company = await _company_o_404(db, company_id)
    return await _cambiar_estado(
        db, company, request, usuario,
        EstadoEmpresa.ELIMINADO, empresas_service.verificar_eliminar,
    )


@router.post("/{company_id}/activar", response_model=CompanyResponse)
async def activar_company(
    company_id: UUID,
    request: Request,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> CompanyModel:
    """Recuperar una empresa desactivada. Una eliminada NO se reactiva."""
    company = await _company_o_404(db, company_id)
    return await _cambiar_estado(
        db, company, request, usuario,
        EstadoEmpresa.ACTIVO, empresas_service.verificar_activar,
    )


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> CompanyModel:
    """Get a company by ID."""
    return await _company_o_404(db, company_id)


@router.patch("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: UUID,
    company_in: CompanyUpdate,
    request: Request,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db)
) -> CompanyModel:
    """Update a company. `estado` NO es editable aqui: tiene sus tres rutas."""
    company = await _company_o_404(db, company_id)

    update_data = company_in.model_dump(exclude_unset=True)

    if "tax_id" in update_data:
        existing = await db.execute(
            select(CompanyModel).where(
                CompanyModel.tax_id == update_data["tax_id"],
                CompanyModel.id != company_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya hay una empresa con ese RFC"
            )

    valores_antes = {k: getattr(company, k, None) for k in update_data}
    for field, value in update_data.items():
        setattr(company, field, value)

    await db.commit()
    await db.refresh(company)
    # Solo los campos que venian en el cuerpo (`exclude_unset`): un PATCH sin
    # cambios no tiene nada que auditar como modificado.
    await registrar_update_auditoria(
        modelo="companies",
        registro_id=str(company.id),
        campos_modificados=sorted(update_data.keys()),
        usuario_id=str(usuario.id),
        valores_anteriores=valores_antes,
        valores_nuevos=update_data,
        nombre=company.name,
        rol=usuario.perfil,
        ip=request.client.host if request.client else "desconocida",
        db=db,
    )
    return company


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: UUID,
    request: Request,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> None:
    """DELETE es un alias del soft delete: ninguna ruta borra evidencia.

    Antes borraba la fila en cascada y las FK se llevaban tickets, banco,
    caja e inventario. Los cerrojos viven en el servicio, no en la base.
    """
    company = await _company_o_404(db, company_id)
    await _cambiar_estado(
        db, company, request, usuario,
        EstadoEmpresa.ELIMINADO, empresas_service.verificar_eliminar,
    )
