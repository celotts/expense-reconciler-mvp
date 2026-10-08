import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MatchStatus
from app.models.bank_transaction import BankTransactionModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel
from app.schemas.reconciliation import (
    ReconciliationMatchDetail,
    ReconciliationRunRequest,
    ReconciliationRunResponse,
)

# Todo lo que no es alfanumerico se vuelve espacio, para comparar "SUPERCENTER
# SA de CV" con "pago supercenter s.a. de c.v." sin que los signos lo arruinen
# la comparacion.
_NO_ALFANUMERICO = re.compile(r"[^A-Z0-9]+")

# Un token de menos de 3 letras no es evidencia de nada ("DE", "SA"), asi que no
# cuenta. Costo: una marca corta como "CFE" no aporta senal. Beneficio: no
# convierte cualquier descripcion en un "si coincide" porque tenga un filler.
_TOKEN_MINIMO = 3


def _normaliza(texto: str) -> str:
    """Deja el texto comparable: sin acentos, sin signos, en mayusculas.

    El banco y el comprobante nunca escriben igual. Sin esto, comparar el
    proveedor con la descripcion es comparacion de formatos, no de nombres.
    """
    descompuesto = unicodedata.normalize("NFKD", texto or "")
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return _NO_ALFANUMERICO.sub(" ", sin_acentos.upper()).strip()


def _menciona_proveedor(proveedor: str, descripcion: str) -> bool:
    """Si el nombre del proveedor aparece en la descripcion del movimiento.

    Solo se usa como ULTIMO desempate, cuando monto y fecha son identicos. Es
    el dato mas pobre de los tres, asi que no puede ganarle a ninguno: sirve
    para dejar de elegir al azar cuando los otros dos no distinguen.
    """
    tokens = [t for t in _normaliza(proveedor).split() if len(t) >= _TOKEN_MINIMO]
    if not tokens:
        return False
    destino = _normaliza(descripcion)
    return all(token in destino for token in tokens)


@dataclass(frozen=True)
class Criterio:
    """Por que un movimiento le gano a los otros.

    Antes el score era un numero (`float(diff_monto) * 100 + diff_dias`) que
    no decia nada: dos candidatos con score casi igual se elegian por el orden
    en que la lista, y nadie podia reconstruir la decision ni aunque quisiera.
    Esto la hace explicable y la hace determinista.
    """

    diff_monto: Decimal
    diff_dias: int
    proveedor_en_descripcion: bool
    id_movimiento: object

    def clave(self) -> tuple:
        # El orden de comparacion ES la politica de eleccion, y esta escrita
        # aqui en una sola linea para que se pueda discutir:
        #   1. el monto manda (es la identidad del movimiento),
        #   2. a igual monto, la fecha mas cercana manda,
        #   3. a igual monto y fecha, el que menciona al proveedor,
        #   4. y si todo eso empata, el id, para que el resultado sea el mismo
        #      en cada corrida y no dependa del orden de la base.
        #
        # El `float` que se quito de en medio importaba: el proyecto preserva
        # Decimal de punta a punta y aqui se perdia justo en la comparacion que
        # decide a que comprobante se concilia.
        return (
            self.diff_monto,
            self.diff_dias,
            0 if self.proveedor_en_descripcion else 1,
            str(self.id_movimiento),
        )

    def texto(self) -> str:
        """La razon, en una linea que se pueda mostrar y discutir."""
        monto = "monto exacto" if self.diff_monto == 0 else f"monto con ${self.diff_monto} de diferencia"
        if self.diff_dias == 0:
            fecha = "misma fecha"
        elif self.diff_dias == 1:
            fecha = "con 1 dia de diferencia de fecha"
        else:
            fecha = f"con {self.diff_dias} dias de diferencia de fecha"
        proveedor = (
            " y el nombre del proveedor aparece en la descripcion"
            if self.proveedor_en_descripcion
            else ""
        )
        return f"{monto}, {fecha}{proveedor}"


class BestMatch(NamedTuple):
    """Un candidato que SI concilia. Sigue siendo una tupla de 5 para que el
    desempaquetado por posicion siga sirviendo."""

    bank_transaction: BankTransactionModel
    match_status: MatchStatus
    amount_diff: Decimal
    date_diff_days: int
    criterio: Criterio


class Discrepancy(NamedTuple):
    """Un movimiento que NO concilia pero que hay que mostrar.

    Sin esto, un ticket de $100 con un movimiento de $150 el mismo dia se ve
    exactamente igual que un ticket sin ningun movimiento: los dos caen en
    "sin match" y el cierre del mes no puede distinguirlos. El dispatcher ve que
    no cuadra, que es justo lo que tiene que ver.
    """

    bank_transaction: BankTransactionModel
    amount_diff: Decimal
    date_diff_days: int
    criterio: Criterio


