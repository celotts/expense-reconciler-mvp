from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps import Escribir, UsuarioActual, resolver_empresa
from app.core.time import utcnow
from app.models.accounting_mapping import AccountingMappingModel
from app.models.bank_transaction import BankTransactionModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel
from app.schemas.accounting_mapping import (
    AccountingMappingCreate,
    AccountingMappingResponse,
    AccountingMappingUpdate,
)
from app.schemas.reconciliation import (
    ReconciliationCreate,
    ReconciliationResponse,
    ReconciliationRunRequest,
    ReconciliationRunResponse,
    ReconciliationUpdate,
)
from app.services.export_service import (
    export_generic,
    export_to_contpaqi,
    export_to_excel,
)
from app.services.reconciliation_service import run_reconciliation

router = APIRouter(tags=["Reconciliations"])


def _es_de_otra_empresa(conciliacion: ReconciliationModel, empresa: UUID) -> bool:
    """Si esta conciliacion pertenece a otra empresa que `empresa`.

    La empresa de una conciliacion se mira en TRES sitios, porque las filas
    viejas no la llevan puesta: `reconciliations.company_id` es nullable y
    el motor (`run_reconciliation`) todavia no la escribe —se rellena en la
    migracion desde el ticket—. Lo que nunca miente es el ticket y el
    movimiento enlazados: si alguno es de otra empresa, la fila es de otra
    empresa. Una fila sin empresa en ningun lado (sin ticket ni movimiento)
    no filtra nada de nadie y se deja pasar: no hay dato ajeno que proteger.
    """
    if (
        conciliacion.company_id is not None
        and conciliacion.company_id != empresa
    ):
        return True
    ticket = conciliacion.ticket
    if ticket is not None and ticket.company_id != empresa:
        return True
    banco = conciliacion.bank_transaction
    if banco is not None and banco.company_id != empresa:
        return True
    return False


@router.post("/run", response_model=ReconciliationRunResponse)
async def run_reconciliation_engine(
    request: ReconciliationRunRequest,
    usuario: UsuarioActual,
    _: Escribir,
    db: AsyncSession = Depends(get_db)
) -> ReconciliationRunResponse:
    """Run the automatic reconciliation engine.

    P0-1: el motor solo corre sobre la empresa resuelta. El `company_id`
    del cuerpo se resuelve contra el usuario; pedir otra empresa es 404.
    El motor ya filtra tickets y movimientos por esa empresa.
    """
    await resolver_empresa(usuario, request.company_id, db)
    return await run_reconciliation(db, request)


