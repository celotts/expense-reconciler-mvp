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
  ENTRADA sin compra           test_una_entrada_sin_ajuste_no_se_puede_escribir
  stock no negativo (Python)   test_no_se_puede_dejar_el_stock_negativo
  el rechazo no se deshace     test_una_compra_rechazada_no_se_puede_confirmar
  el rechazo no se resucita    test_una_compra_rechazada_no_registra_compra_otra_vez
  PROCESADO no se revierte     test_una_compra_procesada_no_se_puede_rechazar
  verificar no es declarar     test_verificar_un_producto_de_ocr_sin_codigo_no
  el origen no se edita        test_no_se_puede_cambiar_el_origen

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

Y HAY UNA RAZON CONCRETA PARA QUE ESO IMPORTE AHORA
---------------------------------------------------

El trigger de stock negativo hacia `IF tipo = 'ENTRADA' THEN suma ELSE resta`, o
sea que trataba `AJUSTE` como resta, mientras `stock_de` lo suma. Y su `SUM` de
filas previas ignoraba el signo de las SALIDAS. Las dos cosas eran inertes porque
la unica via que escribia movimientos era `confirmar_compra`, y esa solo produce
`ENTRADA`: el `ELSE` del trigger no se ejecutaba nunca.

`POST /inventario/movimientos` es el camino que lo vuelve vivo. Por eso la mitad
de Python de esa regla (`TestElSignoDelKardex`) esta aqui y la de Postgres esta en
`verify_postgres_inventario.py`: son dos implementaciones de la misma regla y
pueden divergir sin que ninguna se entere.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EstadoCompra, ProductoOrigen, TipoMovimiento
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


# ---------------------------------------------------------------------------
# El signo del kardex: `AJUSTE`, `SALIDA` y el trigger de Postgres
# ---------------------------------------------------------------------------
#
# POR QUE ESTOS TESTS ESTAN AQUI Y NO EN UN ARCHIVO NUEVO
# -------------------------------------------------------
#
# Porque el bug que los motiva estaba en el DDL de Postgres, no en Python, y su
# sintoma en la API era un `AJUSTE` que restaba stock en la base y lo sumaba en la
# respuesta. Los dos numeros describiendo la misma fila de dos maneras es
# exactamente el fallo que `AGENTS.md` llama "el proyecto no puede sostener".
#
# LO QUE ESTA EN SQL Y NO SE PUEDE PROBAR AQUI
# --------------------------------------------
#
# El trigger `trg_movimientos_no_negativo`, que antes hacia
# `IF tipo = 'ENTRADA' THEN suma ELSE resta` mientras `stock_de` suma `AJUSTE`, y
# cuyo `SUM` de filas previas ignoraba el signo de las SALIDAS. SQLite no tiene
# triggers de este tipo, asi que la version *de Postgres* de estas reglas se
# comprueba en `scripts/verify_postgres_inventario.py`, en
# `TestElSignoEnPostgres`. Este archivo comprueba la mitad de Python: que
# `stock_de`, `stock_de_una` y `TipoMovimiento.suma_stock` dicen lo mismo, que es
# lo que hace que la comprobacion del 409 y el numero que ve el usuario coincidan.


