"""El catalogo de productos, y el kardex que dice cuanto hay de cada uno.

Por que el stock NO es una columna de esta tabla
------------------------------------------------

Porque seria una copia. El stock es la SUMA de `movimientos_inventario`, y una
copia se desincroniza siempre: el dia que un movimiento se escriba y la copia no,
los dos numeros dicen cosas distintas y no hay forma de saber cual es el bueno.
La suma no se puede desincronizar porque no hay nada que sincronizar — es la
definicion.

Ver `db/migrations/0010_inventario.sql`, que tiene el argumento largo y los
motivos medidos (exactitud de OCR del 33.3%, la conciliacion bancaria validando el
monto y nunca la composicion).

`codigo` y por que el indice unico lleva company_id
---------------------------------------------------

El codigo de barras es del mundo, no lleva empresa dentro: dos empresas que
compran el mismo producto dan el mismo codigo. Con unicidad solo por codigo, la
segunda empresa no lo puede dar de alta. Mismo criterio que
`ix_tickets_source_hash` y el mismo archivo que lo explica:
`db/migrations/0005_source_hash_por_empresa.sql`.

Y es un indice PARCIAL (`WHERE codigo IS NOT NULL`) porque muchos comprobantes de
autocomercion no imprimen codigo, y varios NULL en la misma columna no pueden
violar unicidad.

Por que no hay columna `items` en `CompraItemModel` pero si `descripcion`
-------------------------------------------------------------------------

La descripcion se guarda SIEMPRE, incluso cuando si hubo coincidencia de
producto. Es la evidencia de lo que decia el papel, y sin ella no hay forma de
auditar por que se eligio ese producto del catalogo. El caso real que lo
justifica: el OCR devolvio "Cadena Comercial) Uxxo," como proveedor, y si las
lineas se guardaran solo cuando coinciden con un producto, esa basura se
perdia y habria que reescanear el papel para recuperarla.
"""

import uuid
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, validates

from app.core.database import Base
from app.core.time import utcnow