class ReconciliationResult:
    def __init__(
        self,
        ticket: TicketModel,
        bank_transaction: BankTransactionModel,
        match_status: MatchStatus,
        amount_diff: Decimal,
        date_diff_days: int,
        criterio: Criterio,
    ):
        self.ticket = ticket
        self.bank_transaction = bank_transaction
        self.match_status = match_status
        self.amount_diff = amount_diff
        self.date_diff_days = date_diff_days
        self.criterio = criterio


async def run_reconciliation(
    db: AsyncSession,
    request: ReconciliationRunRequest
) -> ReconciliationRunResponse:
    """
    Corre el motor de conciliacion: empareja tickets contra movimientos del banco.

    Criterios (ver `Criterio.clave`, que es donde esta la politica de verdad):

    - PERFECT: el monto cae dentro de la tolerancia Y la fecha dentro de la
      ventana. Es el unico que se da por bueno sin que alguien lo mire.
    - MANUAL: el monto cae dentro de la tolerancia pero la fecha se sale de la
      ventana, o al reves. Se registra y alguien lo confirma.
    - DISCREPANCY: hay un movimiento con la fecha dentro de la ventana y OTRO
      monto. No se concilia: se reporta, porque "el dinero salio por otra
      cantidad" y "no salio nada" no pueden verse igual en un cierre.

    Un ticket sin ningun movimiento parecido queda como no conciliado. Esa es la
    unica categoria que de verdad significa "no encontramos nada".
    """
    tickets = await _get_unreconciled_tickets(db, request)
    bank_transactions = await _get_unreconciled_bank_transactions(db, request)

    matches: list[ReconciliationResult] = []
    matched_ticket_ids = set()
    matched_bank_ids = set()

    for ticket in tickets:
        disponibles = [bt for bt in bank_transactions if bt.id not in matched_bank_ids]

        best_match = _find_best_match(
            ticket,
            disponibles,
            request.amount_tolerance,
            request.date_tolerance_days,
        )

        if best_match:
            bank_tx, match_status, amount_diff, date_diff, criterio = best_match
            matches.append(
                ReconciliationResult(
                    ticket, bank_tx, match_status, amount_diff, date_diff, criterio
                )
            )
            matched_ticket_ids.add(ticket.id)
            matched_bank_ids.add(bank_tx.id)
            continue

        # No hubo nada dentro de tolerancia de monto. Antes de darlo por
        # "no encontramos nada", se mira si hay un movimiento en la fecha
        # correcta con otra cantidad: eso es una discrepancia y hay que
        # reportarla, no esconderla en el mismo cubo que un ticket sin su
        # movimiento.
        discrepancia = _find_discrepancia(
            ticket,
            disponibles,
            request.date_tolerance_days,
        )

        if discrepancia:
            bank_tx, amount_diff, date_diff, criterio = discrepancia
            matches.append(
                ReconciliationResult(
                    ticket,
                    bank_tx,
                    MatchStatus.DISCREPANCY,
                    amount_diff,
                    date_diff,
                    criterio,
                )
            )
            matched_ticket_ids.add(ticket.id)
            matched_bank_ids.add(bank_tx.id)

    await _save_reconciliations(db, matches)

    return _build_response(
        tickets, bank_transactions, matches, matched_ticket_ids, matched_bank_ids
    )


async def _get_unreconciled_tickets(
    db: AsyncSession, request: ReconciliationRunRequest
) -> list[TicketModel]:
    """Tickets todavia sin conciliar, del mas antiguo al mas reciente.

    El orden no es cosmetico. El emparejamiento es voraz: el primer ticket que
    encuentra un movimiento lo toma, y el siguiente ya no lo puede usar. Sin un
    ORDER BY, el orden es el que devuelva la base, que en Postgres no esta
    garantizado y cambia con el VACUUM o con un plan distinto. Entonces dos
    corridas con los mismos datos podrian emparejar distinto, y el ticket que
    se queda con el movimiento correcto dependeria de eso.
    """
    query = select(TicketModel).where(TicketModel.company_id == request.company_id)

    if request.date_from:
        query = query.where(TicketModel.expense_date >= request.date_from)
    if request.date_to:
        query = query.where(TicketModel.expense_date <= request.date_to)

    subquery = select(ReconciliationModel.ticket_id).where(ReconciliationModel.ticket_id.is_not(None))
    query = query.where(TicketModel.id.not_in(subquery))

    # Por fecha y luego por id: la fecha es el orden natural de un cierre, y el
    # id desempata dos tickets del mismo dia para que no dependa de nada mas.
    query = query.order_by(TicketModel.expense_date, TicketModel.id)

    result = await db.execute(query)
    return list(result.scalars().all())


