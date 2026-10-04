"""Inventario: de la factura digitalizada a la entrada al stock.

La regla del repo es que cada defensa tiene un test que MUERE si quitas la
defensa. Eso es lo que hay aqui: no se prueba que el codigo funciona, se prueba
que las reglas que lo sostienen no se pueden quitar sin que alguien lo note.

Las defensas y el test que las mata:

  ck_compras_confirmacion      test_la_compra_no_se_puede_poner_procesado_sin_firma
  registro idempotente         test_registrar_dos_veces_no_crea_dos_compras
  confirmar dos veces          test_confirmar_dos_veces_no_suma_stock_dos_veces
  lineas sin producto          test_no_se_confirma_con_lineas_sin_producto
  producto de otra empresa     test_no_se_asigna_un_producto_de_otra_empresa
  actor obligatorio            test_asignar_sin_actor_no_deja_la_linea_cambiada
  stock por suma del kardex    test_el_stock_es_la_suma_del_kardex
  producto no duplicado        test_la_misma_linea_no_crea_un_producto_nuevo
  buscar antes de crear        test_el_ocr_que_altera_las_tildes_no_parte_el_producto
  la marca de lo automatico    test_los_productos_del_ocr_quedan_para_revisar

Y los que no son defensas pero si tienen reglas:

  la interpretacion de items    TestInterpretarItems
  el descarte de lineas         TestLasLineasQueNoSeEntienen
  la normalizacion              TestNormalizarDescripcion
  la resolucion de lineas       TestResolverLineas
  la cola                       TestLaCola
  el camino completo            TestLaCompraYaSeConfirmaSola

OJO CON LOS TRIGGERS

`ck_compras_confirmacion` y las demas constraints SI se prueban aqui, porque los
tests arman el esquema desde los modelos y SQLite las ejecuta.

Los TRIGGERS de Postgres —append-only del kardex y el stock no negativo— NO se
pueden probar aqui: SQLite no tiene triggers de este tipo y `AGENTS.md` ya avisa
de que un test verde sobre SQLite no dice nada de lo que SQLite ignora. Esos se
comprueban contra Postgres real, en
`scripts/verify_postgres_inventario.py`. Los tests de este archivo SON la
defensa: si el trigger falla, ese script sale con 1.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EstadoCompra, TipoMovimiento
from app.models.company import CompanyModel
from app.models.inventario import (
    CompraItemModel,
    CompraModel,
    MovimientoInventarioModel,
    ProductoModel,
)
from app.models.ticket import TicketModel
from app.services import inventario_service as inv

# --- Lo que devuelve el LLM, tal cual --------------------------------------
#
# Un producto con cantidad en TEXTO, que es lo que el modelo devuelve con
# frecuencia y lo que un `isinstance(v, float)` dejaria fuera. Y uno con el
# precio como numero, porque las dos formas aparecen.
ITEMS_DE_EJEMPLO = [
    {
        "description": "Caja de laminas",
        "quantity": "3",          # texto, no numero
        "unit_price": "1250.50",
        "total": "3751.50",
    },
    {
        "description": "Cinta industrial",
        "quantity": 12,
        "unit_price": 45.0,
        "total": 540.0,
    },
]


async def _empresa(db: AsyncSession, nombre: str = "Empresa Test") -> CompanyModel:
    empresa = CompanyModel(name=nombre, tax_id=f"TAX-{uuid.uuid4().hex[:10]}")
    db.add(empresa)
    await db.flush()
    return empresa


async def _ticket(
    db: AsyncSession,
    empresa: CompanyModel,
    *,
    items: list[dict] | None = None,
    total: str = "4291.50",
) -> TicketModel:
    ticket = TicketModel(
        company_id=empresa.id,
        provider_name="PROVEEDOR SA",
        total_amount=Decimal(total),
        tax_amount=Decimal("540.00"),
        expense_date=date(2026, 9, 15),
        raw_text="texto",
        extraction_status="AUTO_APROBADO",
        items=items,
    )
    db.add(ticket)
    await db.flush()
    return ticket


async def _producto(db: AsyncSession, empresa: CompanyModel, nombre: str) -> ProductoModel:
    producto = ProductoModel(company_id=empresa.id, nombre=nombre)
    db.add(producto)
    await db.flush()
    return producto


async def _desasignar(compra: CompraModel) -> None:
    """Deja una compra sin productos, a mano.

    Existe porque con la resolucion automatica (`0011`) toda linea guardada llega
    con producto. Los tests de las defensas de "linea sin producto" tienen que
    poner al sistema en ese estado explicitamente, o estarian probando que la
    resolucion funciona en vez de que el bloqueo existe.
    """
    for item in compra.items:
        item.producto_id = None
        item.producto_asignado_por = None
        item.producto_asignado_at = None


# ---------------------------------------------------------------------------
# Interpretar el JSON crudo
# ---------------------------------------------------------------------------


class TestInterpretarItems:
    """De lo que devuelve el modelo a filas utilizables."""

    def test_una_cantidad_en_texto_sirve(self):
        """`"3"` es una cantidad, no basura.

        Lo devuelve el modelo con frecuencia y un `isinstance(v, float)` la
        dejaria fuera como si no hubiera linea. Perder 14 lineas correctas por
        una que venia con la cantidad en texto no es un comportamiento que
        nadie quiera.
        """
        lineas, descartadas = inv.interpretar_items(ITEMS_DE_EJEMPLO)
        assert len(lineas) == 2
        assert descartadas == []
        assert lineas[0]["cantidad"] == Decimal("3")
        assert lineas[0]["costo_unitario"] == Decimal("1250.50")

    def test_el_orden_del_papel_se_conserva(self):
        """Sin esto no se puede cotejar una linea con la linea del papel."""
        lineas, _ = inv.interpretar_items(ITEMS_DE_EJEMPLO)
        assert [linea["orden"] for linea in lineas] == [0, 1]

    def test_none_no_es_una_linea(self):
        """items NULL = el lector no produjo lineas. No es un comprobante vacio."""
        assert inv.interpretar_items(None) == ([], [])
        assert inv.interpretar_items([]) == ([], [])

    def test_una_fuente_que_no_es_lista_no_revienta(self):
        assert inv.interpretar_items({"description": "algo"}) == ([], [])

    def test_un_separador_de_miles_no_rompe_el_numero(self):
        lineas, _ = inv.interpretar_items(
            [{"description": "X", "quantity": "1,250.50"}]
        )
        assert lineas[0]["cantidad"] == Decimal("1250.50")


class TestLasLineasQueNoSeEntienden:
    """Una linea mala no puede tumbar las buenas.

    Y cada descarte se cuenta y se dice cual fue: el usuario necesita saber "de 15
    lineas se guardaron 12" ANTES de autorizar una compra.
    """

    def test_una_linea_sin_descripcion_se_descarta_y_las_demas_siguen(self):
        lineas, descartadas = inv.interpretar_items(
            [
                {"description": "Caja", "quantity": 2},
                {"quantity": 5},                       # sin descripcion
                {"description": "Cinta", "quantity": 3},
            ]
        )
        assert len(lineas) == 2
        assert len(descartadas) == 1
        assert descartadas[0].indice == 1
        assert "descripcion" in descartadas[0].motivo

    def test_una_cantidad_ilegible_no_se_adivina(self):
        """Sin cantidad, la linea no se puede sumar a ningun inventario.

        Y `0` no es el valor por omision honesto: 0 seria un producto que se
        compro de a gratis.
        """
        lineas, descartadas = inv.interpretar_items(
            [{"description": "Caja", "quantity": "muchos"}]
        )
        assert lineas == []
        assert "cantidad" in descartadas[0].motivo

    def test_una_cantidad_negativa_se_descarta(self):
        """Una devolucion es una SALIDA, no una compra con signo cambiado."""
        lineas, descartadas = inv.interpretar_items(
            [{"description": "Caja", "quantity": -3}]
        )
        assert lineas == []
        assert "positiva" in descartadas[0].motivo

    def test_un_boolean_no_es_una_cantidad(self):
        """`True` es un int en Python: `Decimal("True")` es un crash."""
        lineas, descartadas = inv.interpretar_items(
            [{"description": "Caja", "quantity": True}]
        )
        assert lineas == []
        assert descartadas[0].motivo == "cantidad ilegible"

    def test_un_elemento_que_no_es_objeto_no_revienta(self):
        lineas, descartadas = inv.interpretar_items(["Caja", 5, None])
        assert lineas == []
        assert len(descartadas) == 3


# ---------------------------------------------------------------------------
# Normalizar: la forma en que dos descripciones se comparan
# ---------------------------------------------------------------------------


class TestNormalizarDescripcion:
    """Sin esto, "Cinta Industrial", "cinta industrial" y "CINTA  INDUSTRIAL."
    son tres productos y el stock se reparte entre tres filas que son la misma
    cosa. Es el problema que la resolucion automatica tiene que evitar.
    """

    @pytest.mark.parametrize(
        "a,b",
        [
            ("Cinta Industrial", "cinta industrial"),
            ("Café molido", "Cafe molido"),            # con y sin tilde
            ("Cinta  Industrial", "Cinta Industrial"),  # espacios de mas
            ("Cinta Industrial.", "Cinta Industrial"),  # punto de final de linea
            ("  Cinta Industrial  ", "Cinta Industrial"),
        ],
    )
    def test_lo_que_debe_ser_igual_es_igual(self, a, b):
        assert inv.normalizar_descripcion(a) == inv.normalizar_descripcion(b)

    def test_no_funde_cosas_diferentes(self):
        """El caso que hace peligroso el fuzzy matching.

        "Cinta Industrial 50mm" y "Cinta Industrial" NO son el mismo producto.
        Si lo fueran, el stock de las dos se sumaria en una fila.
        """
        assert inv.normalizar_descripcion("Cinta Industrial 50mm") != inv.normalizar_descripcion(
            "Cinta Industrial"
        )

    def test_una_cadena_vacia_sigue_vacia(self):
        assert inv.normalizar_descripcion("   ") == ""


# ---------------------------------------------------------------------------
# De una linea de papel a un producto del catalogo
# ---------------------------------------------------------------------------


class TestResolverProducto:
    async def test_una_linea_nueva_crea_su_producto(self, db_session):
        empresa = await _empresa(db_session)
        salida = await inv.resolver_producto(db_session, empresa.id, "Caja de laminas")

        assert salida.creado is True
        assert salida.producto.nombre == "Caja de laminas"
        # Y nace marcado, que es lo que hace manejable el alta automatica.
        assert salida.producto.origen == "OCR"
        assert salida.producto.verificado is False

    async def test_la_misma_linea_no_crea_un_producto_nuevo(self, db_session):
        """LA DEFENZA MAS IMPORTANTE DE ESTA CLASE.

        Sin esto, re-escanear el mismo comprobante crea un producto nuevo cada
        vez, y a la tercera pasada hay tres filas para el mismo carton con tres
        stocks distintos. La compra se confirma igual y nada se queja: tres filas
        son tres filas.
        """
        empresa = await _empresa(db_session)
        primera = await inv.resolver_producto(db_session, empresa.id, "Caja de laminas")
        segunda = await inv.resolver_producto(db_session, empresa.id, "Caja de laminas")
        await db_session.commit()

        assert segunda.creado is False
        assert segunda.reutilizado is True
        assert segunda.producto.id == primera.producto.id

    async def test_el_ocr_que_altera_las_tildes_no_parte_el_producto(self, db_session):
        """El caso real: Tesseract escribe "Cafe" donde el papel dice "Café".

        Y es por esto que existe la columna `nombre_normalizado`: comparar con
        `lower(nombre)` NO alcanza, porque `lower('Café molido')` conserva la
        tilde y el objetivo normalizado no la tiene.
        """
        empresa = await _empresa(db_session)
        primera = await inv.resolver_producto(db_session, empresa.id, "Café molido")
        segunda = await inv.resolver_producto(db_session, empresa.id, "Cafe molido")
        await db_session.commit()

        assert segunda.producto.id == primera.producto.id

    async def test_un_codigo_de_barras_da_una_identidad_y_no_hay_que_verificar(self, db_session):
        """Un codigo es una identidad, no una descripcion: no hay nada que adivinar."""
        empresa = await _empresa(db_session)
        salida = await inv.resolver_producto(
            db_session, empresa.id, "7501234567890 algo", codigo="7501234567890"
        )

        assert salida.producto.verificado is True
        assert salida.producto.codigo == "7501234567890"

    async def test_el_mismo_codigo_no_crea_un_segundo_producto(self, db_session):
        empresa = await _empresa(db_session)
        await inv.resolver_producto(db_session, empresa.id, "Uno", codigo="COD-1")
        segunda = await inv.resolver_producto(
            db_session, empresa.id, "Otro nombre", codigo="COD-1"
        )
        await db_session.commit()

        assert segunda.creado is False
        assert segunda.producto.nombre == "Uno"

    async def test_un_producto_de_otra_empresa_no_se_reutiliza(self, db_session):
        """La comparacion es por empresa. Sin esto, dos empresas con el mismo
        texto sharingarian un producto y el stock se sumaria entre ellas."""
        mia = await _empresa(db_session, "Mia")
        ajena = await _empresa(db_session, "Ajena")
        await inv.resolver_producto(db_session, ajena.id, "Caja de laminas")

        mia_salida = await inv.resolver_producto(db_session, mia.id, "Caja de laminas")
        assert mia_salida.creado is True

    async def test_una_descripcion_vacia_no_crea_un_producto(self, db_session):
        empresa = await _empresa(db_session)
        with pytest.raises(inv.ErrorDeInventario, match="sin descripcion"):
            await inv.resolver_producto(db_session, empresa.id, "   ")


class TestResolverLineas:
    async def test_toda_linea_de_la_compra_queda_con_producto(self, db_session):
        """Es lo que permite confirmar sin ir a una cola a mano."""
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        compra = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()

        assert len(compra.items) == 2
        assert all(item.producto_id is not None for item in compra.items)

    async def test_una_segunda_compra_reutiliza_los_mismos_productos(self, db_session):
        """El numero importa: "15 lineas con 15 productos nuevos" es un catalogo
        que hay que revisar entero; "15 con 2" es un escaneo normal."""
        empresa = await _empresa(db_session)
        await inv.registrar_compra(
            db_session, await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        )
        await db_session.commit()

        otra = await inv.registrar_compra(
            db_session, await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        )
        await db_session.commit()

        productos = (
            await db_session.execute(
                select(ProductoModel).where(ProductoModel.company_id == empresa.id)
            )
        ).scalars().all()
        assert len(productos) == 2

    async def test_quien_asigno_queda_escrito_y_es_el_sistema(self, db_session):
        """Se distingue de "lo eligio Ana". Un producto sin saber de donde vino
        no se puede auditar."""
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        compra = await inv.registrar_compra(db_session, ticket)

        for item in compra.items:
            assert item.producto_asignado_por == "sistema:extraccion"
            assert item.producto_asignado_at is not None

    async def test_es_idempotente(self, db_session):
        """El escaner puede pasar dos veces por el mismo comprobante."""
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        compra = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()
        antes = [i.producto_id for i in compra.items]

        resueltas, creadas = await inv.resolver_lineas(
            db_session, compra, actor="sistema:extraccion"
        )
        await db_session.commit()

        assert resueltas == 0      # todas ya tenian producto
        assert creadas == 0
        assert [i.producto_id for i in compra.items] == antes


# ---------------------------------------------------------------------------
# Idempotencia: un comprobante no se cuenta dos veces
# ---------------------------------------------------------------------------


class TestRegistrarCompra:
    """De `tickets.items` a una compra en EN_REVISION."""

    async def test_una_compra_nace_en_revision_y_no_procesada(self, db_session):
        """La compra NO puede nacer autorizada.

        Este es el test mas importante de la clase: es el que muere si alguien
        pone `estado=PROCESADO` al crear. El unico camino a PROCESADO es
        `confirmar_compra`, que exige una persona.
        """
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)

        compra = await inv.registrar_compra(db_session, ticket)
        assert compra is not None
        assert compra.estado == EstadoCompra.EN_REVISION
        assert compra.confirmada_por is None
        assert compra.confirmada_at is None

    async def test_sin_items_no_hay_compra_y_no_es_error(self, db_session):
        """Es el caso NORMAL de un comprobante leido por Tesseract.

        Que no haya compra no es un fallo: es que no hay nada que meter al
        inventario todavia. Lo que seria un fallo es perder el gasto.
        """
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=None)

        assert await inv.registrar_compra(db_session, ticket) is None
        await db_session.refresh(ticket)
        assert ticket.id is not None

    async def test_items_todo_malo_no_tumba_el_ticket(self, db_session):
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=[{"nada": "sirva"}])

        assert await inv.registrar_compra(db_session, ticket) is None
        await db_session.refresh(ticket)
        assert ticket.id is not None

    async def test_una_linea_ilegible_no_crea_un_producto(self, db_session):
        """Lo que se descarta no llega a productos; lo que se guarda, si.

        Una linea ilegible es basura del modelo, y hacer un producto de ella
        seria meter en el catalogo un producto que no existe.
        """
        empresa = await _empresa(db_session)
        ticket = await _ticket(
            db_session, empresa, items=[{"nada": "sirva"}, {"description": "X"}]
        )

        compra = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()

        productos = (
            await db_session.execute(
                select(ProductoModel).where(ProductoModel.company_id == empresa.id)
            )
        ).scalars().all()
        # Ninguna de las dos lineas era utilizable: falta la cantidad en una y la
        # descripcion en la otra. Cero productos, y no por el alta automatica sino
        # porque no llego ninguna linea.
        assert compra is None
        assert productos == []

    async def test_registrar_dos_veces_no_crea_dos_compras(self, db_session):
        """La idempotencia.

        Sin esto, el escaner releyendo el mismo comprobante —o un POST repetido—
        generan dos compras y el inventario sube el doble. La garantia real es el
        UNIQUE de `compras.ticket_id`; esto es lo que evita que la segunda llamada
        reviente con IntegrityError en vez de devolver la misma compra.
        """
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)

        primera = await inv.registrar_compra(db_session, ticket)
        segunda = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()

        assert primera.id == segunda.id
        en_base = (
            await db_session.execute(
                select(CompraModel).where(CompraModel.ticket_id == ticket.id)
            )
        ).scalars().all()
        assert len(en_base) == 1


# ---------------------------------------------------------------------------
# Asignar un producto a una linea
# ---------------------------------------------------------------------------


class TestAsignarProducto:
    async def test_asignar_guarda_quien_y_cuando(self, db_session):
        """"Lo eligio el sistema" y "lo eligio Ana" son respuestas distintas
        a "de donde salio este producto del catalogo"."""
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        compra = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()
        await _desasignar(compra)
        await db_session.commit()

        producto = await _producto(db_session, empresa, "Caja de laminas")
        item = await inv.asignar_producto(
            db_session, compra.items[0].id, producto.id, actor="ana@empresa.mx"
        )
        await db_session.commit()

        assert item.producto_id == producto.id
        assert item.producto_asignado_por == "ana@empresa.mx"
        assert item.producto_asignado_at is not None

    async def test_no_se_asigna_un_producto_de_otra_empresa(self, db_session):
        """La comprobacion va en el SERVICIO, no en el router.

        `AGENTS.md`: no hay multi-tenancy, cualquiera autenticado puede mandar un
        `company_id` arbitrario. Es la ultima linea antes de que un producto de
        otra empresa acabe en el kardex de esta.
        """
        mia = await _empresa(db_session, "Mia")
        ajena = await _empresa(db_session, "Ajena")
        ticket = await _ticket(db_session, mia, items=ITEMS_DE_EJEMPLO)
        compra = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()
        await _desasignar(compra)
        await db_session.commit()

        producto_ajeno = await _producto(db_session, ajena, "De la otra")
        with pytest.raises(inv.ErrorDeInventario, match="otra empresa"):
            await inv.asignar_producto(
                db_session, compra.items[0].id, producto_ajeno.id, actor="ana@empresa.mx"
            )

    async def test_asignar_sin_actor_no_deja_la_linea_cambiada(self, db_session):
        """Una linea con producto sin saber quien lo eligio no se puede auditar."""
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        compra = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()
        await _desasignar(compra)
        await db_session.commit()

        producto = await _producto(db_session, empresa, "Caja")
        with pytest.raises(inv.ErrorDeInventario, match="quien lo asigne"):
            await inv.asignar_producto(db_session, compra.items[0].id, producto.id, actor="")

        await db_session.refresh(compra.items[0])
        assert compra.items[0].producto_id is None


# ---------------------------------------------------------------------------
# Confirmar: aqui es donde el inventario suma
# ---------------------------------------------------------------------------


class TestConfirmarCompra:
    async def _preparada(self, db_session, con_productos: bool = True):
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        compra = await inv.registrar_compra(db_session, ticket)
        if not con_productos:
            await db_session.commit()
            await _desasignar(compra)
        await db_session.commit()
        return empresa, compra

    async def test_confirmar_mueve_el_stock(self, db_session):
        empresa, compra = await self._preparada(db_session)

        await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        stocks = await inv.stock_de(db_session, empresa.id)
        # 3 + 12 = 15 en total, repartidos entre dos productos.
        assert sum(stocks.values()) == Decimal("15")
        assert len(stocks) == 2

    async def test_confirmar_deja_la_compra_procesada_con_firma(self, db_session):
        _, compra = await self._preparada(db_session)

        await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()
        await db_session.refresh(compra)

        assert compra.estado == EstadoCompra.PROCESADO
        assert compra.confirmada_por == "ana@empresa.mx"
        assert compra.confirmada_at is not None

    async def test_la_compra_no_se_puede_poner_procesado_sin_firma(self, db_session):
        """LA DEFENZA. Si quita `ck_compras_confirmacion`, este test muere.

        Sin la constraint, se podria UPDATEar una compra a PROCESADO con un solo
        INSERT en movimientos_inventario: el inventario moveria sin que nadie
        hubiera autorizado, y la fila no diria quien.
        """
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        compra = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()

        compra.estado = EstadoCompra.PROCESADO
        with pytest.raises(Exception, match="ck_compras_confirmacion"):
            await db_session.commit()
        await db_session.rollback()

    async def test_confirmar_dos_veces_no_suma_stock_dos_veces(self, db_session):
        """LA DEFENZA del lado del servicio.

        El UNIQUE protege el ticket, no el stock: sin esta comprobacion, dos
        llamadas a `confirmar` sumarian el doble. La correccion es un AJUSTE, no
        volver a confirmar.
        """
        empresa, compra = await self._preparada(db_session)

        await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()
        with pytest.raises(inv.ErrorDeInventario, match="ya esta PROCESADO"):
            await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")

        stocks = await inv.stock_de(db_session, empresa.id)
        assert sum(stocks.values()) == Decimal("15")

    async def test_no_se_confirma_con_lineas_sin_producto(self, db_session):
        """LA DEFENSA. Autorizar con lineas sin producto dejaria el stock
        incompleto sin que quede rastro.

        Con la resolucion automatica (`0011`) toda linea guardada llega con
        producto, asi que esto ya no deberia pasar. Por eso `_desasignar` pone al
        sistema en ese estado explicitamente: un test que dejara las lineas como
        llegan probaria que la resolucion funciona, no que el bloqueo existe.
        """
        empresa, compra = await self._preparada(db_session, con_productos=False)

        with pytest.raises(inv.ErrorDeInventario, match="sin producto"):
            await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")

        stocks = await inv.stock_de(db_session, empresa.id)
        assert stocks == {}

    async def test_confirmar_sin_actor_no_mueve_nada(self, db_session):
        empresa, compra = await self._preparada(db_session)

        with pytest.raises(inv.ErrorDeInventario, match="quien la confirme"):
            await inv.confirmar_compra(db_session, compra.id, actor="  ")

        stocks = await inv.stock_de(db_session, empresa.id)
        assert stocks == {}

    async def test_el_movimiento_guarda_el_actor_y_la_descripcion_del_papel(self, db_session):
        """El kardex dice QUIEN movio y QUE decia el papel.

        Si el producto se renombra manana, el movimiento tiene que seguir
        diciendo lo que se aprobo ese dia.
        """
        empresa, compra = await self._preparada(db_session)
        await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        movimientos = (
            await db_session.execute(
                select(MovimientoInventarioModel).where(
                    MovimientoInventarioModel.company_id == empresa.id
                )
            )
        ).scalars().all()

        assert len(movimientos) == 2
        for m in movimientos:
            assert m.actor == "ana@empresa.mx"
            assert m.tipo == TipoMovimiento.ENTRADA.value
            assert m.referencia_tipo == "COMPRA"
            assert m.referencia_id == compra.id
            assert m.descripcion_origen in {"Caja de laminas", "Cinta industrial"}


# ---------------------------------------------------------------------------
# El stock
# ---------------------------------------------------------------------------


class TestElStockEsLaSumaDelKardex:
    async def test_un_producto_sin_movimientos_no_aparece(self, db_session):
        """No aparece con 0, sino que no aparece.

        La diferencia importa al pedir la lista: un producto con stock 0 se
        distingue de "este producto no ha tenido movimientos".
        """
        empresa = await _empresa(db_session)
        await _producto(db_session, empresa, "Sin movimientos")

        assert await inv.stock_de(db_session, empresa.id) == {}

    async def test_entradas_menos_salidas(self, db_session):
        empresa = await _empresa(db_session)
        producto = await _producto(db_session, empresa, "Con stock")
        await db_session.commit()

        for tipo, cantidad in (
            (TipoMovimiento.ENTRADA, Decimal("10")),
            (TipoMovimiento.SALIDA, Decimal("4")),
            (TipoMovimiento.ENTRADA, Decimal("1")),
        ):
            db_session.add(
                MovimientoInventarioModel(
                    company_id=empresa.id,
                    producto_id=producto.id,
                    tipo=tipo.value,
                    cantidad=cantidad,
                    referencia_tipo="COMPRA",
                    actor="ana@empresa.mx",
                )
            )
        await db_session.commit()

        stocks = await inv.stock_de(db_session, empresa.id)
        assert stocks[producto.id] == Decimal("7")

    async def test_el_stock_de_una_empresa_no_se_mezcla_con_otra(self, db_session):
        """No hay multi-tenancy, asi que el filtro por empresa es el unico que hay."""
        mia = await _empresa(db_session, "Mia")
        ajena = await _empresa(db_session, "Ajena")
        producto = await _producto(db_session, mia, "Solo mio")
        db_session.add(
            MovimientoInventarioModel(
                company_id=ajena.id,
                producto_id=producto.id,
                tipo=TipoMovimiento.ENTRADA.value,
                cantidad=Decimal("999"),
                referencia_tipo="COMPRA",
                actor="ana@empresa.mx",
            )
        )
        await db_session.commit()

        stocks = await inv.stock_de(db_session, mia.id)
        assert producto.id not in stocks


# ---------------------------------------------------------------------------
# La cola
# ---------------------------------------------------------------------------


class TestLaCola:
    """La cola de LINEAS sin producto quedo vacia con la resolucion automatica.

    Y eso es lo que se comprueba: que este vacia, y que si algo se queda sin
    producto el bloqueo de confirmar la avisa. La cola de trabajo REAL ya no son
    las lineas —son los productos que el OCR invento— y esa la responde
    `GET /inventario/productos?solo_sin_verificar=true`.
    """

    async def test_la_resolucion_automatica_deja_la_cola_vacia(self, db_session):
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        await inv.registrar_compra(db_session, ticket)
        await db_session.commit()

        # Antes esto era "2 lineas en la cola". Ahora es cero, y ese es el cambio
        # de decision: el producto se crea solo para que la compra pueda
        # procesarse y tener movimiento.
        assert await inv.lineas_sin_producto(db_session, empresa.id) == []

    async def test_una_linea_desasignada_a_mano_si_aparece_en_la_cola(self, db_session):
        """La cola sigue sirviendo si algo se salta la resolucion."""
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        compra = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()
        await _desasignar(compra)
        await db_session.commit()

        cola = await inv.lineas_sin_producto(db_session, empresa.id)
        assert len(cola) == 2

    async def test_una_compra_confirmada_no_aparece_en_la_cola(self, db_session):
        """Una vez PROCESADO no hay nada que asignar: el stock ya se movio."""
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        compra = await inv.registrar_compra(db_session, ticket)
        await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        assert await inv.lineas_sin_producto(db_session, empresa.id) == []

    async def test_los_productos_del_ocr_quedan_para_revisar(self, db_session):
        """LA COLA QUE SI IMPORTA.

        El inventario funciona desde el primer dia, y a la vez hay una lista de
        lo que hay que limpiar. Las dos cosas a la vez, que es lo que hace
        manejable el alta automatica. Ver db/migrations/0011_productos_origen.sql.
        """
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        await inv.registrar_compra(db_session, ticket)
        await db_session.commit()

        productos = (
            await db_session.execute(
                select(ProductoModel).where(ProductoModel.company_id == empresa.id)
            )
        ).scalars().all()
        assert len(productos) == 2
        assert all(p.origen == "OCR" for p in productos)
        assert all(p.verificado is False for p in productos)

    async def test_un_producto_dado_de_alta_a_mano_no_entra_en_la_cola(self, db_session):
        """Lo que puso una persona, mirando el producto, no necesita revision."""
        empresa = await _empresa(db_session)
        await _producto(db_session, empresa, "Producto de verdad")
        await db_session.commit()

        productos = (
            await db_session.execute(
                select(ProductoModel).where(
                    ProductoModel.company_id == empresa.id,
                    ProductoModel.verificado.is_(False),
                )
            )
        ).scalars().all()
        assert productos == []


# ---------------------------------------------------------------------------
# El camino completo
# ---------------------------------------------------------------------------


class TestLaCompraYaSeConfirmaSola:
    """Escanear -> EN_REVISION -> confirmar. Sin tocar la cola.

    Es el flujo que pide el negocio: digitalizar, registrar productos nuevos, y
    que la compra llegue a un estado que alguien autoriza.
    """

    async def test_el_camino_completo_llega_a_procesado_sin_intervencion(self, db_session):
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)

        compra = await inv.registrar_compra(db_session, ticket)
        assert compra is not None
        assert compra.estado == EstadoCompra.EN_REVISION
        await db_session.commit()

        await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        stocks = await inv.stock_de(db_session, empresa.id)
        assert sum(stocks.values()) == Decimal("15")
        assert len(stocks) == 2