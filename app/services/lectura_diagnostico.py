"""Ver POR QUE se leyo asi un comprobante, sin guardarlo.

QUE HACE Y POR QUE EXISTE
========================

La cascada de lectura decide en codigo y solo expone **quien gano**. Cuando un
comprobante cae a `REQUIERE_REVISION` no hay forma de saber si fue porque:

  - el PDF no traia capa de texto y el OCR no vio nada;
  - el OCR si leyó texto pero el parser no encontro el folio ni el total;
  - el modelo fallo por configuracion (extractor apagado) o de verdad;
  - la lectura fue buena y lo que fallo fueron los **checks** del gate.

Esas cuatro cosas piden cuatro arreglos distintos, y son indistinguibles desde la
respuesta de la API. Un "no se pudo leer" que solo dice eso obliga a ir a buscar el
log del servidor; y si nadie busca, el mismo comprobante vuelve a fallar mañana.

Es el mismo motivo que `motivo_de_fallo_del_modelo` (`capture.py`): "el proveedor no
se pudo leer" y "el extractor esta apagado" producen el mismo ticket y piden cosas
opuestas. Ahi se separaron dos mensajes. Aqui se separa la cascada entera.

LO QUE NO HACE, Y POR QUE
========================

**No guarda nada.** Ni ticket, ni documento, ni movimiento. Es el mismo criterio que
`simular` en `POST /scan`: con `archivar` activo por omision, la primera corrida deja
la carpeta de entrada vacia, y conviene ver eso antes de que ocurra. Aqui la analogia
es mas fuerte porque **repetir el mismo archivo muchas veces no llenaria la base de
duplicados**, que es lo que pasa si se prueba con `extract-and-create`.

Tambien por eso no hay forma de guardar desde aqui: un endpoint que "a veces guarda" es
un endpoint del que nadie sabe que hace. Para guardar, `POST /tickets/extract-and-create`
o `POST /scan/files/{id}/reprocess`, que son explicitos.

**No inventa un motor de lectura.** Reusa `capture.capture_ticket` con los mismos
extractores, y ademas corre los pasos INTERNOS por separado para poder contarlos. Si
esta version y la de produccion se desviaran, el diagnostico diria una cosa y el
sistema haria otra, que es peor que no tener diagnostico. Por eso el resultado que se
devuelve es el de `capture_ticket` de verdad, y los pasos son una explicacion de como
se llega a el.

QUE DEVUELVE
============

- `pasos`: cada escalon que se intento, que dio, y **por que se dejo de seguir**.
- `veredicto`: que haria el gate con esta lectura, SIN escribirla. Los mismos checks
  de `gate_ticket`, porque un diagnostico que dijera "pasaria" con checks distintos a
  los del gate seria mentira.
- `contraste_con_gate`: si el ticket ya existe en la base, en que estado esta y si la
  lectura de ahora produciria el mismo. Sirve para el caso "esta bien leido pero el
  gate lo mando a revision" — que es un bug del gate, no de la lectura.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Callable
from uuid import UUID

if TYPE_CHECKING:  # pragma: no cover - solo para el type checker
    from app.services.ocr import ResultadoOCR

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.archivo_real import resolver_tipo
from app.core.enums import ConfidenceSource, ExtractionStatus
from app.core.time import utcnow
from app.models.ticket import TicketModel
from app.services.ai_extractor import ai_extractor
from app.services.capture import (
    OCR_MIN_CHARS_PARA_INTENTAR,
    PDF_MIN_CHARS_PARA_INTENTAR,
    ExtractionUnavailable,
    capture_ticket,
)
from app.services.confidence_gate import gate_ticket
from app.services.parser_service import (
    TicketExtractionResult,
    _parse_receipt_text,
    extract_pdf_text,
    render_pdf_pages,
)

logger = logging.getLogger(__name__)

#: Como leer texto de una imagen. La misma firma que el `ocr_reader` de
#: `capture_ticket`, y por la misma razon: sin inyeccion, la rama del OCR no se puede
#: probar sin el binario de Tesseract instalado, y una suite que depende de un binario
#: del sistema se aprende a saltarse. Ver el docstring de `diagnosticar_lectura`.
OcrDiagnosticoFn = Callable[[bytes], "ResultadoOCR"]


# ---------------------------------------------------------------------------
# Los pasos
# ---------------------------------------------------------------------------


@dataclass
class PasoLectura:
    """Un escalon de la cascada, y que hizo.

    `aceptado` y `motivo` van juntos a proposito: un paso que no dio resultado tiene
    que decir POR QUE, y "no" sin motivo es el mismo "no se pudo leer" que se vino a
    arreglar. Es la distincion entre "miro y no encontro" y "no pudo mirar", que
    piden arreglos opuestos: revisar el parser frente a instalar Tesseract.
    """

    escalon: str
    #: `reglas`, `ocr`, `modelo`, `render`, `pdf_texto`, `formato`.
    motor: str
    #: Que devolvio, resumido. `None` si el escalon no llego a producir nada.
    dio: dict | None = None
    #: Si el resultado sirvio o no, segun `_es_extraccion_util` y `_exige_items`.
    aceptado: bool = False
    #: Por que se dejo de seguir. `None` cuando el escalon gano.
    motivo: str | None = None
    #: Un paso que costo dinero:.modelo. Sabe cuanto.
    costo: str | None = None


@dataclass
class ResultadoLectura:
    """Lo que se leyo, y por que. Es lo que devuelve el endpoint."""

    formato_detectado: str
    formato_declarado: str | None
    #: El motivo por el que se corrigio la etiqueta del cliente, si se corrigio.
    formato_corregido: str | None = None
    pasos: list[PasoLectura] = field(default_factory=list)
    extraccion: TicketExtractionResult | None = None
    error: str | None = None

    # --- Lo que el gate haria con esto, sin escribirlo ------------------
    veredicto_status: ExtractionStatus | None = None
    veredicto_confidence: float | None = None
    veredicto_confidence_persisted: Decimal | None = None
    veredicto_confidence_source: ConfidenceSource | None = None
    veredicto_reasons: list[str] = field(default_factory=list)
    checks_pasados: list[str] = field(default_factory=list)
    checks_fallidos: list[str] = field(default_factory=list)

    # --- El contraste con lo que ya esta guardado -----------------------
    ticket_id: UUID | None = None
    ticket_status_actual: ExtractionStatus | None = None
    ticket_source_actual: ConfidenceSource | None = None
    ticket_leido_en: datetime | None = None
    #: `True` cuando la lectura de ahora produciria el mismo estado que el guardado.
    coincide_con_guardado: bool | None = None

    @property
    def guardado(self) -> bool:
        return self.ticket_id is not None


# ---------------------------------------------------------------------------
# El servicio
# ---------------------------------------------------------------------------


async def diagnosticar_lectura(
    db: AsyncSession,
    content: bytes,
    *,
    declarado: str | None = None,
    nombre: str | None = None,
    ticket_id: UUID | None = None,
    ocr_reader: OcrDiagnosticoFn | None = None,
) -> ResultadoLectura:
    """Lee un comprobante y explica como se leyo. No escribe nada.

    Es el endpoint de diagnostico: corre `capture_ticket` de verdad para que el
    resultado sea el de produccion, y ademas recorre los escalones por separado para
    poder contarlos. Los dos caminos comparten el parser y las reglas de confianza,
    que es donde suele estar el problema.

    `ocr_reader` existe por el mismo motivo que en `capture_ticket`: sin el, la rama
    de OCR **no se puede probar**. SQLite aside, aqui el problema es que Tesseract es
    un binario del sistema: una suite que lo necesito seria roja en la maquina de
    quien no lo tiene, y ese test aprenderia a saltarse. Con la inyeccion, el camino
    del OCR —incluido el motivo de "no vio texto" frente a "no esta disponible"— se
    prueba sin el binario.
    """
    formato, motivo = resolver_tipo(content, declarado)
    salida = ResultadoLectura(
        formato_detectado=formato,
        formato_declarado=declarado,
        formato_corregido=motivo,
    )

    if motivo:
        # Va tambien en la respuesta, no solo en el log. Un cliente que manda
        # `file_type=image` para un PDF esta eligiendo mal la ruta de lectura, y
        # si no se le dice se lleva un resultado distinto del que esperaba sin
        # enterarse. Ver la regla 3 de `AGENTS.md`.
        logger.warning("Diagnostico: tipo de archivo corregido: %s", motivo)

    # --- El formato, y por donde tiene que entrar ------------------------
    if formato == "pdf":
        _explorar_pdf(content, salida, ocr_reader)
    elif formato == "image":
        _explorar_imagen(content, salida, ocr_reader)
    else:
        texto = content.decode("utf-8", errors="replace")
        salida.pasos.append(
            PasoLectura(
                escalon="formato",
                motor="texto_plano",
                dio={"caracteres": len(texto)},
                aceptado=True,
                motivo=None,
            )
        )
        _explorar_texto(texto, salida, exigir_items=False)

    # --- La lectura de verdad -------------------------------------------
    #
    # Va DESPUES de explorar para que, si esto revienta, la respuesta todavia
    # diga que se pudo averiguar. Un endpoint que devuelve 500 sin informacion
    # cuando lo interesante era el diagnostico seria inutil justo cuando mas falta.
    try:
        extraccion = await capture_ticket(
            content,
            formato,
            # `ai_extractor` es la INSTANCIA, no el modulo. Lo mismo que inyecta
            # `tickets._extract_from_upload`: si se importara el modulo, `None`
            # seria un atributo inexistente y no "no hay extractor", que es otra cosa
            # completamente distinta en la cascada.
            extract_from_image=ai_extractor.extract_from_image,
            extract_from_text=ai_extractor.extract_from_text,
            ocr_reader=ocr_reader,
        )
    except ExtractionUnavailable as exc:
        salida.error = str(exc)
        return salida
    except Exception as exc:  # noqa: BLE001 - aqui el fallo se reporta, no se propaga
        logger.warning("Diagnostico: la lectura fallo: %s", exc)
        salida.error = f"{type(exc).__name__}: {exc}"
        return salida

    salida.extraccion = extraccion
    _anotar_veredicto(salida, extraccion)

    if ticket_id is not None:
        await _contrastar(db, salida, extraccion, ticket_id)

    return salida


# ---------------------------------------------------------------------------
# Los exploradores: uno por rama de la cascada
# ---------------------------------------------------------------------------
#
# NO son una segunda implementacion de la cascada. No deciden nada: miran los mismos
# pasos que `capture_ticket` y anotan que los hubo y que dieron. Si un dia la cascada
# cambia, esto queda desactualizado — y por eso cada explorador dice a que paso de
# `capture.py` corresponde, para que el que lo lea sepa donde esta la verdad.
#
# La razon de que sea codigo aparte y no un parametro de `capture_ticket`: instrumentar
# la cascada de verdad significa tocar las decisiones —Meter un `traza.append` dentro
# de los `if` que eligen— y ahi un forget cambia el comportamiento. Esto solo lee.


def _explorar_texto(texto: str, salida: ResultadoLectura, *, exigir_items: bool) -> None:
    """Pasos de `_cascada_texto` (`capture.py:585`)."""
    resultado = _parse_receipt_text(texto)
    paso = PasoLectura(
        escalon="reglas",
        motor="parser",
        dio=_resumen(resultado),
        aceptado=bool(resultado.provider_name) and resultado.total_amount > 0,
        costo="gratis",
    )
    if not paso.aceptado:
        paso.motivo = _por_que_no_sirvio(resultado)
    elif not resultado.items and exigir_items:
        # `_exige_items` esta a apagado si `ESCALAR_A_IA_SIN_LINEAS` es False. No se
        # reimplementa esa decision aqui: se lee el resultado y se dice que no hay
        # lineas, que es el hecho que la condicion consulta.
        paso.motivo = "las reglas aciertaron el encabezado pero no hay lineas de producto"
    salida.pasos.append(paso)


def _explorar_pdf(
    content: bytes, salida: ResultadoLectura, ocr_reader: OcrDiagnosticoFn | None
) -> None:
    """Pasos de `capture_ticket` para PDF (`capture.py:549-582`) y `_vision_pdf`."""
    try:
        texto = extract_pdf_text(content)
    except Exception as exc:  # noqa: BLE001
        # Un PDF que pdfplumber no abre es un PDF escaneado, cifrado o roto. Los tres
        # casos llevan a la misma rama, asi que el motivo importa menos que el hecho
        # de que se va a renderizar.
        salida.pasos.append(
            PasoLectura(
                escalon="pdf_texto",
                motor="pdfplumber",
                aceptado=False,
                motivo=f"no se pudo abrir el PDF: {exc}",
            )
        )
    else:
        if len(texto.strip()) < PDF_MIN_CHARS_PARA_INTENTAR:
            salida.pasos.append(
                PasoLectura(
                    escalon="pdf_texto",
                    motor="pdfplumber",
                    dio={"caracteres": len(texto.strip())},
                    aceptado=False,
                    motivo=(
                        f"solo {len(texto.strip())} caracteres, menos de los "
                        f"{PDF_MIN_CHARS_PARA_INTENTAR} del piso: es un PDF "
                        "escaneado, no un PDF de texto"
                    ),
                )
            )
        else:
            salida.pasos.append(
                PasoLectura(
                    escalon="pdf_texto",
                    motor="pdfplumber",
                    dio={"caracteres": len(texto.strip())},
                    aceptado=True,
                )
            )
            _explorar_texto(texto, salida, exigir_items=True)
            return

    # A partir de aqui es `_vision_pdf`: render, OCR pagina por pagina y modelo.
    paginas = _renderizar(content)
    if paginas is None:
        salida.pasos.append(
            PasoLectura(
                escalon="render",
                motor="pymupdf",
                aceptado=False,
                motivo="no se pudo renderizar el PDF a imagen",
            )
        )
        return

    salida.pasos.append(
        PasoLectura(
            escalon="render",
            motor="pymupdf",
            dio={"paginas": len(paginas)},
            aceptado=True,
        )
    )
    for indice, pagina in enumerate(paginas):
        _explorar_ocr(pagina, salida, ocr_reader, escalon=f"ocr_pagina_{indice + 1}")


def _renderizar(content: bytes) -> list[bytes] | None:
    """Las paginas del PDF como imagen, igual que `_vision_pdf`. `None` si no puede."""
    from app.services.capture import PDF_ESCALA_RENDER, PDF_MAX_PAGINAS_A_VISION

    try:
        return render_pdf_pages(
            content,
            max_pages=PDF_MAX_PAGINAS_A_VISION,
            scale=PDF_ESCALA_RENDER,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Diagnostico: no se pudo renderizar el PDF: %s", exc)
        return None


def _explorar_imagen(
    content: bytes, salida: ResultadoLectura, ocr_reader: OcrDiagnosticoFn | None
) -> None:
    """Pasos de `_desde_imagen` (`capture.py:788`).

    Una foto suelta va al OCR una sola vez: no hay paginas que renderizar.
    """
    _explorar_ocr(content, salida, ocr_reader, escalon="ocr")


def _explorar_ocr(
    pagina: bytes,
    salida: ResultadoLectura,
    ocr_reader: OcrDiagnosticoFn | None,
    *,
    escalon: str,
) -> None:
    """Un paso de OCR. El resto de la decision ya la tomo la rama que lo llamo."""
    from app.services.capture import OCRNoDisponible, _ocr_por_defecto

    leer = ocr_reader or _ocr_por_defecto
    try:
        leido = leer(pagina)
    except OCRNoDisponible as exc:
        # Este NO es un fallo de lectura: es la configuracion de la maquina, y por eso
        # tiene su propio motivo. Confundirlo con "el OCR no vio texto" hace que
        # alguien instale Tesseract en un servidor donde ya estaba.
        salida.pasos.append(
            PasoLectura(
                escalon=escalon,
                motor="tesseract",
                aceptado=False,
                motivo=f"el OCR no esta disponible en esta maquina: {exc}",
            )
        )
        return
    except Exception as exc:  # noqa: BLE001
        salida.pasos.append(
            PasoLectura(
                escalon=escalon,
                motor="tesseract",
                aceptado=False,
                motivo=f"el OCR fallo: {exc}",
            )
        )
        return

    texto = (leido.texto or "").strip()
    if len(texto) < OCR_MIN_CHARS_PARA_INTENTAR:
        salida.pasos.append(
            PasoLectura(
                escalon=escalon,
                motor="tesseract",
                dio={"caracteres": len(texto)},
                aceptado=False,
                motivo=(
                    f"el OCR leyo {len(texto)} caracteres, menos de los "
                    f"{OCR_MIN_CHARS_PARA_INTENTAR} del piso. Suele ser foto "
                    "borrosa, rotada o con la tinta corrida: es cuando toca el "
                    "modelo de vision"
                ),
            )
        )
        return

    resultado = _parse_receipt_text(texto)
    paso = PasoLectura(
        escalon=escalon,
        motor="tesseract",
        dio={"caracteres": len(texto), **_resumen(resultado)},
        aceptado=bool(resultado.provider_name) and resultado.total_amount > 0,
        costo="gratis (local)",
    )
    if not paso.aceptado:
        paso.motivo = _por_que_no_sirvio(resultado)
    salida.pasos.append(paso)


# ---------------------------------------------------------------------------
# El veredicto del gate, sin escribir
# ---------------------------------------------------------------------------


def _anotar_veredicto(salida: ResultadoLectura, extraccion: TicketExtractionResult) -> None:
    """Que haria el gate con esta lectura.

    Se corre `gate_ticket` DE VERDAD, no una copia de sus reglas. Un diagnostico que
    dijera "pasaria el gate" con checks distintos a los del gate seria peor que no
    decir nada: es exactamente el fallo de "el sistema afirma mas de lo que sostiene".
    """
    decision = gate_ticket(
        provider_name=extraccion.provider_name,
        total_amount=extraccion.total_amount,
        tax_amount=extraccion.tax_amount,
        expense_date=extraccion.expense_date,
        provider_tax_id=extraccion.provider_tax_id,
        subtotal=extraccion.subtotal,
        ieps_amount=extraccion.ieps_amount,
        confidence=extraccion.confidence,
        source=extraccion.confidence_source,
    )
    salida.veredicto_status = decision.status
    salida.veredicto_confidence = decision.confidence
    salida.veredicto_confidence_persisted = decision.persisted_confidence
    salida.veredicto_confidence_source = decision.confidence_source
    salida.veredicto_reasons = list(decision.reasons)
    salida.checks_pasados = list(decision.validation.passed)
    salida.checks_fallidos = list(decision.validation.failures)


async def _contrastar(
    db: AsyncSession,
    salida: ResultadoLectura,
    extraccion: TicketExtractionResult,
    ticket_id: UUID,
) -> None:
    """Compara la lectura de ahora con lo que hay guardado.

    Es la pregunta que responde "esta bien leido y el problema es otro": si la
    lectura actual daría el mismo estado que el guardado, entonces releer no arregla
    nada y el problema esta en el gate o en los datos, no en el lector.
    """
    fila = (
        await db.execute(select(TicketModel).where(TicketModel.id == ticket_id))
    ).scalar_one_or_none()
    if fila is None:
        return

    salida.ticket_id = fila.id
    salida.ticket_status_actual = ExtractionStatus(fila.extraction_status)
    salida.ticket_source_actual = ConfidenceSource(fila.confidence_source)
    salida.ticket_leido_en = fila.created_at
    salida.coincide_con_guardado = salida.veredicto_status == salida.ticket_status_actual


# ---------------------------------------------------------------------------
# Los helpers
# ---------------------------------------------------------------------------


def _resumen(extraccion: TicketExtractionResult) -> dict:
    """Los campos que interestsan, sin `raw_text`.

    `raw_text` son hasta 20 000 caracteres y no es un dato: es la evidencia. Quien la
    necesite la pide con `GET /tickets/{id}`, que ademas la sirve con el documento al
    lado. Aqui va una **muestra** de los primeros caracteres, que es lo que hace falta
    para ver si el texto del OCR salio Leibniz o vino con la nota de un cupon.
    """
    return {
        "proveedor": extraccion.provider_name,
        "rfc": extraccion.provider_tax_id,
        "total": str(extraccion.total_amount),
        "subtotal": str(extraccion.subtotal) if extraccion.subtotal is not None else None,
        "iva": str(extraccion.tax_amount),
        "ieps": str(extraccion.ieps_amount) if extraccion.ieps_amount is not None else None,
        "fecha": str(extraccion.expense_date) if extraccion.expense_date else None,
        "lineas": len(extraccion.items) if extraccion.items is not None else None,
        "muestra_texto": (extraccion.raw_text or "")[:400],
    }


def _por_que_no_sirvio(extraccion: TicketExtractionResult) -> str:
    """El motivo legible de que `_es_extraccion_util` devolvera False.

    Se reimplementa la condicion en prosa, y **solo para explicarla**. La decision la
    toma `_es_extraccion_util`; si estas dos se separan, el diagnostico puede mentir
    sobre por que algo no sirvio. Por eso se cites la condicion exacta y no la copia
    con otra forma.
    """
    from app.core.enums import UNKNOWN_PROVIDER

    # `_es_extraccion_util` (capture.py:370): proveedor conocido y total positivo.
    if extraccion.provider_name == UNKNOWN_PROVIDER:
        return "no se encontro un proveedor (o el modelo devolvio un centinela de fallo)"
    if extraccion.total_amount <= 0:
        return "no se encontro un total, o es cero"
    # Llego aqui y `_es_extraccion_util` habria dicho que si. La unica otra salida
    # posible desde `_cascada_texto` es `_exige_items`.
    return "el encabezado esta bien pero faltan las lineas de producto"