async def _get_unreconciled_bank_transactions(
    db: AsyncSession, request: ReconciliationRunRequest
) -> list[BankTransactionModel]:
    """Movimientos bancarios que nadie ha tomado.

    Sin `ORDER BY` a proposito: aqui el orden de la lista no cambia el
    resultado. El criterio de eleccion es una tupla que termina en el id del
    movimiento, y el id es unico, asi que la comparacion es total y gana
    siempre el mismo. Poner un orden seria hacer creer que el recorrido importa
    cuando el recorrido no existe: en los tickets si importa (el emparejamiento
    es voraz) y por eso ahi si hay `ORDER BY`.
    """
    query = select(BankTransactionModel).where(
        and_(
            BankTransactionModel.company_id == request.company_id,
            BankTransactionModel.is_reconciled == False
        )
    )

    if request.date_from:
        query = query.where(BankTransactionModel.transaction_date >= request.date_from)
    if request.date_to:
        query = query.where(BankTransactionModel.transaction_date <= request.date_to)

    result = await db.execute(query)
    return list(result.scalars().all())


def _criterio_de(
    ticket: TicketModel,
    bank_tx: BankTransactionModel,
    amount_diff: Decimal,
    date_diff: int,
) -> Criterio:
    return Criterio(
        diff_monto=amount_diff,
        diff_dias=date_diff,
        proveedor_en_descripcion=_menciona_proveedor(
            ticket.provider_name, bank_tx.description
        ),
        id_movimiento=bank_tx.id,
    )


def _diffs(ticket: TicketModel, bank_tx: BankTransactionModel) -> tuple[Decimal, int]:
    """Diferencias de monto y fecha contra un movimiento.

    El total del ticket es positivo y el movimiento del banco es NEGATIVO en un
    gasto, asi que se comparan valores absolutos. La diferencia de monto
    siempre es positiva a proposito: no tiene direccion, y laeva la direccion
    la tiene el signo del movimiento.
    """
    amount_diff = abs(abs(ticket.total_amount) - abs(bank_tx.amount))
    date_diff = abs((ticket.expense_date - bank_tx.transaction_date).days)
    return amount_diff, date_diff


def _find_best_match(
    ticket: TicketModel,
    bank_transactions: list[BankTransactionModel],
    amount_tolerance: Decimal,
    date_tolerance_days: int,
) -> BestMatch | None:
    """
    El movimiento que mejor concilia con este ticket, o None.

    Solo devuelve candidatos cuyo MONTO cae dentro de la tolerancia. La fecha se
    usa para ordenar y para calificar, nunca para descartar: un comprobante
    pagado 10 dias despues sigue siendo el comprobante, y bajarlo a "sin
    match" solo porque la fecha se sale es como se pierde la conciliacion.

    Devuelve None solo cuando ningun movimiento se acerca en monto. La
    discrepancia (movimiento en la fecha con otro monto) la busca otra
    funcion, porque es una respuesta distinta a "no hay nada".

    El ganador se guarda en UNA variable y no en dos. Antes eran `elegido` y
    `elegido_criterio`, que se escribian juntos pero en dos lineas: nada impedia
    que uno quedara puesto y el otro no, y `BestMatch.criterio` es obligatorio.
    Un solo `(clave, movimiento, criterio)` hace que esa combinacion sea
    imposible de construir a medias, en vez de imposible de comprobar despues.
    """
    mejor: tuple[tuple, BankTransactionModel, Criterio] | None = None

    for bank_tx in bank_transactions:
        amount_diff, date_diff = _diffs(ticket, bank_tx)

        if amount_diff > amount_tolerance:
            continue

        criterio = _criterio_de(ticket, bank_tx, amount_diff, date_diff)
        clave = criterio.clave()

        if mejor is None or clave < mejor[0]:
            mejor = (clave, bank_tx, criterio)

    if mejor is None:
        return None

    _, elegido, elegido_criterio = mejor

    amount_diff, date_diff = _diffs(ticket, elegido)

    if date_diff <= date_tolerance_days:
        match_status = MatchStatus.PERFECT
    else:
        match_status = MatchStatus.MANUAL

    return BestMatch(
        bank_transaction=elegido,
        match_status=match_status,
        amount_diff=amount_diff,
        date_diff_days=date_diff,
        criterio=elegido_criterio,
    )


