"""Gate de confianza: decide que tickets se auto-aprueban y cuales van a cola.

Este modulo es deliberadamente determinista. La IA lee el documento; este
codigo decide si lo que leyo se cree. Las dos cosas separadas son la unica
forma de que la exactitud sea medible en vez de supuesta.

Consistencia aritmetica primero, confianza despues:
  - un total que no cuadra con subtotal + IVA esta mal, diga lo que diga la IA
  - un RFC malformado esta mal, diga lo que diga la IA
  - la confianza solo decide entre las que si pasaron los checks
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.core.enums import (
    AUTO_APPROVE_CONFIDENCE, ConfidenceSource, ExtractionStatus, REVIEW_CONFIDENCE,
)

# Tolerancia de 1 centimo para comparar la aritmetica del documento. Los
# redondeos de IVA a 2 decimales son normales y no son un error.
MONEY_TOLERANCE = Decimal("0.01")

# Un ticket con fecha futura casi siempre es una lectura equivocada (digamos,
# 2026 leido donde decia 2026-02-15 y el modelo corto el mes).
MAX_FUTURE_DAYS = 2
MAX_PAST_YEARS = 3


@dataclass
class ValidationOutcome:
    """Resultado de los checks deterministas sobre una extraccion."""

    passed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_text(self) -> str | None:
        """Serializa los fallos para persistirlos y poder estratificar el error."""
        return "; ".join(self.failures) if self.failures else None


@dataclass
class GateDecision:
    """Veredicto del gate: a que estado va el ticket y por que."""

    status: ExtractionStatus
    confidence: float
    confidence_source: ConfidenceSource
    validation: ValidationOutcome
    reasons: list[str] = field(default_factory=list)

    @property
    def needs_human(self) -> bool:
        return ExtractionStatus(self.status).is_open

    @property
    def persisted_confidence(self) -> Decimal | None:
        """La confianza tal como se guarda en la columna.

        La captura manual devuelve None a proposito. Poner 0.0 seria medir de
        mas: contaminaria el promedio de confianza de la IA con datos que no
        salieron de la IA, y ese promedio es la metrica que respalda el
        objetivo de exactitud. Con NULL, cualquier promedio ignora solo los
        tickets automaticos, que es lo que se quiere medir.
        """
        if self.confidence_source is ConfidenceSource.MANUAL:
            return None
        return confidence_to_decimal(self.confidence)


def confidence_to_decimal(confidence: float | None) -> Decimal | None:
    """Redondea a 3 decimales (columna Numeric(4,3)) y acota a 0-1."""
    if confidence is None:
        return None
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return None
    if c != c:  # NaN
        return None
    c = max(0.0, min(1.0, c))
    return Decimal(str(round(c, 3)))


def validate_extraction(
    provider_name: str | None,
    total_amount: Decimal | None,
    tax_amount: Decimal | None,
    expense_date: date | None,
    provider_tax_id: str | None = None,
    subtotal: Decimal | None = None,
) -> ValidationOutcome:
    """Checks deterministas. Cada uno atrapa un modo de falla concreto y
    barato de detectar."""
    out = ValidationOutcome()

    name = (provider_name or "").strip()
    if not name or name == "Unknown Provider":
        out.failures.append("provider_missing")
    else:
        out.passed.append("provider_present")

    if total_amount is None or total_amount <= 0:
        out.failures.append("total_not_positive")
    else:
        out.passed.append("total_positive")

    if tax_amount is not None and tax_amount < 0:
        out.failures.append("tax_negative")
    elif total_amount is not None and tax_amount is not None and tax_amount > total_amount:
        out.failures.append("tax_exceeds_total")
    else:
        out.passed.append("tax_within_range")

    # Aritmetica del documento. Solo si vino subtotal: no todos los tickets lo traen.
    if subtotal is not None and tax_amount is not None and total_amount is not None:
        esperado = subtotal + tax_amount
        if abs(esperado - total_amount) > MONEY_TOLERANCE:
            # Se guardan los dos numeros. Con solo el esperado, revisar 500
            # tickets es comparar a ciegas contra el documento original.
            out.failures.append(
                f"subtotal_plus_tax_mismatch(leido={total_amount},esperado={esperado})"
            )
        else:
            out.passed.append("arithmetic_consistent")

    if provider_tax_id:
        from app.schemas.ticket import RFC_REGEX  # import local: evita ciclo
        rfc = provider_tax_id.strip().upper()
        if not RFC_REGEX.fullmatch(rfc):
            out.failures.append("malformed_rfc")
        else:
            out.passed.append("rfc_valid")

    if expense_date is None:
        out.failures.append("date_missing")
    else:
        today = datetime.now(timezone.utc).date()
        if expense_date > today + timedelta(days=MAX_FUTURE_DAYS):
            out.failures.append(f"date_in_future({expense_date})")
        elif expense_date < _shift_years(today, -MAX_PAST_YEARS):
            out.failures.append(f"date_too_old({expense_date})")
        else:
            out.passed.append("date_plausible")

    return out


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 de febrero
        return d.replace(year=d.year + years, day=28)


def decide_status(
    confidence: float | None,
    validation: ValidationOutcome,
    source: ConfidenceSource = ConfidenceSource.LLM,
) -> GateDecision:
    """Regla de decision.

    La confianza alta no compensa checks rotos: si el total no cuadra, el
    ticket va a revision aunque la IA diga 0.99. Es deliberado. Un 0.99 sobre
    datos aritmeticamente imposibles indica que el modelo esta siendo
    confiante en algo falso, que es peor que estar abierto e incierto: lo
    primero se cuela solo y despues nadie lo revisa.
    """
    reasons: list[str] = []

    if not validation.ok:
        for f in validation.failures:
            reasons.append(f"check:{f}")
        # Sin proveedor, sin total o sin fecha no hay nada aprovechable:
        # el ticket no puede existir todavia, va a la cola de pendientes.
        blocking = {"provider_missing", "total_not_positive", "date_missing"}
        if blocking.intersection(validation.failures):
            status = ExtractionStatus.PENDIENTE
        else:
            status = ExtractionStatus.REQUIERE_REVISION
        return GateDecision(status, confidence or 0.0, source, validation, reasons)

    c = confidence if confidence is not None else 0.0
    if c <= 0.0 and source == ConfidenceSource.MANUAL:
        return GateDecision(
            ExtractionStatus.APROBADO, 0.0, source, validation, ["manual_entry"],
        )
    if c >= AUTO_APPROVE_CONFIDENCE:
        reasons.append(f"confidence_high({c})")
        status = ExtractionStatus.AUTO_APROBADO
    elif c >= REVIEW_CONFIDENCE:
        reasons.append(f"confidence_medium({c})")
        status = ExtractionStatus.REQUIERE_REVISION
    else:
        reasons.append(f"confidence_low({c})")
        status = ExtractionStatus.REQUIERE_REVISION

    return GateDecision(status, c, source, validation, reasons)


def gate_ticket(
    provider_name: str | None,
    total_amount: Decimal | None,
    tax_amount: Decimal | None,
    expense_date: date | None,
    provider_tax_id: str | None = None,
    subtotal: Decimal | None = None,
    confidence: float | None = None,
    source: ConfidenceSource = ConfidenceSource.LLM,
) -> GateDecision:
    """Entrada unica: valida y decide en una sola llamada."""
    validation = validate_extraction(
        provider_name, total_amount, tax_amount, expense_date,
        provider_tax_id, subtotal,
    )
    return decide_status(confidence, validation, source)


def gate_manual_ticket(
    provider_name: str | None,
    total_amount: Decimal | None,
    tax_amount: Decimal | None,
    expense_date: date | None,
    provider_tax_id: str | None = None,
) -> GateDecision:
    """Captura manual: una persona tecleo los datos, asi que no hay confianza
    que medir. Pasa los mismos checks por consistencia y queda APROBADO.

    La confianza se deja en None a proposito. Poner 1.0 seria mentir: la
    confianza mide lectura automatica, y un dedo humano no se mide.
    """
    validation = validate_extraction(
        provider_name, total_amount, tax_amount, expense_date, provider_tax_id,
    )
    if validation.ok:
        return GateDecision(
            ExtractionStatus.APROBADO, 0.0, ConfidenceSource.MANUAL, validation,
            ["manual_entry"],
        )
    return decide_status(None, validation, ConfidenceSource.MANUAL)


def compute_source_hash(content: bytes) -> str:
    """SHA-256 del contenido, para idempotencia de carga masiva."""
    return hashlib.sha256(content).hexdigest()
