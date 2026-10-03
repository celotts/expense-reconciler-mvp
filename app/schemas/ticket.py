import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Reexportada: los schemas son la frontera publica y quien importa de aqui
# no necesita saber que el contrato vive en core.enums.
from app.core.enums import UNKNOWN_PROVIDER as UNKNOWN_PROVIDER

# RFC mexicano: 3 letras (moral) o 4 (fisica), 6 digitos (YYMMDD), 3 homoclave
RFC_REGEX = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")



class TicketBase(BaseModel):
    provider_name: str = Field(..., min_length=1, max_length=150)
    provider_tax_id: str | None = Field(None, max_length=50)
    total_amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    tax_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=12, decimal_places=2)
    expense_date: date
    category: str | None = Field(None, max_length=100)
    raw_text: str | None = None

    @field_validator("provider_name")
    @classmethod
    def _reject_unknown_provider(cls, v: str) -> str:
        """El parser emite "Unknown Provider" cuando no logra leer el emisor.
        Aceptarlo guardaria un ticket inservible para la conciliacion."""
        name = v.strip()
        if not name:
            raise ValueError("provider_name no puede estar vacio")
        if name == UNKNOWN_PROVIDER:
            raise ValueError(
                "La extraccion no identifico al proveedor. Corrigelo antes de guardar."
            )
        return name

    @field_validator("provider_tax_id")
    @classmethod
    def _validate_rfc(cls, v: str | None) -> str | None:
        """RFC mexicano: 3 letras (moral) o 4 (fisica), 6 digitos, 3 alfanumericos."""
        if v is None or not v.strip():
            return None
        rfc = v.strip().upper()
        if not RFC_REGEX.fullmatch(rfc):
            raise ValueError(
                f"RFC invalido: {rfc!r}. Formato esperado: "
                "3-4 letras, 6 digitos (YYMMDD) y 3 caracteres alfanumericos."
            )
        return rfc

    @model_validator(mode="after")
    def _tax_not_greater_than_total(self) -> "TicketBase":
        if self.tax_amount > self.total_amount:
            raise ValueError(
                f"tax_amount ({self.tax_amount}) no puede ser mayor "
                f"que total_amount ({self.total_amount})."
            )
        return self


class TicketCreate(TicketBase):
    company_id: UUID


