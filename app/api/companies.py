from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.company import CompanyModel
from app.schemas.company import CompanyCreate, CompanyUpdate, CompanyResponse

router = APIRouter(tags=["Companies"])


@router.post("/", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
async def create_company(
    company_in: CompanyCreate,
    db: AsyncSession = Depends(get_db)
) -> CompanyModel:
    """Create a new company."""
    existing = await db.execute(
        select(CompanyModel).where(CompanyModel.tax_id == company_in.tax_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Company with this tax_id already exists"
        )
    
    company = CompanyModel(**company_in.model_dump())
    db.add(company)
    await db.commit()
    await db.refresh(company)
    return company


@router.get("/", response_model=List[CompanyResponse])
async def list_companies(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
) -> List[CompanyModel]:
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
            detail="Company not found"
        )
    return company


@router.patch("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: UUID,
    company_in: CompanyUpdate,
    db: AsyncSession = Depends(get_db)
) -> CompanyModel:
    """Update a company."""
    result = await db.execute(select(CompanyModel).where(CompanyModel.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found"
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
                detail="Company with this tax_id already exists"
            )
    
    for field, value in update_data.items():
        setattr(company, field, value)
    
    await db.commit()
    await db.refresh(company)
    return company


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_company(
    company_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> None:
    """Delete a company."""
    result = await db.execute(select(CompanyModel).where(CompanyModel.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found"
        )
    
    await db.delete(company)
    await db.commit()