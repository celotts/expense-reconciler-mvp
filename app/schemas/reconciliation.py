from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import MatchStatus

# El patron se arma desde el enum y no se escribe a mano. Estaban las dos
# copias: el enum y la cadena, y la unica forma de que dejaran de coincidir era
# que alguien agregara un estado al motor y no al patron. Entonces la API
# aceptaba un estado que el motor nunca produce. Hay un test que compara este
# patron contra el enum para que no vuelva a pasar.
PATRON_MATCH_STATUS = "^(" + "|".join(s.value for s in MatchStatus) + ")$"


class ReconciliationBase(BaseModel):
    ticket_id: UUID | None = None
    bank_transaction_id: UUID | None = None
    match_status: str = Field(..., pattern=PATRON_MATCH_STATUS)


class ReconciliationCreate(ReconciliationBase):
    pass


class ReconciliationUpdate(BaseModel):
    """La correccion a mano del veredicto que puso el motor.

    SOLO CAMBIA `match_status`. NO el ticket ni el movimiento bancario, y la
    razon es que un emparejamiento equivocado se corrige de dos maneras distintas
    que no se pueden confundir:

      - **El veredicto estaba mal** (el motor eligio bien los dos lados pero los
        considero discrepantes, o al reves). Se corrige aqui, cambiando el estado.
      - **El emparejamiento estaba mal** (este ticket no es este movimiento). Se
        deshace con `DELETE` y se rehace con `POST`, porque las dos cosas son
        afirmaciones distintas y una sola columna no puede decir cual se cambio.

    Si `PATCH` aceptara los tres campos, "cambié el estado" y "moví el cruce" se
    grabarian en la misma fila y no habria forma de saber que paso. Y el borrado
    tiene un efecto que el update no: `bank_transactions.is_reconciled` vuelve a
    `false`, asi que un cambio de estado a mano que no lo contemplate dejaria el
    movimiento marcado como conciliado con otra fila.

    `revisado_por` NO lo pide el cliente: sale del token y lo pone el router.
    Aceptarlo en el body seria una puerta para firmar la revision de otra empresa,
    y `AGENTS.md` ya advierte que no hay multi-tenancy. Ver
    `app/models/reconciliation.py`.

    `extra="forbid"`: un cliente que reenvie la fila entera recibe un 422 con el
    nombre del campo en vez de un 200 que finge haber guardado algo que no se
    guarda.
    """

    model_config = ConfigDict(extra="forbid")

    match_status: str = Field(..., pattern=PATRON_MATCH_STATUS)


class ReconciliationResponse(ReconciliationBase):
    id: UUID
    matched_at: datetime
    # Quien toco el veredicto a mano, si alguien lo toco. `None` = lo puso el
    # motor. Va en la respuesta y no solo en la base a proposito: es lo que
    # distingue una conciliacion que el sistema decidio de una que una persona
    # aprobo, y sin eso el export seria indistinguible. Ver
    # `app/models/reconciliation.py`.
    revisado_por: Optional[str] = None
    revisado_at: Optional[datetime] = None
    ticket: Optional["TicketResponse"] = None
    bank_transaction: Optional["BankTransactionResponse"] = None

    model_config = ConfigDict(from_attributes=True)


# Forward references
from app.schemas.bank_transaction import BankTransactionResponse
from app.schemas.ticket import TicketResponse

ReconciliationResponse.model_rebuild()


class ReconciliationRunRequest(BaseModel):
    company_id: UUID
    date_from: date | None = None
    date_to: date | None = None
    amount_tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)
    date_tolerance_days: int = Field(default=3, ge=0)


class ReconciliationMatchDetail(BaseModel):
    ticket_id: UUID
    ticket_provider: str
    ticket_amount: Decimal
    ticket_date: date
    bank_transaction_id: UUID
    bank_description: str
    bank_amount: Decimal
    bank_date: date
    match_status: str = Field(..., pattern=PATRON_MATCH_STATUS)
    amount_diff: Decimal
    date_diff_days: int
    # Por que se eligio este movimiento y no otro. Sin esto, la fila dice que
    # se concilio pero no deja reconstruir la decision, y "el motor lo hizo" no
    # es una razon que se pueda auditar. Es texto para personas, no un numero:
    # la aritmetica ya esta en amount_diff y date_diff_days.
    criterio: str


class ReconciliationRunResponse(BaseModel):
    total_tickets: int
    total_bank_transactions: int
    perfect_matches: int
    manual_review: int
    discrepancies: int
    unmatched_tickets: int
    unmatched_bank_transactions: int
    matches: list[ReconciliationMatchDetail]