@router.get("/", response_model=list[ReconciliationResponse])
async def list_reconciliations(
    usuario: UsuarioActual,
    company_id: UUID | None = Query(None),
    match_status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[ReconciliationModel]:
    """List reconciliations with optional filters.

    P0-1: el listado es SIEMPRE de la empresa resuelta. El filtro mira la
    empresa de la fila Y la del ticket enlazado: el motor todavia no escribe
    `reconciliations.company_id` (se rellena en la migracion), asi que solo
    con la columna las corridas del motor desaparecerian de la lista.
    """
    empresa = await resolver_empresa(usuario, company_id, db)
    query = select(ReconciliationModel).options(
        selectinload(ReconciliationModel.ticket),
        selectinload(ReconciliationModel.bank_transaction)
    ).outerjoin(
        TicketModel, ReconciliationModel.ticket_id == TicketModel.id
    ).where(
        or_(
            ReconciliationModel.company_id == empresa,
            TicketModel.company_id == empresa,
        )
    )
    
    if match_status:
        query = query.where(ReconciliationModel.match_status == match_status)
    
    query = query.offset(skip).limit(limit).order_by(ReconciliationModel.matched_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())




@router.post("/", response_model=ReconciliationResponse, status_code=status.HTTP_201_CREATED)
async def create_reconciliation(
    reconciliation_in: ReconciliationCreate,
    usuario: UsuarioActual,
    _: Escribir,
    db: AsyncSession = Depends(get_db)
) -> ReconciliationModel:
    """Create a manual reconciliation link.

    P0-1: nunca se enlaza un ticket de A con un movimiento de B. Los dos
    lados tienen que ser de la empresa resuelta, y la fila nueva la lleva
    puesta en `company_id`.
    """
    ticket = None
    if reconciliation_in.ticket_id:
        ticket_result = await db.execute(
            select(TicketModel).where(TicketModel.id == reconciliation_in.ticket_id)
        )
        ticket = ticket_result.scalar_one_or_none()
        if ticket is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El ticket no existe"
            )
    
    bank_tx = None
    if reconciliation_in.bank_transaction_id:
        bank_result = await db.execute(
            select(BankTransactionModel).where(BankTransactionModel.id == reconciliation_in.bank_transaction_id)
        )
        bank_tx = bank_result.scalar_one_or_none()
        if not bank_tx:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El movimiento bancario no existe"
            )
        if bank_tx.is_reconciled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este movimiento bancario ya está conciliado"
            )

    # La empresa se resuelve contra el lado que exista: el ticket manda, y
    # si no hay ticket, el movimiento. Sin ninguno de los dos no hay nada
    # que cruzar y vale la empresa del usuario. Un lado de otra empresa es
    # 404, no 403, para no confirmar que existe.
    candidata = (
        ticket.company_id
        if ticket is not None
        else (bank_tx.company_id if bank_tx is not None else None)
    )
    empresa = await resolver_empresa(usuario, candidata, db)
    if ticket is not None and ticket.company_id != empresa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El ticket no existe",
        )
    if bank_tx is not None and bank_tx.company_id != empresa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El movimiento bancario no existe",
        )
    if bank_tx is not None:
        bank_tx.is_reconciled = True

    reconciliation = ReconciliationModel(
        **reconciliation_in.model_dump(), company_id=empresa
    )
    db.add(reconciliation)
    await db.commit()
    await db.refresh(reconciliation)
    
    # Reload with relationships for response
    result = await db.execute(
        select(ReconciliationModel)
        .options(
            selectinload(ReconciliationModel.ticket),
            selectinload(ReconciliationModel.bank_transaction)
        )
        .where(ReconciliationModel.id == reconciliation.id)
    )
    return result.scalar_one()


@router.delete("/{reconciliation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reconciliation(
    reconciliation_id: UUID,
    usuario: UsuarioActual,
    _: Escribir,
    company_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> None:
    """Delete a reconciliation and mark bank transaction as unreconciled."""
    result = await db.execute(
        select(ReconciliationModel)
        .options(
            selectinload(ReconciliationModel.ticket),
            selectinload(ReconciliationModel.bank_transaction),
        )
        .where(ReconciliationModel.id == reconciliation_id)
    )
    reconciliation = result.scalar_one_or_none()
    if not reconciliation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La conciliación no existe"
        )
    # P0-1: borrar un cruce de otra empresa es 404, no 403.
    empresa = await resolver_empresa(usuario, company_id, db)
    if _es_de_otra_empresa(reconciliation, empresa):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La conciliación no existe"
        )

    # Check if bank_transaction_id exists and retrieve the associated transaction
    # Using isinstance() to avoid "Invalid conditional operand" warning from Pylance/SQLAlchemy
    if reconciliation.bank_transaction_id is not None:
        if BankTransactionModel is None:
            raise HTTPException(
                status_code=500, detail="BankTransactionModel not imported"
            )
        bank_result = await db.execute(
            select(BankTransactionModel).where(BankTransactionModel.id == reconciliation.bank_transaction_id)
        )
        bank_tx = bank_result.scalar_one_or_none()
        if bank_tx:
            bank_tx.is_reconciled = False
    
    await db.delete(reconciliation)
    await db.commit()


