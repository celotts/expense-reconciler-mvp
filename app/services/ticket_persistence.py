"""Persistir una extraccion: el gate, y el ticket que sale de el.

Vive en `services/` y no en `app/api/tickets.py` por una razon concreta: el
escaneo de carpeta (`app/services/scan_service.py`) necesita exactamente la
misma operacion y no puede importar de un router. Importar un router desde un
service invierte la capa, y lo que se acaba teniendo son dos implementaciones
de "crear un ticket desde una extraccion" que divergen: una con el muestreo de
exactitud y otra sin el, y el Scanner guarda tickets que no se pueden medir.

`app/api/tickets.py:_persist_extracted` se queda como delegacion de una linea
para no romper los tres archivos de test que la importan desde ahi, y para que
la ruta que ya existe siga siendo la misma ruta: no hay dos caminos de
persistencia, hay uno y dos nombres.
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    UNKNOWN_PROVIDER,
    ExtractionStatus,
    SourceType,
    SpotCheckStatus,
)
from app.models.ticket import TicketModel
from app.services.accuracy_service import en_muestra
from app.services.confidence_gate import compute_source_hash, gate_ticket
from app.services.document_service import guardar_documento
from app.services.parser_service import TicketExtractionResult

logger = logging.getLogger(__name__)


async def persistir_extraccion(
    db: AsyncSession,
    company_id: UUID,
    extracted: TicketExtractionResult,
    content: bytes,
    source_type: SourceType,
    source_file: str | None = None,
    content_type: str | None = None,
) -> TicketModel:
    """Gate + persistencia. Unico camino de entrada para datos extraidos.

    `content` es el archivo original y `extracted` lo que se entendio de el. Los
    dos se guardan, y son cosas distintas: el segundo sin el primero no se puede
    revisar ni auditar. Ver `app/services/document_service.py`.
    """
    # El origen lo decide la ruta de captura, no esta funcion. Antes todo se
    # guardaba como `llm` porque el gate recibia su default, y con eso la
    # columna `confidence_source` miente sobre de donde salio el dato: un PDF
    # leido con regex aparecia como lectura de modelo. Sin esa verdad no hay
    # forma de medir la exactitud de la IA, porque se promedia la IA con un
    # regex que casi nunca falla.
    decision = gate_ticket(
        provider_name=extracted.provider_name,
        total_amount=extracted.total_amount,
        tax_amount=extracted.tax_amount,
        # Un documento sin fecha no se fecha con la de hoy: se guarda con la
        # fecha que se va a resolver en la cola. Fabricar la de hoy esconde un
        # gasto de marzo en el cierre de septiembre, que es el error que este
        # producto no puede cometer.
        expense_date=extracted.expense_date,
        provider_tax_id=extracted.provider_tax_id,
        subtotal=extracted.subtotal,
        confidence=extracted.confidence,
        source=extracted.confidence_source,
    )
    source_hash = compute_source_hash(content)

    # Idempotencia de carga masiva: el mismo archivo no crea dos tickets.
    #
    # El filtro por `company_id` no es un refinamiento, es la garantia de que el
    # hash significa algo. `source_hash` es el SHA-256 del archivo y no lleva
    # empresa dentro, asi que el mismo comprobante subido por dos empresas es el
    # mismo hash. Buscando solo por hash, la segunda empresa se encontraba con el
    # ticket de la primera y lo devolvia: el gasto no se registraba para ella y
    # ademas le mostraba un ticket ajeno, de otra empresa. Por eso el indice
    # unico es `(company_id, source_hash)`, no `source_hash` solo.
    existing = await db.execute(
        select(TicketModel).where(
            TicketModel.source_hash == source_hash,
            TicketModel.company_id == company_id,
        )
    )
    dup = existing.scalar_one_or_none()
    if dup is not None:
        return dup

    ticket = TicketModel(
        company_id=company_id,
        provider_name=extracted.provider_name or UNKNOWN_PROVIDER,
        provider_tax_id=extracted.provider_tax_id,
        total_amount=extracted.total_amount,
        tax_amount=extracted.tax_amount,
        # Se guarda, aunque antes no se guardara. Es el campo con el que el
        # gate valido que `subtotal + IVA == total`, asi que sin el no se puede
        # auditar por que el ticket se aprobo. Va como None cuando el
        # documento no trae subtotal: un 0 seria un dato falso con apariencia
        # de dato, y haria que la cuenta pareciera cuadrar sin comprobar nada.
        subtotal=extracted.subtotal,
        category=extracted.category,
        raw_text=extracted.raw_text,
        confidence=decision.persisted_confidence,
        confidence_source=decision.confidence_source.value,
        extraction_status=decision.status.value,
        source_type=source_type.value,
        source_file=source_file,
        source_hash=source_hash,
        # `expense_date` es NOT NULL, asi que un documento sin fecha necesita
        # un valor. Se guarda el dia local en que se registro, que es la unica
        # fecha que si se sabe de cierto, y el ticket queda en la cola con
        # `date_missing` a la vista. El tradeoff: un ticket fechado
        # provisionalmente, marcado como pendiente de fecha. El que se
        # descartaba antes era el otro: un ticket con fecha y sin nada que
        # advertise que la fecha es inventada, que es indistinguible de un
        # gasto real de ese dia.
        #
        # `date.today()` y no `utcnow().date()`. No es indistinto: `utcnow()` es
        # lo correcto para `created_at`, que es un instante, y lo equivocado
        # aqui, que es una fecha de negocio. Un servidor en UTC-6 que corre a
        # las 20:00 del dia 27 ya esta en el dia 28 en UTC, y el ticket
        # quedaria fechado manana. Eso es el mismo error que se esta
        # corrigiendo aqui, con un dia de diferencia.
        expense_date=extracted.expense_date or date.today(),
        validation_errors=decision.validation.as_text(),
        # Muestreo de exactitud: se marca al insertar, por hash de contenido, y
        # solo si el gate aprobo solo. Tres condiciones, y cada una importa:
        #
        # - Solo AUTO_APROBADO. Un ticket que esta en la cola no se puede medir:
        #   su exactitud ya la decidio el gate, no el extractor. Y meterlo
        #   inflaria el promedio con casos que nunca fueron automaticos.
        # - Solo si tiene hash. La captura manual no es automatismo.
        # - La decision va por el contenido, no al azar, para que sea
        #   reproducible y no se pueda elegir la muestra a conveniencia.
        spot_check_status=(
            SpotCheckStatus.PENDIENTE.value
            if decision.status == ExtractionStatus.AUTO_APROBADO
            and en_muestra(source_hash)
            else None
        ),
    )
    db.add(ticket)
    await db.flush()

    # El documento va antes del commit, en la misma transaccion.
    #
    # El orden importa y no es cosmetico. Un `commit` aqui seguido de un guardado
    # del documento dejaria una ventana en la que el ticket existe y el papel no,
    # y en esa ventana un ticket de la cola aparece sin nada que revisar sin que
    # se pueda distinguir de "nunca se subio". Con el guardado antes, el ticket
    # llega con su documento o no llega ninguno de los dos.
    #
    # Y si el guardado falla, el ticket NO se pierde: `guardar_documento` usa un
    # savepoint y devuelve False. Perder un gasto que se leyo bien por un
    # problema de almacenamiento seria peor que un gasto sin comprobante adjunto,
    # que ademas queda visible.
    await guardar_documento(
        db, ticket, content,
        content_type=content_type,
        nombre_archivo=source_file,
    )

    await db.commit()
    await db.refresh(ticket)
    return ticket