def _find_discrepancia(
    ticket: TicketModel,
    bank_transactions: list[BankTransactionModel],
    date_tolerance_days: int,
) -> Discrepancy | None:
    """El movimiento mas cercano en FECHA cuando ninguno cuadra en monto.

    Solo mira la fecha. Es la unica senal que queda: si el banco movio algo el
    mismo dia por otra cantidad, eso es el hecho que hay que reportar, y el
    monto mas cercano es el sospechoso mas obvio.

    Antes de este cambio esto no existia, y por eso el contador de
    discrepancias de la respuesta era siempre cero: la categoria estaba
    declarada en el schema, pintada en la UI, y era inalcanzable.
    """
    mejor: tuple | None = None
    elegido: BankTransactionModel | None = None
    elegido_criterio: Criterio | None = None

    for bank_tx in bank_transactions:
        amount_diff, date_diff = _diffs(ticket, bank_tx)

        if date_diff > date_tolerance_days:
            continue

        criterio = _criterio_de(ticket, bank_tx, amount_diff, date_diff)
        clave = criterio.clave()

        if mejor is None or clave < mejor:
            mejor = clave
            elegido = bank_tx
            elegido_criterio = criterio

    if elegido is None or elegido_criterio is None:
        return None

    amount_diff, date_diff = _diffs(ticket, elegido)
    return Discrepancy(
        bank_transaction=elegido,
        amount_diff=amount_diff,
        date_diff_days=date_diff,
        criterio=elegido_criterio,
    )


async def _save_reconciliations(
    db: AsyncSession, matches: list[ReconciliationResult]
) -> None:
    """Guarda lo que decidio el motor.

    Una DISCREPANCIA tambien se guarda y tambien marca el movimiento como
    revisado. Si no, el proximo run volveria a proponer el mismo movimiento y
    se acumularia una fila por corrida para el mismo hecho. El ticket tampoco
    se vuelve a proponer, porque `_get_unreconciled_tickets` excluye los que
    ya tienen conciliacion. Para deshacer una discrepancia hay que borrar la
    conciliacion (`DELETE /reconciliations/{id}`), que es la accion que
    corresponde: alguien confirmo que no era ese movimiento.
    """
    for match in matches:
        reconciliation = ReconciliationModel(
            ticket_id=match.ticket.id,
            bank_transaction_id=match.bank_transaction.id,
            match_status=match.match_status.value,
        )
        db.add(reconciliation)

        match.bank_transaction.is_reconciled = True

    await db.commit()


def _build_response(
    tickets: list[TicketModel],
    bank_transactions: list[BankTransactionModel],
    matches: list[ReconciliationResult],
    matched_ticket_ids: set,
    matched_bank_ids: set,
) -> ReconciliationRunResponse:
    """Arma la respuesta.

    `unmatched_tickets` cuenta los que no tienen NADA: ni concilia ni
    discrepancia. Una discrepancia esta contabilizada pero no esta cuadrada, y
    por eso tambien tiene su propio contador. Si `unmatched_tickets` sumara
    las discrepancias, el cierre del mes veria "todo bien" con el mismo numero
    que cuando el banco no movio nada.
    """
    perfect = sum(1 for m in matches if m.match_status is MatchStatus.PERFECT)
    manual = sum(1 for m in matches if m.match_status is MatchStatus.MANUAL)
    discrepancy = sum(1 for m in matches if m.match_status is MatchStatus.DISCREPANCY)

    match_details = [
        ReconciliationMatchDetail(
            ticket_id=m.ticket.id,
            ticket_provider=m.ticket.provider_name,
            ticket_amount=m.ticket.total_amount,
            ticket_date=m.ticket.expense_date,
            bank_transaction_id=m.bank_transaction.id,
            bank_description=m.bank_transaction.description,
            bank_amount=m.bank_transaction.amount,
            bank_date=m.bank_transaction.transaction_date,
            match_status=m.match_status.value,
            amount_diff=m.amount_diff,
            date_diff_days=m.date_diff_days,
            criterio=m.criterio.texto(),
        )
        for m in matches
    ]

    return ReconciliationRunResponse(
        total_tickets=len(tickets),
        total_bank_transactions=len(bank_transactions),
        perfect_matches=perfect,
        manual_review=manual,
        discrepancies=discrepancy,
        unmatched_tickets=len(tickets) - len(matched_ticket_ids),
        unmatched_bank_transactions=len(bank_transactions) - len(matched_bank_ids),
        matches=match_details,
    )