from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import UsuarioActual
from app.models.company import CompanyModel
from app.schemas.company import CompanyCreate, CompanyResponse, CompanyUpdate
from app.services import tipos_gasto_service
from app.services.auditoria import (
    registrar_auditoria,
    registrar_delete_auditoria,
    registrar_update_auditoria,
)

router = APIRouter(tags=["Companies"])


@router.post("/", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
async def create_company(
    company_in: CompanyCreate,
    request: Request,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db)
) -> CompanyModel:
    """Create a new company."""
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
    return company


@router.get("/", response_model=list[CompanyResponse])
async def list_companies(
    skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)
) -> list[CompanyModel]:
    """List all companies with pagination."""
    result = await db.execute(select(CompanyModel).offset(skip).limit(limit))
    return list(result.scalars().all())


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> CompanyModel:
    """Get a company by ID."""
    result = await db.execute(select(CompanyModel).where(CompanyModel.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La empresa no existe"
        )
    return company


@router.patch("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: UUID,
    company_in: CompanyUpdate,
    request: Request,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db)
) -> CompanyModel:
    """Update a company."""
    result = await db.execute(select(CompanyModel).where(CompanyModel.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La empresa no existe"
        )
    
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
    db: AsyncSession = Depends(get_db)
) -> None:
    """Delete a company."""
    result = await db.execute(select(CompanyModel).where(CompanyModel.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La empresa no existe"
        )
    
    # Si la empresa tiene hijos (tickets, productos, movimientos), el DELETE
    # revienta con un IntegrityError. En vez de dejar que salga como 500,
    # se traduce a 409 con un mensaje que dice qué hacer.
    #
    # MEDIDO: sin este manejo, `DELETE /companies/{id}` devuelve 500 para
    # toda empresa con datos, y el usuario no tiene forma de saber por qué.
    try:
        await db.delete(company)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La empresa tiene datos asociados (comprobantes, productos, "
                   "movimientos). Elimínelos primero o dé de baja la empresa.",
        )
    # Solo si el borrado se confirmo: un 409 no borro nada y no hay nada que
    # auditar como borrado.
    await registrar_delete_auditoria(
        modelo="companies",
        registro_id=str(company_id),
        usuario_id=str(usuario.id),
        nombre=company.name,
        detalles={"nombre": company.name, "tax_id": company.tax_id},
        rol=usuario.perfil,
        ip=request.client.host if request.client else "desconocida",
        db=db,
    )