class ProductoModel(Base):
    """Un producto del catalogo de una empresa. Ver el modulo."""

    @validates("nombre")
    def _llena_normalizado(self, _clave, valor: str) -> str:
        """Mantiene `nombre_normalizado` sin que nadie se acuerde de hacerlo.

        Ver la nota de la columna. Importar aqui y no arriba del archivo es a
        proposito: `inventario_service` importa este modulo, y al reves serian un
        ciclo.
        """
        from app.services.inventario_service import normalizar_descripcion

        self.nombre_normalizado = normalizar_descripcion(valor)[:200]
        return valor

    __tablename__ = "productos"
    __table_args__ = (
        # Vacio es peor que NULL: un codigo de "" no identifica nada y
        # colisionaria con otro "".
        CheckConstraint(
            "codigo IS NULL OR length(trim(codigo)) > 0",
            name="ck_productos_codigo_no_vacio",
        ),
        CheckConstraint(
            "length(trim(nombre)) > 0", name="ck_productos_nombre_no_vacio"
        ),
        # Un precio negativo es un descuento, y un descuento se registra en la
        # compra, no como el precio del producto.
        CheckConstraint(
            "precio_referencia IS NULL OR precio_referencia >= 0",
            name="ck_productos_precio_no_negativo",
        ),
        CheckConstraint(
            "origen IN ('MANUAL', 'OCR')", name="ck_productos_origen"
        ),
        # Un producto de OCR sin codigo no se puede dar por verificado solo: no
        # hay identidad, hay una descripcion que el sistema creyo. Con codigo de
        # barras, si: un codigo es una identidad.
        CheckConstraint(
            "verificado = true OR origen = 'OCR'", name="ck_productos_verificado_ocr"
        ),
        # La cola de revision de productos que salio de un papel.
        Index("ix_productos_sin_verificar", "company_id", "created_at",
              postgresql_where=text("verificado = false")),
        # `postgresql_where=text(...)` y no tambien `sqlite_where`, por la misma
        # razon que en `ticket.py:100` y `ticket_document.py:194`: SQLite no
        # soporta indices parciales y el dialecto se niega a compilar el
        # `sqlite_where` como si fuera una cadena. La constraint
        # `ck_productos_codigo_no_vacio` sigue aplicando en los dos motores; lo
        # que SQLite no tiene es el indice, que es solo una optimizacion.
        Index("ix_productos_codigo", "company_id", "codigo", unique=True,
              postgresql_where=text("codigo IS NOT NULL")),
        Index("ix_productos_empresa", "company_id", "activo"),
        # Buscar por nombre es como se resuelve la cola de lineas sin producto,
        # y sin indice es un seq scan por linea.
        Index("ix_productos_nombre", "company_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    codigo = Column(String(64), nullable=True)
    nombre = Column(String(200), nullable=False)
    # El nombre tal como se COMPARA, no tal como se muestra.
    #
    # Existe porque comparar con `lower(nombre)` no basta: `lower('Café molido')`
    # es 'café molido', con tilde, y el objetivo normalizado es 'cafe molido',
    # sin ella. La comparacion daria distinta y "Café molido" y "Cafe molido"
    # crearian dos productos — que es exactamente el problema que
    # `normalizar_descripcion` existe para evitar, y fallaria en el primer
    # producto con tilde. Casi todos.
    #
    # Se llena con un `@validates` y no a mano en cada INSERT: una columna
    # "normalizada" que hay que acordarse de mantener es una columna que un dia
    # no se mantiene, y el fallo es silencioso — dos productos en vez de uno, sin
    # error en ninguna parte.
    nombre_normalizado = Column(String(200), nullable=True, index=True)
    # Quien lo puso: 'MANUAL' si fue una persona, 'OCR' si salio de una linea de
    # comprobante leida. Ver `ProductoOrigen`.
    origen = Column(String(20), nullable=False, default="MANUAL")
    # `false` = nadie ha confirmado que este texto sea un producto y no una
    # variante de otro. NO impide que entre al inventario: lo que hace es que
    # quede en una cola de revision, que es un problema distinto y resoluble.
    # Ver db/migrations/0011_productos_origen.sql para por que las dos salidas
    # (no-crear / crear-sin-marca) son peores que esta.
    verificado = Column(Boolean, nullable=False, default=True)
    # "PZA", "KG", "LT", "CAJA". Se guarda porque 3 piezas y 3 kilos son
    # inventarios distintos, y sin la unidad el kardex no puede comparar dos
    # movimientos del mismo producto.
    unidad_medida = Column(String(20), nullable=False, default="PZA")
    # Referencia, no el costo real: el costo lo determina cada compra, en
    # `compra_items.costo_unitario`.
    precio_referencia = Column(Numeric(12, 2), nullable=True)
    # Dar de baja sin borrar: el historial del kardex lo necesita vivo.
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    # `passive_deletes=True` NO es cosmetico, y sin el el borrado de una empresa
    # esta ROTO. Medido, no supuesto:
    #
    # El `ON DELETE CASCADE` de la FK dice que Postgres borre los productos al
    # borrar la empresa. Pero SQLAlchemy no se lo deja: su cascada por omision en
    # uno-a-muchos es `save-update, merge`, que NO borra — desasocia, y eso es un
    # `UPDATE productos SET company_id = NULL`. La columna es NOT NULL, asi que
    # el UPDATE revienta con NotNullViolation ANTES de que la cascada de la base
    # llegue a correr, y `DELETE /companies/{id}` devuelve 500.
    #
    # La version corta: `passive_deletes=True` le dice al ORM "no toques a
    # los hijos, ya lo hace la base". Es lo correcto cuando la FK tiene ondelete,
    # y es lo que evita que el ORM intente hacer en Python lo que Postgres hace
    # mejor y en el orden correcto.
    company = relationship("CompanyModel", backref="productos", passive_deletes=True)

    # NO hay propiedad `stock` aqui, y es deliberado.
    #
    # Una propiedad que promete un numero sin traer `movimientos_inventario` solo
    # puede dar un numero inventado, y con `from_attributes` de Pydantic una
    # propiedad que ademas lance rompe la validacion del schema entero. Por eso no
    # esta: el stock se pide con `inventario_service.stock_de(db, company_id)`, que
    # si trae la tabla.


class CompraModel(Base):
    """La orden de compra: un comprobante que va camino al inventario.

    `estado` NO es `tickets.extraction_status`. Ver el docstring de
    `EstadoCompra` en `app/core/enums.py`: son dos maquinas que contestan
    preguntas distintas, y esta responde "el inventario ya conto esto".
    """

    __tablename__ = "compras"
    __table_args__ = (
        CheckConstraint(
            "estado IN ('PROCESAR', 'EN_REVISION', 'PROCESADO')",
            name="ck_compras_estado",
        ),
        # La regla que sostiene el diseno: PROCESADO exige firma y fecha, y
        # ningun otro estado las tiene. Sin esto se podria marcar una compra
        # como PROCESSED sin que nadie hubiera pasado.
        CheckConstraint(
            "(estado = 'PROCESADO' AND confirmada_por IS NOT NULL "
            " AND length(trim(confirmada_por)) > 0 AND confirmada_at IS NOT NULL) "
            "OR "
            "(estado <> 'PROCESADO' AND confirmada_por IS NULL "
            " AND confirmada_at IS NULL)",
            name="ck_compras_confirmacion",
        ),
        CheckConstraint("total >= 0", name="ck_compras_total_no_negativo"),
        Index("ix_compras_empresa_estado", "company_id", "estado"),
        Index("ix_compras_fecha", "fecha"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    # UNIQUE en la columna, no un indice suelto: es el candado que impide
    # contar dos veces el mismo comprobante.
    ticket_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    estado = Column(String(20), nullable=False, default="PROCESAR")
    # Copia del comprobante en el momento de la compra. Redundante con
    # `tickets` a proposito: una compra se lee y se concilia durante meses, y el
    # ticket pudo recibir correcciones. Lo que se facturas es lo que estaba en el
    # papel el dia que se aprobo.
    fecha = Column(Date, nullable=False)
    proveedor_nombre = Column(String(150), nullable=True)
    total = Column(Numeric(12, 2), nullable=False)
    # Texto y no FK, por el precedente de `tickets.spot_checked_by` y
    # `cierres_periodo.cerrado_por`: la firma tiene que sobrevivir a la baja de la
    # cuenta.
    confirmada_por = Column(String(255), nullable=True)
    confirmada_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    company = relationship("CompanyModel", backref="compras", passive_deletes=True)
    ticket = relationship("TicketModel", backref="compra")
    # `passive_deletes=True` en TODA la cadena, no solo en la relacion directa
    # con `companies`. Ver la nota larga de `ProductoModel.company`: sin esto en
    # `items`, el ORM carga las lineas para borrarlas en Python, y al cargarlas
    # `CompraItemModel.producto` (que es `selectin`) arrastra los productos al
    # session. Ya cargados, `passive_deletes` en `ProductoModel.company` deja de
    # servir y el ORM emite `UPDATE productos SET company_id = NULL` — que revienta
    # con NotNullViolation antes de que la cascada de Postgres corra.
    #
    # La regla: si la FK tiene `ondelete`, el que borra es Postgres. El ORM solo
    # tiene que dejar de meterse.
    items = relationship(
        "CompraItemModel",
        back_populates="compra",
        cascade="all, delete-orphan",
        passive_deletes=True,
        # El orden del papel. Sin esto el indice sale en el orden de la base, que
        # no es el orden en que se compro, y una diferencia de dos lineas no se
        # puede comparar con el papel.
        order_by="CompraItemModel.orden",
        lazy="selectin",
    )


class CompraItemModel(Base):
    """Una linea del comprobante.

    `producto_id` admite NULL y eso es la cola de trabajo: una linea sin producto
    es "esta descripcion no la conozco". Se consulta con
    `WHERE producto_id IS NULL`, sin una tabla aparte que se pueda
    desincronizar de esta.
    """

    __tablename__ = "compra_items"
    __table_args__ = (
        CheckConstraint(
            "length(trim(descripcion)) > 0",
            name="ck_compra_items_descripcion_no_vacia",
        ),
        # Positiva SIEMPRE. Una cantidad negativa es una devolucion, y una
        # devolucion es una SALIDA: mezclarlas deja dos formas de restar y el
        # kardex deja de ser un SUM simple.
        CheckConstraint("cantidad > 0", name="ck_compra_items_cantidad_positiva"),
        CheckConstraint(
            "(costo_unitario IS NULL OR costo_unitario >= 0) "
            "AND (total IS NULL OR total >= 0)",
            name="ck_compra_items_costos_no_negativos",
        ),
        # Si hay producto asignado, tiene que haber alguien y una fecha.
        CheckConstraint(
            "(producto_id IS NULL) "
            "OR (producto_asignado_por IS NOT NULL "
            "    AND length(trim(producto_asignado_por)) > 0 "
            "    AND producto_asignado_at IS NOT NULL)",
            name="ck_compra_items_asignacion",
        ),
        Index("ix_compra_items_compra", "compra_id"),
        # Cola: parcial, porque lo unico que se consulta en caliente es
        # "que lineas no tienen producto". Sobre toda la tabla seria del tamano
        # del historico entero de compras.
        Index("ix_compra_items_sin_producto", "compra_id", "orden",
              postgresql_where=text("producto_id IS NULL")),
        Index("ix_compra_items_producto", "producto_id",
              postgresql_where=text("producto_id IS NOT NULL")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    compra_id = Column(
        UUID(as_uuid=True),
        ForeignKey("compras.id", ondelete="CASCADE"),
        nullable=False,
    )
    producto_id = Column(
        UUID(as_uuid=True),
        ForeignKey("productos.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Tal como salio del comprobante, sin normalizar. Ver el modulo.
    descripcion = Column(String(300), nullable=False)
    cantidad = Column(Numeric(14, 3), nullable=False)
    costo_unitario = Column(Numeric(12, 2), nullable=True)
    # El impuesto POR LINEA. Es aqui donde vive la tasa fiscal: un mismo
    # comprobante puede tener partidas a 0%, a 16% y con IEPS, y un unico
    # `tickets.tax_amount` no puede representar eso. Ver migracion 0012.
    #
    # IMPORTE y no tasa: `iva_linea = 8.14` son 8.14 pesos de IVA sobre esta
    # linea, no un 8.14%. Confundirlo es la forma mas rapida de que el total
    # deje de cuadrar sin que nadie sepa por que.
    iva_linea = Column(Numeric(12, 2), nullable=True)
    ieps_linea = Column(Numeric(12, 2), nullable=True)
    total = Column(Numeric(12, 2), nullable=True)
    # La posicion en el papel. Sin esto no se puede cotejar la linea con la
    # linea, que es la operacion completa cuando hay una diferencia.
    orden = Column(Integer, nullable=False, default=0)
    producto_asignado_por = Column(String(255), nullable=True)
    producto_asignado_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    compra = relationship("CompraModel", back_populates="items", passive_deletes=True)
    producto = relationship("ProductoModel", lazy="selectin", passive_deletes=True)


class MovimientoInventarioModel(Base):
    """Un movimiento del kardex. El stock es la suma de esta tabla.

    Append-only, y eso no es una convencion del codigo sino un trigger de
    Postgres (`trg_movimientos_no_reescribir`): UPDATE nunca, DELETE nunca
    mientras el producto exista. Corregir un movimiento es agregar un AJUSTE.

    `cantidad` es positiva siempre; el signo lo da `tipo`. Asi el stock es un
    `SUM` y no un `SUM` con un `CASE` que alguien tiene que acordarse de escribir.
    """

    __tablename__ = "movimientos_inventario"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('ENTRADA', 'SALIDA', 'AJUSTE')", name="ck_movimientos_tipo"
        ),
        CheckConstraint(
            "referencia_tipo IN ('COMPRA', 'VENTA', 'AJUSTE')",
            name="ck_movimientos_referencia_tipo",
        ),
        CheckConstraint("cantidad > 0", name="ck_movimientos_cantidad_positiva"),
        CheckConstraint(
            "actor IS NOT NULL AND length(trim(actor)) > 0",
            name="ck_movimientos_actor",
        ),
        # La consulta de stock. El indice mas importante de la migracion: sin el,
        # cada lectura de inventario recorre el kardex completo.
        Index("ix_movimientos_producto", "company_id", "producto_id", "created_at"),
        # Para "que movio este producto este dia", que es la pregunta que
        # aparece cuando el conteo fisico no cuadra.
        Index("ix_movimientos_referencia", "referencia_tipo", "referencia_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    producto_id = Column(
        UUID(as_uuid=True),
        ForeignKey("productos.id", ondelete="CASCADE"),
        nullable=False,
    )
    tipo = Column(String(20), nullable=False)
    cantidad = Column(Numeric(14, 3), nullable=False)
    # De donde viene. La compra es lo unico que lo genera hoy, pero 'VENTA' esta
    # porque la regla de negocio lo exige y porque un enum del que hay que quitar
    # un valor para anadirlo despues esta mal puesto.
    referencia_tipo = Column(String(20), nullable=False)
    referencia_id = Column(UUID(as_uuid=True), nullable=True)
    actor = Column(String(255), nullable=False)
    # Copia congelada: si el producto se renombra o se corrige la cantidad en su
    # linea, el kardex tiene que seguir diciendo lo que se aprobo ese dia.
    descripcion_origen = Column(String(300), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    producto = relationship("ProductoModel", lazy="selectin", passive_deletes=True)