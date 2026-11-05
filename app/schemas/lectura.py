"""Lo que sale de un diagnostico de lectura.

POR QUE ESTOS SCHEMAS Y NO UN `TicketExtractionResult` SUELTO
============================================================

Porque la respuesta tiene que decir **las dos cosas**: que se leyo, y por que se
leyo asi. Devolver solo el resultado es lo que ya hacia `POST /tickets/extract`, y no
alcanza: si el ticket cae a revision y la respuesta dice "no se pudo leer el
proveedor", no hay forma de saber si fue que el OCR no vio texto, que el parser no
encontro el folio, o que el modelo estaba apagado. Y esas tres piden arreglos
distintos.

`datos` y `veredicto` van en el MISMO objeto y no en campos sueltos, por la misma
razon que `POST /scan`: si alguien lee el JSON buscando "el total" y lo encuentra al
lado de `extraction_status: PENDIENTE`, tiene el dato Y el veredicto a la vista. Un
JSON con el numero sin el veredicto deja a quien lo consume creyendo que el sistema
respondio por el, que es justo lo que el gate se niega a afirmar.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ConfidenceSource, ExtractionStatus


class PasoLecturaResponse(BaseModel):
    """Un escalon de la cascada, y que hizo.

    `motivo` y `aceptado` van juntos: un paso que no dio resultado tiene que decir
    POR QUE. "No" sin motivo es el mismo "no se pudo leer" que este endpoint vino a
    distinguir.
    """

    escalon: str
    motor: str
    #: Lo que devolvio, resumido. `null` si no llego a producir nada.
    dio: dict | None = None
    aceptado: bool = False
    #: Por que se dejo de seguir. `null` cuando el escalon gano.
    motivo: str | None = None
    #: Si este paso costo dinero. `gratis` en los locales.
    costo: str | None = None


class LecturaCamposResponse(BaseModel):
    """Los campos que el lector afirmo que dice el papel.

    NO es `TicketResponse` y no es `TicketExtractionResult`, por dos razones:

      - `raw_text` no se expone. Son hasta 20 000 caracteres y no es un dato: es la
        evidencia. Va una **muestra** de 400, que es lo que hace falta para ver si el
        OCR salio con `"REGINA DE 12 PULG"` o con la nota de un cupon. Quierela
        entera, con `GET /tickets/{id}`.
      - `items` sale como `list[dict]` crudo y no como un modelo de linea tipado: las
        lineas se persisten sin normalizar, e inventar `descripcion`/`cantidad`
        afirmaria una estructura que el sistema nunca verifico — con OCR al 33.3%
        esa afirmacion seria falsa seguido. Lo que normaliza es
        `inventario_service.interpretar_items`.
    """

    proveedor: str
    rfc: str | None = None
    total: Decimal
    subtotal: Decimal | None = None
    iva: Decimal
    ieps: Decimal | None = None
    fecha: date | None = None
    categoria: str | None = None
    confianza: float | None = None
    origen: ConfidenceSource
    #: `null` = el lector no produjo lineas (la ruta OCR no las extrae). `[]` = produjo
    #: lineas y no eran ninguna. La diferencia obliga a mirar el papel.
    lineas: list[dict] | None = None
    muestra_texto: str = Field(default="", max_length=400)


class VeredictoLecturaResponse(BaseModel):
    """Lo que haria el gate con esta lectura, SIN escribirla.

    `status` es lo que el ticket habria quedado. Los checks van por separado porque
    "cayo a revision" no dice nada accionable, y `subtotal_plus_tax_mismatch` si:
    apunta a una aritmetica, que es un dato del papel, no de la maquina.
    """

    status: ExtractionStatus
    confidence: float
    #: Tal como se guardaria en la columna `Numeric(4,3)`. `null` para captura manual,
    #: que no tiene confianza que medir.
    confidence_persisted: Decimal | None = None
    confidence_source: ConfidenceSource
    #: `check:<nombre>` y `confidence_high(...)`. Es la lista que el gate ya arma.
    reasons: list[str] = Field(default_factory=list)
    checks_pasados: list[str] = Field(default_factory=list)
    checks_fallidos: list[str] = Field(default_factory=list)


class DiagnosticoLecturaResponse(BaseModel):
    """Por que se leyo asi un comprobante. No guardo nada.

    `guardado` esta siempre en `false` y existe para que el que consume el JSON no
    tenga que suponer. Un endpoint que "a veces guarda" es un endpoint del que nadie
    sabe que hace; este no guarda nunca, y lo dice en el campo y en el docstring.
    Para guardar, `POST /tickets/extract-and-create` o `POST /scan/files/{id}/
    reprocess`, que son explicitos.
    """

    formato_detectado: str = Field(
        description="El formato deducido de los BYTES, no del `file_type` del cliente."
    )
    formato_declarado: str | None = None
    #: El motivo por el que se corrigio la etiqueta del cliente, si se corrigio. Un PDF
    #: subido como `image` entra por vision y sale con otra confianza; sin este
    #: campo, el cliente se lleva un resultado distinto del que esperaba sin saberlo.
    formato_corregido: str | None = None

    #: Cada escalon que se intento, en orden. Es lo que hace util el endpoint: no dice
    #: "fallo", dice "el OCR no vio texto, el modelo no estaba disponible".
    pasos: list[PasoLecturaResponse] = Field(default_factory=list)

    #: `null` si el archivo ni se pudo abrir. Es un error de verdad, no una lectura
    #: mala, y por eso se distingue de `datos` con campos vacios.
    datos: LecturaCamposResponse | None = None
    veredicto: VeredictoLecturaResponse | None = None

    #: Por que no se pudo leer, cuando no se pudo leer. Un fallo del MODELO y uno de
    #: la MAQUINA piden cosas opuestas: apagar y encender el extractor, o revisar la
    #: foto. Decir "no se pudo leer" para los dos es lo que obliga a ir al log.
    error: str | None = None

    # --- El contraste con lo que ya esta guardado -------------------------
    #: El ticket contra el que se comparo, si la peticion trajo `ticket_id`.
    ticket_id: UUID | None = None
    ticket_status_actual: ExtractionStatus | None = None
    ticket_source_actual: ConfidenceSource | None = None
    ticket_leido_en: datetime | None = None
    #: `true` cuando releer daria el mismo estado. Es la respuesta a "esta bien leido y
    #: el problema es otro": si releer no cambia nada, el problema no es el lector.
    coincide_con_guardado: bool | None = None

    #: Siempre `false`. Ver la nota del docstring de la clase.
    guardado: bool = False