@router.get("/export/excel", response_class=Response)
async def export_reconciliations_excel(
    usuario: UsuarioActual,
    company_id: UUID = Query(...),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    only_reconciled: bool = Query(True),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Export reconciliations to standard Excel format."""
    # P0-1: el export es de la empresa resuelta, y es requerida.
    empresa = await resolver_empresa(usuario, company_id, db)
    content = await export_to_excel(db, empresa, date_from, date_to, only_reconciled)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=conciliacion.xlsx"}
    )


@router.get("/export/contpaqi", response_class=Response)
async def export_reconciliations_contpaqi(
    usuario: UsuarioActual,
    company_id: UUID = Query(...),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    only_reconciled: bool = Query(True),
    mapping_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Export reconciliations to CONTPAQI format."""
    # P0-1: el export es de la empresa resuelta, y es requerida.
    empresa = await resolver_empresa(usuario, company_id, db)
    content = await export_to_contpaqi(db, empresa, date_from, date_to, only_reconciled, mapping_id)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=contpaqi_polizas.xlsx"}
    )


@router.get("/export/generic", response_class=Response)
async def export_reconciliations_generic(
    usuario: UsuarioActual,
    company_id: UUID = Query(...),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    only_reconciled: bool = Query(True),
    columns: str | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Export reconciliations with custom columns."""
    # P0-1: el export es de la empresa resuelta, y es requerida.
    empresa = await resolver_empresa(usuario, company_id, db)
    column_list = None
    if columns:
        column_list = [c.strip() for c in columns.split(",")]
    content = await export_generic(db, empresa, date_from, date_to, only_reconciled, columns=column_list)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=export.xlsx"}
    )


@router.post("/mappings", response_model=AccountingMappingResponse, status_code=status.HTTP_201_CREATED)
async def create_accounting_mapping(
    mapping_in: AccountingMappingCreate,
    usuario: UsuarioActual,
    _: Escribir,
    db: AsyncSession = Depends(get_db)
) -> AccountingMappingModel:
    """Create an accounting mapping template.

    P0-1: el mapeo se crea en la empresa resuelta, no en la del cuerpo sin mas.
    """
    empresa = await resolver_empresa(usuario, mapping_in.company_id, db)
    mapping = AccountingMappingModel(
        **{**mapping_in.model_dump(), "company_id": empresa}
    )
    db.add(mapping)
    await db.commit()
    await db.refresh(mapping)
    return mapping


@router.get("/mappings", response_model=list[AccountingMappingResponse])
async def list_accounting_mappings(
    usuario: UsuarioActual,
    company_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> list[AccountingMappingModel]:
    """List accounting mappings.

    P0-1: el listado es SIEMPRE de la empresa resuelta.
    """
    empresa = await resolver_empresa(usuario, company_id, db)
    query = select(AccountingMappingModel).where(
        AccountingMappingModel.company_id == empresa
    )
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/mappings/{mapping_id}", response_model=AccountingMappingResponse)
async def get_accounting_mapping(
    mapping_id: UUID,
    usuario: UsuarioActual,
    company_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> AccountingMappingModel:
    """Get an accounting mapping by ID."""
    result = await db.execute(select(AccountingMappingModel).where(AccountingMappingModel.id == mapping_id))
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe el mapeo contable"
        )
    # P0-1: un mapeo de otra empresa no existe para este usuario (404, no 403).
    empresa = await resolver_empresa(usuario, company_id, db)
    if mapping.company_id != empresa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe el mapeo contable"
        )
    return mapping


@router.patch("/mappings/{mapping_id}", response_model=AccountingMappingResponse)
async def update_accounting_mapping(
    mapping_id: UUID,
    mapping_in: AccountingMappingUpdate,
    usuario: UsuarioActual,
    _: Escribir,
    company_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> AccountingMappingModel:
    """Corrige un mapeo: el nombre del software o el diccionario de columnas.

    `exclude_unset=True` y no `exclude_none`: sin eso, un `PATCH` que solo manda
    `software_name` borraria `column_mappings`, que es el cuerpo del mapeo. El
    200 llegaria igual y el export CONTPAQI dejaria de encontrar las columnas.
    """
    result = await db.execute(select(AccountingMappingModel).where(AccountingMappingModel.id == mapping_id))
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe el mapeo contable"
        )
    # P0-1: corregir un mapeo de otra empresa es 404, no 403.
    empresa = await resolver_empresa(usuario, company_id, db)
    if mapping.company_id != empresa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe el mapeo contable"
        )

    cambios = mapping_in.model_dump(exclude_unset=True)
    if not cambios:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No mandaste ningún campo que se pueda cambiar",
        )

    for campo, valor in cambios.items():
        setattr(mapping, campo, valor)
    await db.commit()
    await db.refresh(mapping)
    return mapping


@router.delete("/mappings/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_accounting_mapping(
    mapping_id: UUID,
    usuario: UsuarioActual,
    _: Escribir,
    company_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> None:
    """Borra un mapeo.

    Que el borrado sea definitivo y no una baja logica es una decision distinta de
    la de los productos, y merece decirse: un mapeo no tiene historial. No es una
    fila de la que se deriven saldos ni una firma de un cierre; es un diccionario
    de tradaccion de columnas que el export lee. Guardarlo como `activo=false`
    anadiria una columna y una condicion al unico consumidor, a cambio de poder
    deshacer un `DELETE` equivocado.

    Y lo que NO se borra con el es lo que el mapeo hizo: los tickets, los
    movimientos y las conciliaciones no lo referencian. Por eso esto no toca el
    historico contable y por eso es seguro.
    """
    result = await db.execute(select(AccountingMappingModel).where(AccountingMappingModel.id == mapping_id))
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe el mapeo contable"
        )
    # P0-1: borrar un mapeo de otra empresa es 404, no 403.
    empresa = await resolver_empresa(usuario, company_id, db)
    if mapping.company_id != empresa:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe el mapeo contable"
        )
    await db.delete(mapping)
    await db.commit()


@router.patch(
    "/{reconciliation_id}",
    response_model=ReconciliationResponse,
    summary="Corrige a mano el veredicto que puso el motor",
)
async def update_reconciliation(
    reconciliation_id: UUID,
    reconciliacion_in: ReconciliationUpdate,
    usuario: UsuarioActual,
    _: Escribir,
    company_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ReconciliationModel:
    """Una persona corrige el `match_status` que produjo el motor.

    QUE POR QUE HAY UNA COLUMNA `revisado_por` Y NO SOLO EL CAMBIO
    -----------------------------------------------------------

    Porque `match_status` decide que se exporta: `export_service` filtra por
    `MATCHED_STATUSES`, y `DISCREPANCY` esta fuera a proposito. Sin `revisado_por`,
    un `PERFECT` que decidio el motor y uno que aprobo una persona serían la misma
    fila, y no habria forma de saber cual se esta mandando a CONTPAQI.

    Es el mismo motivo por el que existe `confidence_source` en `tickets`, y por el
    que `compras.confirmada_por` es obligatorio para PROCESADO: "lo automatico lo
    hizo" y "lo hizo una persona" no se suman, y mezclarlos produce un numero que
    no describe a ninguno de los dos.

    POR QUE NO SE CAMBIA EL TICKET NI EL MOVIMIENTO AQUI
    ---------------------------------------------------

    Porque un emparejamiento equivocado se corrige de otra manera —`DELETE` y
    `POST`—, y mezclar las dos cosas en un endpoint deja la fila sin poder decir
    que se cambio. Ver `ReconciliationUpdate`.

    Y ADEMAS el estado del movimiento bancario. Este endpoint NO toca
    `bank_transactions.is_reconciled`: un cruce a mano sigue siendo un cruce
    (hay reconciliacion, y el movimiento esta emparejado), lo que cambio es si
    cuenta como cuadrado. Marcar el movimiento como no conciliado desde aqui
    dejaria la fila de `reconciliations` apuntando a un movimiento que dice que no
    esta conciliado, que es la contradiccion que `delete_reconciliation` si
    resuelve.
    """
    result = await db.execute(
        select(ReconciliationModel)
        .options(
            selectinload(ReconciliationModel.ticket),
            selectinload(ReconciliationModel.bank_transaction),
        )
        .where(ReconciliationModel.id == reconciliation_id)
    )
    conciliacion = result.scalar_one_or_none()
    if not conciliacion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="La conciliación no existe"
        )
    # P0-1: corregir el veredicto de un cruce de otra empresa es 404.
    empresa = await resolver_empresa(usuario, company_id, db)
    if _es_de_otra_empresa(conciliacion, empresa):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="La conciliación no existe"
        )

    # Idempotente a proposito: si el estado ya era ese, no se lanza un 409. Un
    # cliente que reintenta tras un timeout no debe recibir un error por algo que
    # ya esta aplicado. Y `revisado_at` se escribe igual en los dos casos, porque
    # la columna dice "cuando lo reviso por ultima vez una persona", no "cuando
    # cambio el veredicto": eso ultimo ya lo dice `match_status` junto con
    # `matched_at`.
    conciliacion.match_status = reconciliacion_in.match_status
    conciliacion.revisado_por = usuario.email
    conciliacion.revisado_at = utcnow()

    await db.commit()

    # Se RE-CONSULTA con `selectinload` y no se devuelve el objeto del `select` de
    # arriba. El motivo es concreto: tras el commit, el objeto tiene las
    # relaciones expiradas, y `ReconciliationResponse` pide `ticket` y
    # `bank_transaction`. Serializarlo sin recargarlas lanza MissingGreenlet —el
    # mismo motivo por el que `create_reconciliation` hace su segunda consulta— y
    # el sintoma es un 500 en un endpoint que en realidad funciono.
    resultado = await db.execute(
        select(ReconciliationModel)
        .options(
            selectinload(ReconciliationModel.ticket),
            selectinload(ReconciliationModel.bank_transaction),
        )
        .where(ReconciliationModel.id == conciliacion.id)
    )
    return resultado.scalar_one()


# Esta ruta va AL FINAL a proposito, despues de /mappings y /export/*.
#
# Starlette resuelve las rutas en orden de declaracion y gana el primer match.
# Un("/{reconciliation_id}") declarado antes se traga los paths literales de un
# solo segmento: "GET /mappings" llegaba aqui, el UUID "mappings" no parseaba, y
# la respuesta era 422 en vez de la lista de mapeos.
#
# Nota: /export/excel, /export/contpaqi, /export/generic y /mappings/{id} NO se
# veian afectados. Son dos o mas segmentos de path, y un placeholder de un solo
# segmento no compite con ellos. Solo "GET /mappings" (un segmento) colisionaba.
#
# Antes el test no lo Notaba porque afirmaba `len(respuesta.json()) == 1`, y el
# {"detail": [...]} de un 422 tambien cumple len == 1. Un test de API exige el
# codigo de estado explicito; afirmar sobre la forma de la respuesta puede
# darle el visto bueno a un error.
#
# Al anadir una ruta literal nueva, declarala ANTES de esta.
@router.get("/{reconciliation_id}", response_model=ReconciliationResponse)
async def get_reconciliation(
    reconciliation_id: UUID,
    usuario: UsuarioActual,
    company_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db)
) -> ReconciliationModel:
    """Get a reconciliation by ID."""
    result = await db.execute(
        select(ReconciliationModel)
        .options(
            selectinload(ReconciliationModel.ticket),
            selectinload(ReconciliationModel.bank_transaction)
        )
        .where(ReconciliationModel.id == reconciliation_id)
    )
    reconciliation = result.scalar_one_or_none()
    if not reconciliation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La conciliación no existe"
        )
    # P0-1: un cruce de otra empresa no existe para este usuario (404, no 403).
    empresa = await resolver_empresa(usuario, company_id, db)
    if _es_de_otra_empresa(reconciliation, empresa):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La conciliación no existe"
        )
    return reconciliation