class TicketUpdate(BaseModel):
    provider_name: str | None = Field(None, min_length=1, max_length=150)
    provider_tax_id: str | None = Field(None, max_length=50)
    total_amount: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)
    tax_amount: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    expense_date: date | None = None
    category: str | None = Field(None, max_length=100)
    raw_text: str | None = None

    @field_validator("provider_name")
    @classmethod
    def _reject_unknown_provider(cls, v: str | None) -> str | None:
        if v is None:
            return v
        name = v.strip()
        if not name:
            raise ValueError("provider_name no puede estar vacio")
        if name == UNKNOWN_PROVIDER:
            raise ValueError(
                "La extraccion no identifico al proveedor. Corrigelo antes de guardar."
            )
        return name

    @field_validator("provider_tax_id")
    @classmethod
    def _validate_rfc(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        rfc = v.strip().upper()
        if not RFC_REGEX.fullmatch(rfc):
            raise ValueError(
                f"RFC invalido: {rfc!r}. Formato esperado: "
                "3-4 letras, 6 digitos (YYMMDD) y 3 caracteres alfanumericos."
            )
        return rfc

    @model_validator(mode="after")
    def _tax_not_greater_than_total(self) -> "TicketUpdate":
        if (
            self.tax_amount is not None
            and self.total_amount is not None
            and self.tax_amount > self.total_amount
        ):
            raise ValueError(
                f"tax_amount ({self.tax_amount}) no puede ser mayor "
                f"que total_amount ({self.total_amount})."
            )
        return self


class ClasificarLoteRequest(BaseModel):
    """Clasificar varios tickets de golpe.

    Existe porque la alternativa no es mas comoda, es inviable: la barra gris del
    tablero puede ser de 200 tickets, y a uno por uno son 200 peticiones y 200
    esperas. Quien tiene esa barra abierta no esta eligiendo categoria por
    categoria, esta reconociendo que veinte comprobantes de la ferreteria son
    HERRAMIENTAS, y eso es una sola decision.

    Acepta cadena vacia para **desclasificar**. Un ticket mal clasificado es
    peor que uno sin clasificar: el primero se suma a un rubro que no es el suyo
    sin que nada lo señale, y sin esta via de salida el error de captura no se
    puede corregir nunca.
    """

    ticket_ids: list[UUID] = Field(..., min_length=1, max_length=500)
    category: str | None = Field(None, max_length=100)

    @field_validator("category")
    @classmethod
    def _limpia(cls, v: str | None) -> str | None:
        if v is None:
            return None
        limpio = v.strip()
        # Cadena vacia y solo espacios significan lo mismo: "no lo se". Se
        # guarda como NULL y no como '', para que el filtro `sin_categoria` del
        # listado los encuentre sin tener que buscar las dos formas.
        return limpio or None


class ClasificarLoteResponse(BaseModel):
    actualizados: int
    # cuantos se pedian y cuantos se tocaron. `pedidos` != `actualizados` no es un
    # error: un id que no existe simplemente no se cuenta, y en un lote de 200
    # que uno haya sido borrado entre la pantalla y el envio es lo normal. Lo que
    # no se hace es mentir diciendo "200" cuando se tocaron 198.
    pedidos: int


class TicketResponse(BaseModel):
    """Forma de LECTURA de un ticket.

    No hereda de TicketBase a proposito. Los validadores de TicketBase son de
    escritura: un ticket no puede CREARSE con total 0 ni con proveedor
    "Unknown Provider". Pero un ticket PENDIENTE existe precisamente porque
    tiene esos datos rotos: es la foto de un papel que la IA no pudo leer.

    Si esta clase heredara los validadores de entrada, la cola de revision
    reventaria con un 500 al intentar devolver justamente los tickets que
    existen para revision. El error sale de nuevo: un ticket ilegible es
    invisible, que es el unico resultado inaceptable.
    """

    id: UUID
    company_id: UUID
    provider_name: str
    provider_tax_id: str | None = None
    total_amount: Decimal
    tax_amount: Decimal
    # Va en la respuesta, no solo en la tabla, por una razon util: sin el, el
    # muestreo pregunta si el subtotal se leyo bien y no hay con que comparar.
    # El revisor tendria que buscarlo dentro de `raw_text`, que en la ruta de
    # vision es lo que devolvio el modelo y no siempre lo trae.
    subtotal: Decimal | None = None
    expense_date: date
    category: str | None = None
    raw_text: str | None = None
    # La columna admite NULL, asi que la respuesta lo admite. Declararla
    # obligatoria convertia una fila con created_at nulo en un 500 al serializar,
    # y la fila con created_at nulo es justamente la que uno no quiere perder:
    # sin fecha de creacion no se puede calcular la antiguedad, y eso lo
    # vuelve mas importante verla, no menos.
    created_at: datetime | None = None
    confidence: Decimal | None = None
    confidence_source: str | None = None
    extraction_status: str
    source_type: str | None = None
    source_file: str | None = None
    # Si el comprobante original esta guardado, y cuanto pesa.
    #
    # Sin esto, la UI no puede distinguir tres cosas que se ven igual en una
    # tabla: un ticket de captura manual (que no tiene documento y no deberia),
    # un ticket de una captura antigua (que deberia tenerlo y se puede volver a
    # subir), y uno al que se le pudo archivar mal. Con el booleano, la pantalla
    # puede decir "no hay documento" sin que el revisor crea que el sistema
    # fallo, que es la lectura que hace que se dejen de confiar en la cola.
    #
    # `documento_url` va relativo a proposito, sin el host: el front y la API se
    # sirven en el mismo origen en desarrollo y pueden no estarlo en produccion.
    # Un host hardcodeado aqui seria una URL que funciona hoy y se rompe el dia
    # que se pongan detras de un proxy.
    tiene_documento: bool = False
    documento_url: str | None = None
    documento_tamano: int | None = None
    validation_errors: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_notes: str | None = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _derivar_documento(cls, datos):
        """Lee la relacion del ticket y arma los tres campos del documento.

        Va en `before` y no en `after` por una razon mecanica: la relacion
        `documento` del modelo NO es un campo de este schema, asi que en
        `after` ya no esta disponible - se leyo del ORM pero no quedo en el
        schema. En `before` todavia esta, porque `datos` es el objeto del ORM
        o el diccionario que se esta validando.

        Se deriva en el schema y no en cada endpoint porque la regla es una sola
        y repetirla en la cola, en el muestreo, en el listado y en el detalle son
        cuatro oportunidades de que una se olvide. Una pantalla que no pone el
        enlace cuando el documento existe es una pantalla donde el revisor
        revisa a ciegas, y no lo va a delatar ninguna prueba de la API: la API
        responde bien.

        Y la URL va con el id en la ruta y sin query params para que un
        `<img src>` o un `window.open` funcionen sin Javascript: la imagen del
        comprobante tiene que poder abrirse con un clic y nada mas.
        """
        documento = (
            datos.get("documento") if isinstance(datos, dict)
            else getattr(datos, "documento", None)
        )
        if documento is None:
            return datos

        # Se copian los campos DECLARADOS de este schema, no los de `datos`.
        # `datos` es un TicketModel (una clase de SQLAlchemy, sin `model_fields`)
        # o un diccionario, y usar las columnas de cualquiera de los dos meteria
        # en la respuesta cosas que no estan declaradas aqui, que es como un
        # `from_attributes` empieza a filtrar columnas por accidente.
        #
        # Y se copia en vez de sustituir por un dict nuevo con los tres campos,
        # porque el `id` viene de ahi: si se construyera un dict con solo
        # `tiene_documento` y `documento_tamano`, la URL saldria vacia y el
        # enlace no existiria. Falla en silencio, que es la peor forma.
        if isinstance(datos, dict):
            enriched = dict(datos)
        else:
            enriched = {
                campo: getattr(datos, campo, None)
                for campo in cls.model_fields  # type: ignore[attr-defined]
            }

        # La URL se arma aqui y no en un validador `after` por una razon concreta:
        # en `before` el `id` todavia esta a mano, y en `after` habria que
        # reasignar un campo ya construido, que Pydantic v2 no hace sin
        # `object.__setattr__`. Con eso, el `documento_url` se queda en None sin
        # avisar: el schema valida, la respuesta sale con el campo en blanco y
        # el enlace no existe. Es el fallo mas dificil de ver de los tres, y el
        # unico que necesita un test que mire el campo, no el endpoint.
        #
        # El prefijo `/api/v1` va aqui, no sale de `settings`: el schema no
        # deberia importar la configuracion para armar una URL, y si el prefijo
        # cambiara, la respuesta y el router tendrian que cambiar juntos, que es
        # justo lo que hace este string.
        enriched["tiene_documento"] = True
        enriched["documento_tamano"] = documento.tamano
        if enriched.get("id") is not None:
            enriched["documento_url"] = (
                f"/api/v1/tickets/{enriched['id']}/documento"
            )
        return enriched


class DocumentoHistorialResponse(BaseModel):
    """UNA version del comprobante, sin los bytes.

    El historial responde "¿que papel guardamos y como llegamos a el?". Los bytes
    NO van aqui: se descargan del vigente, y meterlos en un listado de historial
    seria una forma de bajar 10 MB sin querer.

    `vigente` es lo que hace util la lista sin tener que deducirlo de las fechas:
    la version vigente es la que no fue reemplazada, y es la unica que se
    descarga y la unica contra la que se contrasta una lectura.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    sha256: str | None = None
    tamano: int
    content_type: str | None = None
    nombre_archivo: str | None = None
    actor: str | None = None
    motivo: str | None = None
    created_at: datetime
    vigente: bool


class TicketReviewRequest(BaseModel):
    """Accion de revision humana sobre un ticket en cola.

    Aprobar exige datos validos. Es la contraparte del gate: si el humano
    confirma, los checks se vuelven a correr para que no entre a conciliacion
    algo que el gate habia bloqueado.
    """

    action: Literal["approve", "reject", "request_info"]
    notes: str | None = Field(None, max_length=2000)
    # Correcciones aplicadas durante la revision (opcional).
    provider_name: str | None = Field(None, min_length=1, max_length=150)
    provider_tax_id: str | None = Field(None, max_length=50)
    total_amount: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)
    tax_amount: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    expense_date: date | None = None
    category: str | None = Field(None, max_length=100)


class TicketReviewQueueResponse(BaseModel):
    """Cola de revision agrupada por estado, para la pantalla de pendientes."""

    company_id: UUID | None = None
    total_open: int
    por_estado: dict[str, int]
    antiguedad_promedio_dias: float | None = None
    tickets: list[TicketResponse]

# Campos que, si estan mal, hacen que un ticket este mal leido.
#
# No se agrega `category` a proposito: nada de la conciliacion depende de el, y
# un ticket con la categoria equivocada entra a conciliar igual. Meterlo en la
# cuenta haria que "acierto" significara algo mas estricto de lo que el sistema
# promete, y una metrica mas estricta que la promesa es una metrica que nunca
# pasa.
CAMPOS_VERIFICABLES = (
    "provider_name",
    "provider_tax_id",
    "total_amount",
    "tax_amount",
    "expense_date",
    "subtotal",
)


class SpotCheckRequest(BaseModel):
    """Veredicto de una revision de muestreo.

    A diferencia de la revision humana, esto NO corrige el ticket. Solo dice si
    la extraccion coincidia con el papel, y que campos no coincidieron. Que el
    muestreo no pueda alterar un gasto es deliberado: si una muestra mal
    hecha cambiara el total de un comprobante, la exactitud medida dependeria
    de quien reviso, y la metrica dejaria de medir el automatismo.
    """

    # La lista de campos la valida el servicio, no el schema, porque tiene que
    # ser la MISMA lista que usa el reporte para contar. Si el schema aceptara
    # cualquier texto, alguien podria anotar "fecha y total" y el reporte
    # contaria un campo que no existe en la cuenta.
    correct: bool
    campos_incorrectos: list[Literal[
        "provider_name", "provider_tax_id", "total_amount",
        "tax_amount", "expense_date", "subtotal",
    ]] = Field(default_factory=list)
    notes: str | None = Field(None, max_length=2000)


class SpotCheckItemResponse(BaseModel):
    """Un ticket esperando veredicto, o ya verificado."""

    ticket: TicketResponse
    spot_check_status: str
    spot_checked_at: datetime | None = None
    spot_check_notes: str | None = None
    spot_check_wrong_fields: list[str] = Field(default_factory=list)


class SpotCheckQueueResponse(BaseModel):
    """Muestra a revisar, y lo que ya se verifico.

    `total_pendientes` va aparte de la lista porque la lista se limita, y el
    numero de lo que falta no. Sin el, una cola de 200 con `limit=50` se ve
    como si quedaran 50.
    """

    company_id: UUID | None = None
    total_pendientes: int
    total_revisados: int
    aciertos: int
    incorrectos: int
    antiguedad_promedio_dias: float | None = None
    tickets: list[SpotCheckItemResponse]


class ExactitudPorOrigenResponse(BaseModel):
    """Como leyo el sistema cada via de captura, con lo que la muestra sostiene.

    El intervalo va SIEMPRE, y `revisados` va siempre. Un porcentaje sin las
    dos cosas al lado no significa nada: 96% de 25 y 96% de 5000 son el mismo
    numero con consecuencias opuestas.
    """

    origen: str
    revisados: int
    aciertos: int
    incorrectos: int
    pendientes: int
    # None cuando no hay evidencia. La ausencia de un numero NO es un cero:
    # significa que nadie miró, no que se falló todo.
    exactitud: float | None = None
    intervalo_inferior: float | None = None
    intervalo_superior: float | None = None
    veredicto: str
    # Por que no se puede afirmar aun, o que falta para poder hacerlo. Viene
    # como texto porque la accion depende de la razon: faltante se resuelve
    # revisando, por debajo del objetivo se resuelve arreglando el extractor.
    motivo_faltante: str
    total_revisiones_necesarias: int | None = None
    campo_mas_fallido: str | None = None
    conteo_por_campo: dict[str, int] = Field(default_factory=dict)


class ReporteExactitudResponse(BaseModel):
    """El numero que respalda el objetivo, con lo que le falta para sostenerse.

    `veredicto_global` es el PEOR de los origenes, no el promedio. Un sistema
    que falla en una via de captura no cumple el objetivo aunque la otra sea
    perfecta, y promediar las dos esconderia justamente la que hay que arreglar.
    """

    company_id: UUID | None = None
    objetivo: float
    nivel_confianza: float
    veredicto_global: str
    # El detalle de por que, en texto. Un veredicto sin explicacion obliga a
    # quien lo lee a buscar en otro lado de donde saiu.
    explicacion: str
    por_origen: list[ExactitudPorOrigenResponse]