class TestElSignoDelKardex:
    async def test_una_salida_resta_y_una_entrada_suma(self, db_session):
        empresa = await _empresa(db_session)
        producto = await _producto(db_session, empresa, "Con salidas")
        await db_session.commit()

        for tipo, cantidad in (
            (TipoMovimiento.ENTRADA, Decimal("10")),
            (TipoMovimiento.SALIDA, Decimal("4")),
        ):
            db_session.add(
                MovimientoInventarioModel(
                    company_id=empresa.id,
                    producto_id=producto.id,
                    tipo=tipo.value,
                    cantidad=cantidad,
                    referencia_tipo="VENTA",
                    actor="ana@empresa.mx",
                )
            )
        await db_session.commit()

        assert (await inv.stock_de(db_session, empresa.id))[producto.id] == Decimal("6")

    async def test_suma_stock_dice_lo_mismo_que_el_tipo(self):
        """La propiedad que el trigger de Postgres replica en SQL.

        Si `suma_stock` y el trigger divergen, la base rechaza un movimiento que la
        API acepto. Se fija aqui en Python y en Postgres en
        `verify_postgres_inventario.py`, porque son dos implementaciones de la
        misma regla y pueden divergir sin que ninguna se entere.
        """
        assert TipoMovimiento.ENTRADA.suma_stock is True
        assert TipoMovimiento.SALIDA.suma_stock is False
        # `AJUSTE` no se escribe nunca como `tipo`: se escribe como ENTRADA o
        # SALIDA con `referencia_tipo='AJUSTE'`. Ver `registrar_movimiento`.
        assert TipoMovimiento.AJUSTE.suma_stock is True

    async def test_el_ajuste_hacia_arriba_suma_y_el_hacia_abajo_resta(self, db_session):
        """Un AJUSTE en las dos direcciones, que es el caso que no cabe en un enum.

        "Se conto de menos" y "se conto de mas" son el mismo `AJUSTE` para quien
        lee, y signos opuestos para el stock. La distincion va en `tipo`, y por
        eso `es_ajuste` y `tipo` son campos separados en `MovimientoCreate`.
        """
        empresa = await _empresa(db_session)
        producto = await _producto(db_session, empresa, "Ajustado")
        await db_session.commit()

        db_session.add(
            MovimientoInventarioModel(
                company_id=empresa.id,
                producto_id=producto.id,
                tipo=TipoMovimiento.ENTRADA.value,
                cantidad=Decimal("10"),
                referencia_tipo="COMPRA",
                actor="ana@empresa.mx",
            )
        )
        await db_session.commit()

        # Se conto de mas: el ajuste resta.
        await inv.registrar_movimiento(
            db_session,
            company_id=empresa.id,
            producto_id=producto.id,
            tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("3"),
            actor="ana@empresa.mx",
            referencia_tipo="AJUSTE",
        )
        # Se conto de menos: el ajuste suma.
        await inv.registrar_movimiento(
            db_session,
            company_id=empresa.id,
            producto_id=producto.id,
            tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("2"),
            actor="ana@empresa.mx",
            referencia_tipo="AJUSTE",
        )
        await db_session.commit()

        assert (await inv.stock_de(db_session, empresa.id))[producto.id] == Decimal("9")

    async def test_stock_de_una_dice_lo_mismo_que_stock_de(self, db_session):
        """La comprobacion del 409 y el stock del catalogo, sobre la misma fila.

        `stock_de_una` existe para no recorrer el kardex entero en cada
        `registrar_movimiento`. Si las dos contasen distinto, el "no hay stock
        suficiente" se dispararia con un numero que no es el que ve el usuario en
        el listado, y el 409 pareciese un bug.
        """
        empresa = await _empresa(db_session)
        producto = await _producto(db_session, empresa, "Comparado")
        otro = await _producto(db_session, empresa, "Otro")
        await db_session.commit()

        for prod, tipo, cantidad in (
            (producto, TipoMovimiento.ENTRADA, Decimal("10")),
            (producto, TipoMovimiento.SALIDA, Decimal("4")),
            (otro, TipoMovimiento.ENTRADA, Decimal("99")),
        ):
            db_session.add(
                MovimientoInventarioModel(
                    company_id=empresa.id,
                    producto_id=prod.id,
                    tipo=tipo.value,
                    cantidad=cantidad,
                    referencia_tipo="VENTA",
                    actor="ana@empresa.mx",
                )
            )
        await db_session.commit()

        todos = await inv.stock_de(db_session, empresa.id)
        assert await inv.stock_de_una(db_session, producto.id) == todos[producto.id]
        assert await inv.stock_de_una(db_session, otro.id) == todos[otro.id]


