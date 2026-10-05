import uuid
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP, CheckConstraint, Column, Date, ForeignKey, Index, Numeric, String, Text, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.enums import OPEN_STATUSES, SETTLED_STATUSES, ExtractionStatus, SourceType
from app.core.json_decimal import JSONConDecimal
from app.core.time import utcnow

# Las condiciones se construyen desde el enum para que la constraint y el indice
# no puedan divergir cuando se agregue un estado nuevo.
_SIN_CERRAR = "extraction_status NOT IN ({})".format(
    ", ".join(f"'{s.value}'" for s in SETTLED_STATUSES)
)
_SIN_ABRIR = "extraction_status IN ({})".format(
    ", ".join(f"'{s.value}'" for s in OPEN_STATUSES)
)


class TicketModel(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        # Invariante a nivel de base, y es condicional a proposito.
        #
        # Lo que de verdad hay que proteger es la conciliacion: un ticket que
        # entra a conciliar no puede traer un total de 0. Pero un documento
        # ilegible SI tiene que poder guardarse como PENDIENTE, porque si no
        # se descarta en silencio y nadie se entera de que existio. Con un
        # CHECK incondicional, una foto de un papel quemado daba
        # IntegrityError -> 500 -> documento perdido. Un error invisible es
        # peor que un error visible.
        #
        # Lo mismo con RECHAZADO: si el constraint lo alcanzara, un documento
        # ilegible no se podria ni guardar en la cola ni descartar, y la cola
        # no se podria vaciar nunca.
        CheckConstraint(
            f"{_SIN_CERRAR} OR total_amount > 0",
            name="ck_tickets_total_positive_when_settled",
        ),
        CheckConstraint(
            f"{_SIN_CERRAR} OR tax_amount >= 0",
            name="ck_tickets_tax_non_negative_when_settled",
        ),
        CheckConstraint(
            f"{_SIN_CERRAR} OR tax_amount <= total_amount",
            name="ck_tickets_tax_lte_total_when_settled",
        ),
        # Constraints del muestreo (0003).
        #
        # Se replican aqui, y no solo en la migracion, por una razon concreta:
        # una constraint que existe unicamente en el DDL de Postgres no la
        # ejecuta la suite, porque los tests corren contra SQLite. Si el estado
        # valido de `spot_check_status` viviera solo en el SQL, la serie podria
        # pasar 300 tests y fallar al registrar la primera revision en
        # produccion. Estando en el modelo, un estado mal escrito revienta aqui.
        CheckConstraint(
            "spot_check_status IS NULL OR spot_check_status IN "
            "('PENDIENTE', 'CORRECTO', 'INCORRECTO')",
            name="ck_tickets_spot_check_values",
        ),
        # Un veredicto sin fecha no se puede envejecer, y la antiguedad es lo
        # que permite medir a que ritmo se acumula la evidencia.
        CheckConstraint(
            "spot_check_status IS NULL OR spot_check_status = 'PENDIENTE' "
            "OR spot_checked_at IS NOT NULL",
            name="ck_tickets_spot_check_verdict_has_date",
        ),
        # Solo se muestrea lo que fue automatico y nadie toco. Un ticket que
        # una persona aprobo no mide el automatismo.
        CheckConstraint(
            "spot_check_status IS NULL OR extraction_status = 'AUTO_APROBADO'",
            name="ck_tickets_spot_check_only_auto",
        ),
        # Cola de revision: indice parcial sobre los estados abiertos. El
        # indice solo contiene lo que esta en la cola, asi que no crece con el
        # historico y la consulta de la cola no tiene que ordenar sobre miles de
        # tickets ya cerrados.
        Index(
            "ix_tickets_review_queue",
            "extraction_status", "company_id",
            postgresql_where=text(_SIN_ABRIR),
        ),
        # Recepcion de lotes idempotente por hash de contenido.
        #
        # `company_id` va en el indice y no es decorativo. El hash es el
        # SHA-256 del archivo, y el mismo comprobante puede rightfulmente
        # existir en dos empresas: es el mismo papel, y cada una lo gasto por su
        # cuenta. Unico sobre `source_hash` solo, el sistema decidia que solo
        # una de las dos puede tenerlo, y el indice era el que resolvia a favor
        # de la primera. Ver db/migrations/0005_source_hash_por_empresa.sql.
        #
        # Sigue siendo unico solo cuando existe, por la misma razon de antes:
        # los tickets manuales no tienen hash y varios NULL en la misma columna
        # no pueden violar unicidad.
        Index("ix_tickets_source_hash", "company_id", "source_hash", unique=True,
              postgresql_where=text("source_hash IS NOT NULL")),
        # Cola de muestreo: parcial sobre PENDIENTE. A diferencia de la cola de
        # revision, esta no se vacia nunca: los tickets revisados se quedan para
        # siempre porque son la evidencia. Sin el indice parcial, la cola
        # ordenaria sobre todo el historico ya revisado.
        Index(
            "ix_tickets_spot_check_queue", "company_id", "created_at",
            postgresql_where=text("spot_check_status = 'PENDIENTE'"),
        ),
        # Sostiene la agregacion del reporte de exactitud, que cuenta por
        # confidence_source. Es indice parcial porque solo las filas con
        # veredicto participan, y esas son una fraccion del total.
        Index(
            "ix_tickets_spot_check_report", "company_id", "confidence_source",
            postgresql_where=text("spot_check_status IS NOT NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    provider_name = Column(String(150), nullable=False)
    provider_tax_id = Column(String(50), nullable=True)
    total_amount = Column(Numeric(12, 2), nullable=False)
    tax_amount = Column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    # Se usaba para validar `subtotal + IVA == total` y para la confianza, y no
    # se guardaba. Ver db/migrations/0003_spot_check.sql, seccion 1b.
    #
    # NULL y no cero: si el comprobante no trae subtotal, no se sabe el
    # subtotal. Un 0 aqui haria que la cuenta pareciera cuadrar cuando en
    # realidad no hay nada que comprobar.
    subtotal = Column(Numeric(12, 2), nullable=True)
    # El IEPS del comprobante. Es IMPORTE y no tasa, y es NULL cuando el papel no
    # lo imprime. Sin esta columna, un ticket con IVA + IEPS no puede cuadrar con
    # `subtotal + IVA == total` ni aunque los tres numeros esten bien leidos.
    # Ver db/migrations/0012_el_impuesto_es_de_la_partida.sql.
    ieps_amount = Column(Numeric(12, 2), nullable=True)
    # Las lineas del comprobante. El modelo las pedia desde antes de que
    # existiera el inventario (`ai_extractor.py`), devolvia la respuesta entera
    # con ellas, y `capture.py:invoice_to_result` las dejaba fuera del
    # `TicketExtractionResult`: el gasto se guardaba y el contenido se perdia.
    # Ver db/migrations/0010_inventario.sql.
    #
    # NULL y no lista vacia, y la diferencia importa: NULL = el lector no produjo
    # lineas (la ruta OCR no las extrae, solo el LLM). `[]` = produjo lineas y no
    # eran ninguna. Con las dos en `[]` no se puede distinguir "este comprobante
    # no tiene detalle" de "no lo sabemos", y la segunda es la que obliga a
    # revisar el papel.
    # `none_as_null=True` NO es cosmetico, y sin el esta columna MIENTE.
    #
    # SQLAlchemy guarda un `None` de Python como el literal JSON `null`, no como
    # SQL NULL, porque `none_as_null` viene en False por omision. Medido sobre la
    # base real: `items IS NULL` daba falso para 5 tickets que en realidad no
    # tienen lineas, y `jsonb_typeof(items)` daba `null`.
    #
    # Consecuencia: cualquier `WHERE items IS NULL` — que es exactamente lo que
    # la documentacion de esta columna promete— no devuelve nada. El codigo que
    # lee el atributo si funciona, porque `json 'null'` vuelve a Python `None` al
    # deserializar, asi que el fallo es silencioso: el comportamiento parece
    # correcto y la columna no cumple lo que dice.
    # `JSON(none_as_null=True)` y NO `Column(JSON, none_as_null=True)`: en el
    # constructor de `Column` el argumento se ignora con un SAWarning, y el
    # arreglo "aplica" sin aplicar. `none_as_null` es una opcion del tipo `JSON`,
    # y va ahi.
    # `JSONConDecimal` y no `JSON` pelado: las lineas del LLM traen `Decimal`
    # en cantidad, precio e importe (`ai_extractor.py:346-348`), y `Decimal` no
    # es JSON. Con `JSON` a secas, un INSERT de cualquier factura CON detalle de
    # partidas revienta con "Object of type Decimal is not JSON serializable" y el
    # ticket se pierde como `accion=ERROR`. Ver `app/core/json_decimal.py`.
    items = Column(JSONConDecimal(none_as_null=True), nullable=True)
    expense_date = Column(Date, nullable=False)
    category = Column(String(100), nullable=True)
    raw_text = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)

    # --- Trazabilidad de la extraccion (Fase 0) -------------------------
    # La confianza que la IA ya calculaba se descartaba. Ahora se persiste y
    # gobierna el flujo: es lo que permite medir exactitud real en vez de
    # suponerla.
    confidence = Column(Numeric(4, 3), nullable=True)
    confidence_source = Column(String(20), nullable=True)
    extraction_status = Column(
        String(20), nullable=False, default=ExtractionStatus.PENDIENTE.value,
    )
    source_type = Column(String(20), nullable=True, default=SourceType.MANUAL.value)
    source_file = Column(String(500), nullable=True)
    source_hash = Column(String(64), nullable=True)

    # Checks deterministas que fallo. Guardarlos permite ver donde se
    # concentra el error (OCR, vision, reglas) en vez de suponerlo.
    validation_errors = Column(Text, nullable=True)

    # --- Revision humana -----------------------------------------------
    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    review_notes = Column(Text, nullable=True)

    # --- Muestreo de exactitud (0003) -----------------------------------
    # La diferencia con lo de arriba: la revision humana corrige UN ticket. El
    # muestreo no corrige nada, produce evidencia sobre si el automatismo
    # funciona. Por eso no toca los datos del ticket: un muestreo mal hecho no
    # puede alterar un gasto, solo la medicion de como se leyeron los gastos.
    #
    # NULL = fuera de la muestra. Ver SpotCheckStatus en app/core/enums.py.
    spot_check_status = Column(String(20), nullable=True)
    spot_checked_at = Column(TIMESTAMP(timezone=True), nullable=True)
    spot_check_notes = Column(Text, nullable=True)
    # Que campos estaban mal, no solo que estaban mal. "96% correcto" no dice
    # que arreglar; "el 3% que falla es casi todo la fecha" si.
    spot_check_wrong_fields = Column(Text, nullable=True)
    # Quien registro el veredicto. Sale del token, no de un texto fijo: el
    # reporte de exactitud promedia veredictos de varias personas, y sin saber
    # cuales, "el sistema es 96% exacto" es un promedio sin dueño.
    #
    # Es texto y no llave foranea a proposito: una baja de cuenta es
    # `is_active = false`, pero si alguien termina borrando la cuenta, el
    # veredicto que firmo tiene que seguir diciendo quien fue. Con ON DELETE
    # SET NULL se perderia justo ese dato.
    spot_checked_by = Column(String(255), nullable=True)

    company = relationship("CompanyModel", backref="tickets")
    reconciliations = relationship("ReconciliationModel", back_populates="ticket")

    # LA CADENA DE DOCUMENTOS del ticket, en orden de version.
    #
    # Antes era `documento`, uno a uno, y `cascade="all, delete-orphan"` con
    # `single_parent=True`: el ORM manejaba el borrado. Ahora son varias filas por
    # ticket (ver `db/migrations/0009_documento_inmutable.sql`) y el borrado es de
    # la BASE, no del ORM.
    #
    # Por que `viewonly=True` y sin cascade, que es un cambio de fondo y no un
    # detalle: esta tabla es append-only y lo hace cumplir un trigger de Postgres
    # que prohibe `UPDATE` y `DELETE` mientras el ticket exista. Si el ORM
    # tuvieracascade, un `db.delete(ticket)` podria intentar borrar los
    # documentos por su cuenta Y dejar que la base los borrara otra vez, que es
    # justo el doble borrado que la regla quiere evitar. Con `viewonly` el ORM
    # no escribe nunca aqui: las inserciones las hace `document_service` y el
    # borrado, la base.
    documentos = relationship(
        "TicketDocumentModel",
        back_populates="ticket",
        order_by="TicketDocumentModel.version",
        lazy="selectin",
        viewonly=True,
    )

    # El documento VIGENTE, que es el que se descarga y el que compara el
    # muestreo. Vive como propiedad y no como relationship para poder filtrar por
    # `reemplaza_a IS NULL`: con un `uselist=False` sobre la coleccion entera, un
    # ticket con dos versiones lanzaria `MultipleResultsFound`, que es un error
    # de programacion donde deberia haber una respuesta.
    @property
    def documento(self):
        """El papel vigente, o `None` si este ticket se tecleo a mano.

        Vigente = el de mayor `version`. La relationship viene ordenada por
        `version` ascendente, asi que el ultimo es la punta de la cadena.
        """
        return self.documentos[-1] if self.documentos else None

    @property
    def is_open_for_review(self) -> bool:
        return ExtractionStatus(self.extraction_status).is_open

    @property
    def tiene_documento(self) -> bool:
        """Si el comprobante original esta guardado.

        Vive en el modelo y no se arma en cada schema porque hay dos
        consumidores que lo necesitan y los dos lo piden de forma distinta:
        la respuesta de la API (para pintar el enlace) y la fila de la cola (para
        decidir si mostrar el aviso de "no hay documento"). Una propiedad en el
        modelo tiene una sola definicion, y por lo tanto una sola verdad.
        """
        return self.documento is not None
