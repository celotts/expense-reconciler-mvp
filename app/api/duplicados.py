"""API de duplicados por huella fiscal.

Dos preguntas distintas, dos rutas:

- `POST /buscar`: "antes de contar este gasto, ¿ya existe este comprobante?"
- `GET /resumen`: "¿qué grupos repetidos hay y cuánto se está contando dos veces?"

La busqueda no mira solo el SHA del archivo: dos fotos del mismo papel tienen
bytes distintos. Mira los campos que el comprobante imprime, que son los que el
indice `ix_tickets_company_fiscal_huella` sostiene.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import EmpresaActiva, RolEnEmpresa, UsuarioActual
from app.schemas.caja_chica import (
    BuscarDuplicadoRequest,
    DuplicadoFiscalItem,
    DuplicadoFiscalResponse,
    ResumenDuplicados,
)
from app.services.duplicados_service import (
    buscar_duplicados_fiscales,
    calcular_hash_duplicado,
    datos_item_duplicado,
    resumen_duplicados_fiscales,
)

router = APIRouter(tags=["Duplicados"])


@router.post("/buscar", response_model=DuplicadoFiscalResponse)
async def buscar_duplicado(
    datos: BuscarDuplicadoRequest,
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    db: AsyncSession = Depends(get_db),
) -> DuplicadoFiscalResponse:
    # La empresa NO se cree del cuerpo: se comprueba contra la activa. Aceptar
    # `company_id` del cliente permitiria preguntar por los comprobantes de otra
    # empresa sin dejar rastro de que se hizo.
    if datos.company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La empresa de la busqueda no coincide con la empresa activa",
        )
    candidatos = await buscar_duplicados_fiscales(
        db,
        company_id=company_id,
        identificacion_fiscal=datos.provider_tax_id,
        fecha=datos.expense_date,
        folio=datos.folio,
        total=datos.total_amount,
        excluir_ticket_id=datos.excluir_ticket_id,
    )
    items = [
        DuplicadoFiscalItem.model_validate(datos_item_duplicado(ticket))
        for ticket in candidatos
    ]
    hash_duplicado = calcular_hash_duplicado(
        identificacion_fiscal=datos.provider_tax_id,
        fecha=datos.expense_date,
        folio=datos.folio,
        total=datos.total_amount,
    )
    if items:
        mensaje = (
            "Este comprobante parece el mismo que otro ya registrado. "
            "Revisa los datos del existente antes de volver a contarlo."
        )
    else:
        mensaje = "No hay un comprobante previo con la misma huella fiscal."
    return DuplicadoFiscalResponse(
        es_duplicado=bool(items),
        hash_duplicado=hash_duplicado,
        candidatos=items,
        mensaje=mensaje,
    )


@router.get("/resumen", response_model=ResumenDuplicados)
async def resumen_duplicados(
    usuario: UsuarioActual,
    company_id: EmpresaActiva,
    rol: RolEnEmpresa,
    company_id_filtro: UUID | None = Query(None, alias="company_id"),
    db: AsyncSession = Depends(get_db),
) -> ResumenDuplicados:
    if company_id_filtro is not None and company_id_filtro != company_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La empresa del filtro no coincide con la empresa activa",
        )
    return await resumen_duplicados_fiscales(db, company_id=company_id)