class TestRegistrarMovimiento:
    async def _preparado(self, db_session, stock: str = "10"):
        """Un producto con `stock` de entrada.

        La entrada se inserta DIRECTAMENTE y no con `registrar_movimiento`, y no por
        atajo: `registrar_movimiento` rechaza una `ENTRADA` que no sea de ajuste,
        que es exactamente lo que estos tests tienen que comprobar. Escribirla a
        mano es como se deja el estado inicial de un kardex que ya tiene historia,
        que es el punto de partida de todos ellos.

        `stock="0"` NO inserta la fila: `ck_movimientos_cantidad_positiva` lo
        prohibe, y con razon —un movimiento de cero no es un movimiento, es ruido
        en el kardex—. Para el caso "producto sin nada", esta misma funcion con
        `stock=None`.
        """
        empresa = await _empresa(db_session)
        producto = await _producto(db_session, empresa, "Producto")
        if stock is not None:
            db_session.add(
                MovimientoInventarioModel(
                    company_id=empresa.id,
                    producto_id=producto.id,
                    tipo=TipoMovimiento.ENTRADA.value,
                    cantidad=Decimal(stock),
                    referencia_tipo="COMPRA",
                    actor="ana@empresa.mx",
                )
            )
        await db_session.commit()
        return empresa, producto

    async def test_una_venta_resta_el_stock(self, db_session):
        empresa, producto = await self._preparado(db_session, stock="10")

        await inv.registrar_movimiento(
            db_session,
            company_id=empresa.id,
            producto_id=producto.id,
            tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("4"),
            actor="ana@empresa.mx",
            referencia_tipo="VENTA",
        )
        await db_session.commit()

        assert (await inv.stock_de(db_session, empresa.id))[producto.id] == Decimal("6")

    async def test_una_entrada_sin_ajuste_no_se_puede_escribir(self, db_session):
        """LA DEFENSA. Si quitas el `if` de `registrar_movimiento`, este test muere.

        Una `ENTRADA` con `referencia_tipo='VENTA'` o `'COMPRA'` escrita a mano
        sumaria stock sin que hubiera compra, sin linea y sin que nadie mirara el
        papel. `compras.ticket_id` UNIQUE deja de ser entonces la garantia de que
        el inventario solo refleja compras de verdad, porque ya no todas las
        entradas del kardex vienen de una compra.
        """
        empresa, producto = await self._preparado(db_session, stock=None)

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.registrar_movimiento(
                db_session,
                company_id=empresa.id,
                producto_id=producto.id,
                tipo=TipoMovimiento.ENTRADA,
                cantidad=Decimal("100"),
                actor="ana@empresa.mx",
                referencia_tipo="VENTA",
            )

        assert "confirmar una compra" in str(exc.value)
        await db_session.commit()
        # `stock_de` no devuelve los productos sin movimientos —no aparecen con 0,
        # no aparecen—, y esa es la distincion que `TestElStockEsLaSumaDelKardex`
        # fija aparte. Aqui lo que importa es que no se movio nada.
        assert await inv.stock_de(db_session, empresa.id) == {}
        assert await inv.stock_de_una(db_session, producto.id) == Decimal("0")

    async def test_una_entrada_de_ajuste_si_se_puede(self, db_session):
        """El caso legitimo que el bloqueo anterior tiene que dejar pasar.

        "Se conto de menos" suma stock y no viene de ninguna compra. Si el bloqueo
        fuera "nada de entradas", el inventario no se podria corregir hacia arriba
        y la unica salida seria editar el kardex, que el trigger prohibe.
        """
        empresa, producto = await self._preparado(db_session, stock=None)

        await inv.registrar_movimiento(
            db_session,
            company_id=empresa.id,
            producto_id=producto.id,
            tipo=TipoMovimiento.ENTRADA,
            cantidad=Decimal("7"),
            actor="ana@empresa.mx",
            referencia_tipo="AJUSTE",
        )
        await db_session.commit()

        assert (await inv.stock_de(db_session, empresa.id))[producto.id] == Decimal("7")

    async def test_no_se_puede_dejar_el_stock_negativo(self, db_session):
        """LA DEFENSA, y su mitad de Python.

        El trigger `trg_movimientos_no_negativo` es la garantia de Postgres y no se
        puede probar aqui (SQLite no tiene triggers). Esta comprobacion es la que
        existe para que el 409 diga "hay 6 y pediste restar 10" en vez de dejar que
        reviente el INSERT con un IntegrityError sin traducir.
        """
        empresa, producto = await self._preparado(db_session, stock="6")

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.registrar_movimiento(
                db_session,
                company_id=empresa.id,
                producto_id=producto.id,
                tipo=TipoMovimiento.SALIDA,
                cantidad=Decimal("10"),
                actor="ana@empresa.mx",
                referencia_tipo="VENTA",
            )

        # El mensaje lleva los dos numeros: sin ellos no se puede decidir si se
        # equivoco la cantidad o si el stock esta mal.
        assert "6" in str(exc.value) and "10" in str(exc.value)

    async def test_un_ajuste_hacia_abajo_tampoco_deja_negativo(self, db_session):
        """Un AJUSTE que resta es el caso caro, y por eso va aparte.

        La tentacion de "es solo un ajuste, dejalo pasar" es el error: el ajuste es
        la via por la que un error de conteo se vuelve permanente. Con esta
        comprobacion, la unica forma de bajar el stock es que haya stock.
        """
        empresa, producto = await self._preparado(db_session, stock="2")

        with pytest.raises(inv.ErrorDeInventario):
            await inv.registrar_movimiento(
                db_session,
                company_id=empresa.id,
                producto_id=producto.id,
                tipo=TipoMovimiento.SALIDA,
                cantidad=Decimal("5"),
                actor="ana@empresa.mx",
                referencia_tipo="AJUSTE",
            )

    async def test_una_cantidad_negativa_no_se_puede(self, db_session):
        """El signo va en el tipo. Una cantidad negativa seria una SALIDA."""
        empresa, producto = await self._preparado(db_session)

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.registrar_movimiento(
                db_session,
                company_id=empresa.id,
                producto_id=producto.id,
                tipo=TipoMovimiento.SALIDA,
                cantidad=Decimal("-4"),
                actor="ana@empresa.mx",
                referencia_tipo="VENTA",
            )
        assert "mayor que cero" in str(exc.value)

    async def test_sin_actor_no_se_registra(self, db_session):
        """LA DEFENSA. Sin actor no hay quien responda de la fila."""
        empresa, producto = await self._preparado(db_session)

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.registrar_movimiento(
                db_session,
                company_id=empresa.id,
                producto_id=producto.id,
                tipo=TipoMovimiento.SALIDA,
                cantidad=Decimal("1"),
                actor="  ",
                referencia_tipo="VENTA",
            )
        assert "quien lo registre" in str(exc.value)

    async def test_un_producto_de_otra_empresa_no_se_mueve(self, db_session):
        """No hay multi-tenancy, asi que esta comparacion es la unica que hay."""
        mia, _ = await self._preparado(db_session)
        ajena = await _empresa(db_session, "Ajena")
        suyo = await _producto(db_session, ajena, "De la otra")

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.registrar_movimiento(
                db_session,
                company_id=mia.id,
                producto_id=suyo.id,
                tipo=TipoMovimiento.SALIDA,
                cantidad=Decimal("1"),
                actor="ana@empresa.mx",
                referencia_tipo="VENTA",
            )
        assert "otra empresa" in str(exc.value)

    async def test_el_kardex_guarda_quien_y_que(self, db_session):
        """El actor y la descripcion congelada son lo que hace auditable la fila."""
        empresa, producto = await self._preparado(db_session)

        movimiento = await inv.registrar_movimiento(
            db_session,
            company_id=empresa.id,
            producto_id=producto.id,
            tipo=TipoMovimiento.SALIDA,
            cantidad=Decimal("2"),
            actor="ana@empresa.mx",
            referencia_tipo="VENTA",
            descripcion_origen="dos piezas vendidas",
        )
        await db_session.commit()

        assert movimiento.actor == "ana@empresa.mx"
        assert movimiento.descripcion_origen == "dos piezas vendidas"
        assert movimiento.referencia_tipo == "VENTA"


class TestActualizarProducto:
    async def test_renombrar_recalcula_el_nombre_normalizado(self, db_session):
        """La defensa de por que la cola se puede limpiar.

        "Reginen de" y "Regin de" son dos productos por el OCR. Quien mira el papel
        decide que son el mismo y renombra; el `@validates` del modelo recalcula
        `nombre_normalizado` para que las dos descripciones dejen de compararse
        distintas. Sin esto, el siguiente reescaneo crearia un tercero.
        """
        empresa = await _empresa(db_session)
        producto = await _producto(db_session, empresa, "Reginen de")
        await db_session.commit()
        assert producto.nombre_normalizado == "reginen de"

        await inv.actualizar_producto(db_session, producto.id, {"nombre": "Regina de"})
        await db_session.commit()

        assert producto.nombre == "Regina de"
        assert producto.nombre_normalizado == "regina de"

    async def test_verificar_un_producto_de_ocr_sin_codigo_no(self, db_session):
        """LA DEFENSA. Si quitas el `if`, este test muere.

        Un producto que salio de leer un papel no tiene identidad: hay una
        descripcion que el sistema creyo. Marcarla como verificada sin que nadie la
        mire seria afirmar que el sistema sabe lo que hay en el almacen, que es
        justo el salto que `verificado` existe para no dar. Con codigo de barras si
        se puede, porque un codigo es una identidad y no una opinion.
        """
        empresa = await _empresa(db_session)
        producto = ProductoModel(
            company_id=empresa.id,
            nombre="Cinta industrial",
            origen=ProductoOrigen.OCR.value,
            verificado=False,
        )
        db_session.add(producto)
        await db_session.commit()

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.actualizar_producto(db_session, producto.id, {"verificado": True})
        assert "no tiene codigo" in str(exc.value) or "sin codigo" in str(exc.value)

        producto.codigo = "7501234567890"
        await db_session.commit()

        await inv.actualizar_producto(db_session, producto.id, {"verificado": True})
        await db_session.commit()
        assert producto.verificado is True

    async def test_no_se_puede_cambiar_el_origen(self, db_session):
        """LA DEFENSA. Si quitas el `if`, este test muere.

        `origen` declara si lo puso una persona o si salio de leer un papel. Si
        fuera editable, un producto de OCR podria declararse MANUAL y salir de la
        cola de revision sin que nadie lo mirara. `ProductoUpdate` ni siquiera
        ofrece el campo; esta es la red por si alguien lo anade.
        """
        empresa = await _empresa(db_session)
        producto = await _producto(db_session, empresa, "Normal")
        await db_session.commit()

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.actualizar_producto(
                db_session, producto.id, {"origen": ProductoOrigen.MANUAL.value}
            )
        assert "origen" in str(exc.value)

    async def test_no_se_puede_mover_de_empresa(self, db_session):
        """El kardex lleva su propia copia de `company_id`.

        Mover el producto de empresa dejaria sus movimientos en la empresa vieja y
        contandose en la nueva: el stock apareceria en las dos, o en ninguna.
        """
        empresa = await _empresa(db_session)
        otra = await _empresa(db_session, "Otra")
        producto = await _producto(db_session, empresa, "Normal")
        await db_session.commit()

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.actualizar_producto(
                db_session, producto.id, {"company_id": otra.id}
            )
        assert "company_id" in str(exc.value)

    async def test_un_codigo_repetido_da_409_y_no_500(self, db_session):
        """El indice unico `ix_productos_codigo` es `(company_id, codigo)`.

        Sin la comprobacion previa, el codigo repetido llega al UPDATE y sale un
        IntegrityError que el router no traduce. Un 500 sin contexto para un error
        que el cliente puede corregir mandando otro codigo.
        """
        empresa = await _empresa(db_session)
        primero = await _producto(db_session, empresa, "Primero")
        segundo = ProductoModel(
            company_id=empresa.id, nombre="Segundo", codigo="7501234567890"
        )
        db_session.add(segundo)
        await db_session.commit()

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.actualizar_producto(
                db_session, primero.id, {"codigo": "7501234567890"}
            )
        assert "Segundo" in str(exc.value)

    async def test_dar_de_baja_no_borra_el_kardex(self, db_session):
        """Por que `activo` y no `DELETE`.

        `movimientos_inventario.producto_id` tiene `ON DELETE CASCADE`, asi que
        borrar un producto borra su historial entero y `stock_de` deja de poder
        responder por el. Dar de baja lo saca del catalogo y lo deja en el kardex.
        """
        empresa, producto = await self._preparado_con_stock(db_session)

        await inv.actualizar_producto(db_session, producto.id, {"activo": False})
        await db_session.commit()

        assert producto.activo is False
        movimientos = await inv.stock_de(db_session, empresa.id)
        assert producto.id in movimientos

    async def _preparado_con_stock(self, db_session):
        empresa = await _empresa(db_session)
        producto = await _producto(db_session, empresa, "Con historial")
        db_session.add(
            MovimientoInventarioModel(
                company_id=empresa.id,
                producto_id=producto.id,
                tipo=TipoMovimiento.ENTRADA.value,
                cantidad=Decimal("5"),
                referencia_tipo="COMPRA",
                actor="ana@empresa.mx",
            )
        )
        await db_session.commit()
        return empresa, producto

    async def test_un_producto_inexistente_da_404(self, db_session):
        with pytest.raises(inv.NoExiste):
            await inv.actualizar_producto(
                db_session, uuid.uuid4(), {"nombre": "X"}
            )


class TestRechazarYReabrirCompra:
    async def _compra(self, db_session):
        empresa = await _empresa(db_session)
        ticket = await _ticket(db_session, empresa, items=ITEMS_DE_EJEMPLO)
        compra = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()
        return empresa, compra

    async def test_rechazar_no_devuelve_la_compra_a_la_cola(self, db_session):
        empresa, compra = await self._compra(db_session)

        await inv.rechazar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        assert compra.estado == EstadoCompra.RECHAZADO

    async def test_una_compra_rechazada_no_se_puede_confirmar(self, db_session):
        """LA DEFENSA.

        Si `confirmar_compra` no distinguiera RECHAZADO de EN_REVISION, el rechazo
        seria solo cosmetico: bastaba con llamar al endpoint de confirmar para que
        la compra entrara al inventario. Y el 409 de "ya esta PROCESADO" no
        saltaria, porque todavia no lo esta.
        """
        empresa, compra = await self._compra(db_session)
        await inv.rechazar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        with pytest.raises(inv.ErrorDeInventario):
            await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")

        stocks = await inv.stock_de(db_session, empresa.id)
        assert stocks == {}

    async def test_una_compra_rechazada_no_registra_compra_otra_vez(self, db_session):
        """LA DEFENSA, y la razon de que RECHAZADO sea un estado y no un borrado.

        Sin estado, borrar la compra dejaria el ticket libre y el proximo
        `registrar_compra` la volveria a crear en EN_REVISION: el rechazo se
        deshecho solo en cuanto se tocaba el comprobante. Aqui la segunda llamada
        devuelve la misma compra, rechazada.
        """
        empresa, compra = await self._compra(db_session)
        await inv.rechazar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        # `compra.ticket` se relee del servidor en vez de usar el atributo: tras el
        # commit, `expire_on_commit=False` deja el objeto vivo pero su relacion
        # `lazy` no, y tocarla fuera de un `await` de SQLAlchemy lanza
        # MissingGreenlet. Es un detalle del ORM, no del comportamiento probado.
        ticket = await db_session.get(TicketModel, compra.ticket_id)
        otra = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()

        assert otra is not None
        assert otra.id == compra.id
        assert otra.estado == EstadoCompra.RECHAZADO

    async def test_una_compra_rechazada_no_aparece_en_la_cola_de_productos(self, db_session):
        """Una compra rechazada no genera trabajo de catalogo.

        `lineas_sin_producto` filtra por `estado != PROCESADO`, y RECHAZADO no es
        PROCESADO, asi que sus lineas SI aparecen en la cola. Es lo correcto: si
        alguien la reabre, las lineas siguen ahi con sus productos. Lo que no debe
        pasar es que el rechazo borre la informacion de las lineas.
        """
        empresa, compra = await self._compra(db_session)
        await inv.rechazar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        cola = await inv.lineas_sin_producto(db_session, empresa.id)
        assert cola == []  # las lineas ya tienen producto por resolucion automatica

    async def test_rechazar_dos_veces_no_falla(self, db_session):
        """Idempotente: un cliente que reintenta tras un timeout no recibe un error."""
        empresa, compra = await self._compra(db_session)

        await inv.rechazar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await inv.rechazar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        assert compra.estado == EstadoCompra.RECHAZADO

    async def test_una_compra_procesada_no_se_puede_rechazar(self, db_session):
        """LA DEFENSA. El kardex ya tiene movimientos y no se reescribe.

        El 409 dice que la via es el AJUSTE, porque sin ese camino en el mensaje
        quien lo lea busca una pantalla que no existe.
        """
        empresa, compra = await self._compra(db_session)
        await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.rechazar_compra(db_session, compra.id, actor="ana@empresa.mx")
        assert "AJUSTE" in str(exc.value)

    async def test_reabrir_devuelve_la_compra_a_revision(self, db_session):
        empresa, compra = await self._compra(db_session)
        await inv.rechazar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        await inv.reabrir_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        assert compra.estado == EstadoCompra.EN_REVISION

    async def test_una_compra_reabierta_se_puede_confirmar(self, db_session):
        """El ciclo completo del rechazo: rechazar, arrepentirse, autorizar."""
        empresa, compra = await self._compra(db_session)
        await inv.rechazar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await inv.reabrir_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        stocks = await inv.stock_de(db_session, empresa.id)
        assert sum(stocks.values()) == Decimal("15")

    async def test_una_compra_procesada_no_se_puede_reabrir(self, db_session):
        """Reabrir y deshacer un rechazo son cosas distintas, y se separan.

        Un unico "reabrir" que aceptara las dos necesitaria adivinar cual es, y el
        error —dar por reversible una compra que ya movio stock— no tiene salida.
        """
        empresa, compra = await self._compra(db_session)
        await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")
        await db_session.commit()

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.reabrir_compra(db_session, compra.id, actor="ana@empresa.mx")
        assert "no se reabre" in str(exc.value)

    async def test_sin_actor_no_se_rechaza(self, db_session):
        """LA DEFENSA. Quien rechaza sale del token, nunca del cuerpo."""
        empresa, compra = await self._compra(db_session)

        with pytest.raises(inv.ErrorDeInventario) as exc:
            await inv.rechazar_compra(db_session, compra.id, actor="")
        assert "quien la rechace" in str(exc.value)

    async def test_rechazar_sin_actor_no_cambia_el_estado(self, db_session):
        """LA DEFENSA, y su mitad importante: el estado no se toca."""
        empresa, compra = await self._compra(db_session)

        with pytest.raises(inv.ErrorDeInventario):
            await inv.rechazar_compra(db_session, compra.id, actor="   ")

        assert compra.estado == EstadoCompra.EN_REVISION