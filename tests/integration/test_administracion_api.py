"""La administracion: productos, kardex, compras y cuentas.

POR QUE ESTE ARCHIVO EXISTE
---------------------------

Porque hasta ahora los endpoints que faltaban eran de dos clases, y las dos se
notaban desde la aplicacion:

  - **Funciones documentadas sin puerta.** `GET /inventario/productos
    ?solo_sin_verificar=true` devuelve la cola de productos que salieron de leer un
    papel, y `AGENTS.md` le atribuye la tarea de "revisar, renombrar o fusionar" —
    pero no habia ningun `PATCH` que lo hiciera. La cola se podia mirar y no
    limpiar. `TipoMovimiento.AJUSTE` existia, `stock_de` lo sumaba, y no habia
    ninguna via para escribirlo.

  - **Ciclos que no se podian deshacer.** Una compra rechazada se "*rechazaba*" no
    haciendo nada, porque `registrar_compra` la volvia a crear; dar de baja a una
    cuenta no existia; cambiar una contrasena, tampoco.

QUE COMPRUEBA Y QUE NO
----------------------

Los caminos HTTP: que el 409 llegue con su motivo, que el 404 sea un 404, que un
campo que no se puede cambiar no se acepte en silencio.

Lo que NO se comprueba aqui y por que:

  - **Los triggers de Postgres.** SQLite no los tiene. Van en
    `scripts/verify_postgres_inventario.py`.
  - **Que un `PATCH` de conciliacion no se pueda forjar.** El estado de la sesion
    lo decide `deps`, y ya hay tests de eso.

UNA COSA QUE ESTA PRESENTE Y NO EN LOS OTROS ARCHIVES
------------------------------------------------------

`test_el_rechazo_no_se_deshace_llamando_a_confirmar`. El rechazo escribe un estado
en la base, pero lo que lo hace una decision y no una constante es que
`confirmar_compra` lo respete. Un estado que existe y que la unica operacion que
mueve stock no mira es un estado decorativo, y el 409 de "ya PROCESADO" no
salvaria: en el momento del rechazo la compra todavia no esta PROCESADO.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.enums import EstadoCompra, ProductoOrigen, TipoMovimiento
from app.models.inventario import (
    CompraModel,
    MovimientoInventarioModel,
    ProductoModel,
)
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel

# Lo que devuelve el LLM: descripcion en texto y cantidad como numero, que es lo
# que mas sale. Ver ITEMS_DE_EJEMPLO en tests/unit/test_inventario.py.
ITEMS = [
    {
        "description": "Caja de laminas",
        "quantity": "3",
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

CONTRASENA = "contrasena-de-prueba"


async def _producto(db_session, empresa, nombre: str, **extra) -> ProductoModel:
    producto = ProductoModel(company_id=empresa.id, nombre=nombre, **extra)
    db_session.add(producto)
    await db_session.commit()
    await db_session.refresh(producto)
    return producto


async def _con_stock(db_session, empresa, producto, cantidad: str) -> None:
    """Le da entrada por la puerta de verdad: una compra confirmada.

    Se usa en vez de un INSERT directo porque `registrar_movimiento` rechaza una
    `ENTRADA` que no sea de ajuste, y los endpoints de esta prueba tienen que
    empezar desde un kardex legitimo.
    """
    ticket = TicketModel(
        company_id=empresa.id,
        provider_name="PROVEEDOR",
        total_amount=Decimal("100.00"),
        tax_amount=Decimal("16.00"),
        expense_date=date(2026, 9, 15),
        raw_text="texto",
        extraction_status="APROBADO",
        items=[
            {
                "description": producto.nombre,
                "quantity": str(cantidad),
                "unit_price": "10.00",
                "total": "100.00",
            }
        ],
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)

    compra = CompraModel(
        company_id=empresa.id,
        ticket_id=ticket.id,
        estado=EstadoCompra.EN_REVISION.value,
        fecha=date(2026, 9, 15),
        total=Decimal("100.00"),
    )
    compra.compra = None
    db_session.add(compra)
    await db_session.commit()

    from app.models.inventario import CompraItemModel

    db_session.add(
        CompraItemModel(
            compra_id=compra.id,
            producto_id=producto.id,
            descripcion=producto.nombre,
            cantidad=Decimal(cantidad),
            costo_unitario=Decimal("10.00"),
            total=Decimal("100.00"),
            orden=0,
            producto_asignado_por="sistema:extraccion",
            producto_asignado_at=ticket.created_at,
        )
    )
    await db_session.commit()

    from app.services import inventario_service as inv

    await inv.confirmar_compra(db_session, compra.id, actor="ana@empresa.mx")
    await db_session.commit()
    await db_session.refresh(producto)


# ---------------------------------------------------------------------------
# El catalogo: la cola se puede limpiar
# ---------------------------------------------------------------------------


class TestAdministrarProductos:
    async def test_verificar_el_producto_de_la_cola(self, async_client, db_session, test_company):
        """EL BUG QUE ESTE ARCHIVO CIERRA.

        `GET /inventario/productos?solo_sin_verificar=true` devuelve la cola de
        productos que salieron de leer un papel. Antes no habia forma de sacarlos de
        ahi: `verificado` no se podia cambiar por ningun endpoint, con lo que la
        cola crecia sin limite y era una lista que no se vaciaba.
        """
        producto = await _producto(
            db_session,
            test_company,
            "Cinta industrial",
            origen=ProductoOrigen.OCR.value,
            verificado=False,
            codigo="7501234567890",
        )

        r = await async_client.patch(
            f"/api/v1/inventario/productos/{producto.id}",
            params={"company_id": str(test_company.id)},
            json={"verificado": True},
        )
        assert r.status_code == 200, r.text
        assert r.json()["verificado"] is True

        # Y sale de la cola de verdad, no solo del campo.
        cola = await async_client.get(
            "/api/v1/inventario/productos",
            params={"company_id": str(test_company.id), "solo_sin_verificar": True},
        )
        assert [p["id"] for p in cola.json()] == []

    async def test_dar_de_alta_a_mano_no_entra_en_la_cola(self, async_client, db_session, test_company):
        """El otro camino de la cola: no entrar, en vez de salir.

        Un producto que puso una persona mirando el producto no necesita revision.
        Es lo que `POST /inventario/productos` ya hacia y lo que `verificado`
        distingue de lo que salio de un papel.
        """
        r = await async_client.post(
            "/api/v1/inventario/productos",
            params={"company_id": str(test_company.id)},
            json={"nombre": "Cinta de 50mm"},
        )
        assert r.status_code == 201, r.text
        cuerpo = r.json()
        assert cuerpo["origen"] == "MANUAL"
        assert cuerpo["verificado"] is True

    async def test_renombrar_recalcula_el_normalizado(self, async_client, db_session, test_company):
        """La operacion que "fusiona" dos variantes del OCR.

        "Reginen de" y "Regin de" son dos filas con dos stocks. Quien mira el papel
        decide que son el mismo y renombra; con el `@validates` del modelo, la
        columna normalizada se recalcula sola y el siguiente reescaneo no crea un
        tercero.
        """
        producto = await _producto(
            db_session, test_company, "Reginen de", origen=ProductoOrigen.OCR.value
        )

        r = await async_client.patch(
            f"/api/v1/inventario/productos/{producto.id}",
            params={"company_id": str(test_company.id)},
            json={"nombre": "Regina de"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["nombre"] == "Regina de"

        await db_session.refresh(producto)
        assert producto.nombre_normalizado == "regina de"

    async def test_renombrar_no_toca_el_resto(self, async_client, db_session, test_company):
        """`exclude_unset`: mandar solo `nombre` no borra el precio ni el codigo.

        Es el fallo que hace `model_dump()` sin `exclude_unset`: en los dos casos
        trae la clave, y el servicio no puede saber que el cliente no quiso
        mandarlo. Un 200 llegaria igual y el precio habria desaparecido.
        """
        producto = await _producto(
            db_session, test_company, "Con precio", precio_referencia=Decimal("45.00")
        )

        r = await async_client.patch(
            f"/api/v1/inventario/productos/{producto.id}",
            params={"company_id": str(test_company.id)},
            json={"nombre": "Otro nombre"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["precio_referencia"] == "45.00"

    async def test_un_campo_que_no_se_puede_cambiar_da_422(self, async_client, db_session, test_company):
        """`stock` se lee pero no se escribe: es la suma del kardex.

        Aceptarlo en silencio seria prometer una escritura que no ocurre, y el
        cliente se iria creyendo que fijo el stock.
        """
        producto = await _producto(db_session, test_company, "Normal")

        r = await async_client.patch(
            f"/api/v1/inventario/productos/{producto.id}",
            params={"company_id": str(test_company.id)},
            json={"stock": 999},
        )
        assert r.status_code == 422

    async def test_el_origen_no_se_puede_forjar(self, async_client, db_session, test_company):
        """Un producto de OCR no puede declararse MANUAL para salir de la cola.

        `origen` no esta en el schema, asi que ni siquiera se puede intentar por el
        camino normal. Y si estuviera, declararlo MANUAL sin codigo seria afirmar
        que una persona lo puso mirando el producto.
        """
        producto = await _producto(
            db_session,
            test_company,
            "Del papel",
            origen=ProductoOrigen.OCR.value,
            verificado=False,
        )

        r = await async_client.patch(
            f"/api/v1/inventario/productos/{producto.id}",
            params={"company_id": str(test_company.id)},
            json={"origen": "MANUAL", "verificado": True},
        )
        assert r.status_code == 422

    async def test_verificar_un_ocr_sin_codigo_da_409_con_el_motivo(self, async_client, db_session, test_company):
        """El 409 tiene que decir POR QUE, no solo que no se puede.

        Sin codigo de barras hay una descripcion que el sistema creyo, no una
        identidad. Marcarlo verificado sin que nadie lo mire seria el salto que
        `verificado` existe para no dar.
        """
        producto = await _producto(
            db_session,
            test_company,
            "Cinta sin codigo",
            origen=ProductoOrigen.OCR.value,
            verificado=False,
        )

        r = await async_client.patch(
            f"/api/v1/inventario/productos/{producto.id}",
            params={"company_id": str(test_company.id)},
            json={"verificado": True},
        )
        assert r.status_code == 409
        assert "codigo" in r.json()["detail"].lower()

    async def test_dar_de_baja_no_borra_el_kardex(self, async_client, db_session, test_company):
        """Por que no hay `DELETE` de producto.

        `movimientos_inventario.producto_id` tiene `ON DELETE CASCADE`: borrar el
        producto borra su historial y `stock_de` deja de poder responder por el.
        Dar de baja lo saca del catalogo y lo deja en el kardex.
        """
        producto = await _producto(db_session, test_company, "Con historial")
        await _con_stock(db_session, test_company, producto, "5")

        r = await async_client.patch(
            f"/api/v1/inventario/productos/{producto.id}",
            params={"company_id": str(test_company.id)},
            json={"activo": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["activo"] is False

        movimientos = (
            await db_session.execute(
                select(MovimientoInventarioModel).where(
                    MovimientoInventarioModel.producto_id == producto.id
                )
            )
        ).scalars().all()
        assert len(movimientos) == 1

    async def test_un_producto_de_otra_empresa_da_404(self, async_client, db_session, test_company):
        """No hay multi-tenancy, asi que el `company_id` es lo unico que hay.

        Un 403 confirmaria que hay un producto con ese id en otra empresa.
        """
        otra = test_company.__class__(name="Otra", tax_id=f"OTR-{uuid4().hex[:9].upper()}")
        db_session.add(otra)
        await db_session.commit()
        producto = await _producto(db_session, otra, "De la otra")

        r = await async_client.patch(
            f"/api/v1/inventario/productos/{producto.id}",
            params={"company_id": str(test_company.id)},
            json={"nombre": "Renombrado"},
        )
        assert r.status_code == 404

    async def test_el_detalle_devuelve_el_stock(self, async_client, db_session, test_company):
        """El detalle trae stock, no obliga a una segunda llamada para el mismo numero."""
        producto = await _producto(db_session, test_company, "Con stock")
        await _con_stock(db_session, test_company, producto, "7")

        r = await async_client.get(
            f"/api/v1/inventario/productos/{producto.id}",
            params={"company_id": str(test_company.id)},
        )
        assert r.status_code == 200, r.text
        assert Decimal(r.json()["stock"]) == Decimal("7")


# ---------------------------------------------------------------------------
# El kardex: ventas y ajustes
# ---------------------------------------------------------------------------


class TestRegistrarMovimientos:
    async def test_una_venta_resta_el_stock(self, async_client, db_session, test_company):
        producto = await _producto(db_session, test_company, "Se vende")
        await _con_stock(db_session, test_company, producto, "10")

        r = await async_client.post(
            "/api/v1/inventario/movimientos",
            params={"company_id": str(test_company.id)},
            json={
                "producto_id": str(producto.id),
                "tipo": "SALIDA",
                "cantidad": "4",
            },
        )
        assert r.status_code == 201, r.text
        cuerpo = r.json()
        assert cuerpo["tipo"] == "SALIDA"
        # `referencia_tipo` lo pone el router, no el cliente: pedirlo abriria la
        # puerta a escribir 'COMPRA' a mano.
        assert cuerpo["referencia_tipo"] == "VENTA"

        detalle = await async_client.get(
            f"/api/v1/inventario/productos/{producto.id}",
            params={"company_id": str(test_company.id)},
        )
        assert Decimal(detalle.json()["stock"]) == Decimal("6")

    async def test_un_ajuste_hacia_arriba_suma(self, async_client, db_session, test_company):
        """"Se conto de menos": el ajuste suma, y con `referencia_tipo='AJUSTE'`.

        `es_ajuste=true` es lo que permite una `ENTRADA`. Sin el, la entrada seria
        indistinguible de una compra y el servicio la rechazaria.
        """
        producto = await _producto(db_session, test_company, "Contado de menos")

        r = await async_client.post(
            "/api/v1/inventario/movimientos",
            params={"company_id": str(test_company.id)},
            json={
                "producto_id": str(producto.id),
                "tipo": "ENTRADA",
                "cantidad": "3",
                "es_ajuste": True,
                "descripcion_origen": "conteo fisico",
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["referencia_tipo"] == "AJUSTE"

        detalle = await async_client.get(
            f"/api/v1/inventario/productos/{producto.id}",
            params={"company_id": str(test_company.id)},
        )
        assert Decimal(detalle.json()["stock"]) == Decimal("3")

    async def test_una_entrada_sin_ajuste_da_409(self, async_client, db_session, test_company):
        """LA DEFENSA. Sin esto, el inventario sumaria stock sin compra.

        `compras.ticket_id` UNIQUE es lo que garantiza que el inventario solo
        refleja compras de verdad. Si existiera una via de entrada sin compra, esa
        garantia se acaba y no habria ninguna columna que lo dijera.
        """
        producto = await _producto(db_session, test_company, "Sin compra")

        r = await async_client.post(
            "/api/v1/inventario/movimientos",
            params={"company_id": str(test_company.id)},
            json={"producto_id": str(producto.id), "tipo": "ENTRADA", "cantidad": "50"},
        )
        assert r.status_code == 409
        assert "compra" in r.json()["detail"].lower()

    async def test_sin_stock_suficiente_da_409_con_los_dos_numeros(self, async_client, db_session, test_company):
        """El 409 lleva lo que hay y lo que se pidio.

        Sin los dos numeros no se puede decidir si la cantidad estaba mal o si el
        stock esta mal, que son dos arreglos distintos.
        """
        producto = await _producto(db_session, test_company, "Poco")
        await _con_stock(db_session, test_company, producto, "6")

        r = await async_client.post(
            "/api/v1/inventario/movimientos",
            params={"company_id": str(test_company.id)},
            json={"producto_id": str(producto.id), "tipo": "SALIDA", "cantidad": "10"},
        )
        assert r.status_code == 409
        detalle = r.json()["detail"]
        assert "6" in detalle and "10" in detalle

    async def test_un_actor_falso_no_se_puede_mandar(self, async_client, db_session, test_company):
        """`extra="forbid"`: el `actor` sale del token y no del cuerpo.

        Sin esto, un cliente podria mandar `actor` y `extra="ignore"` lo dejaria
        pasar en silencio, escribiendo en el kardex que lo hizo una persona que no
        fue esa.
        """
        producto = await _producto(db_session, test_company, "Normal")

        r = await async_client.post(
            "/api/v1/inventario/movimientos",
            params={"company_id": str(test_company.id)},
            json={
                "producto_id": str(producto.id),
                "tipo": "SALIDA",
                "cantidad": "1",
                "actor": "ana@empresa.mx",
            },
        )
        assert r.status_code == 422

    async def test_una_cantidad_cero_da_422(self, async_client, db_session, test_company):
        """Un movimiento de cero no es un movimiento: es ruido en el kardex."""
        producto = await _producto(db_session, test_company, "Normal")

        r = await async_client.post(
            "/api/v1/inventario/movimientos",
            params={"company_id": str(test_company.id)},
            json={"producto_id": str(producto.id), "tipo": "SALIDA", "cantidad": "0"},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# El ciclo de la compra
# ---------------------------------------------------------------------------


class TestRechazarYReabrir:
    async def _compra(self, db_session, empresa) -> CompraModel:
        from app.services import inventario_service as inv

        ticket = TicketModel(
            company_id=empresa.id,
            provider_name="PROVEEDOR",
            total_amount=Decimal("4291.50"),
            tax_amount=Decimal("540.00"),
            expense_date=date(2026, 9, 15),
            raw_text="texto",
            extraction_status="APROBADO",
            items=ITEMS,
        )
        db_session.add(ticket)
        await db_session.commit()
        await db_session.refresh(ticket)

        compra = await inv.registrar_compra(db_session, ticket)
        await db_session.commit()
        return compra

    async def test_el_rechazo_no_se_deshace_llamando_a_confirmar(self, async_client, db_session, test_company):
        """LA DEFENSA, y es la que hace que el estado no sea decorativo.

        Escribir RECHAZADO en la base no basta: lo que hace una decision es que
        `confirmar_compra` la respete. Sin ese `if` en el servicio, el rechazo se
        deshacia llamando al endpoint de confirmar —y el 409 de "ya PROCESADO" no
        saltaria, porque en el momento del rechazo la compra todavia no lo esta.
        """
        compra = await self._compra(db_session, test_company)

        r = await async_client.post(
            f"/api/v1/inventario/compras/{compra.id}/rechazar", json={"motivo": "no es una compra"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["estado"] == "RECHAZADO"

        confirmar = await async_client.post(
            f"/api/v1/inventario/compras/{compra.id}/confirmar", json={}
        )
        assert confirmar.status_code == 409

        stocks = await async_client.get(
            "/api/v1/inventario/movimientos", params={"company_id": str(test_company.id)}
        )
        assert stocks.json() == []

    async def test_el_motivo_no_puede_firmar_por_otro(self, async_client, db_session, test_company):
        """Quien rechaza sale del token. Un `rechazada_por` en el body seria una puerta."""
        compra = await self._compra(db_session, test_company)

        r = await async_client.post(
            f"/api/v1/inventario/compras/{compra.id}/rechazar",
            json={"rechazada_por": "jefe@empresa.mx"},
        )
        assert r.status_code == 422

    async def test_rechazar_dos_veces_no_falla(self, async_client, db_session, test_company):
        """Idempotente: un cliente que reintenta tras un timeout no recibe un error."""
        compra = await self._compra(db_session, test_company)
        url = f"/api/v1/inventario/compras/{compra.id}/rechazar"

        assert (await async_client.post(url, json={})).status_code == 200
        segundo = await async_client.post(url, json={})
        assert segundo.status_code == 200
        assert segundo.json()["estado"] == "RECHAZADO"

    async def test_una_compra_procesada_no_se_puede_rechazar(self, async_client, db_session, test_company):
        """El 409 dice cual es la via, porque la hay: el AJUSTE."""
        compra = await self._compra(db_session, test_company)
        confirmar = await async_client.post(
            f"/api/v1/inventario/compras/{compra.id}/confirmar", json={}
        )
        assert confirmar.status_code == 200, confirmar.text

        r = await async_client.post(
            f"/api/v1/inventario/compras/{compra.id}/rechazar", json={}
        )
        assert r.status_code == 409
        assert "AJUSTE" in r.json()["detail"]

    async def test_reabrir_devuelve_la_compra_a_revision(self, async_client, db_session, test_company):
        compra = await self._compra(db_session, test_company)
        await async_client.post(f"/api/v1/inventario/compras/{compra.id}/rechazar", json={})

        r = await async_client.post(f"/api/v1/inventario/compras/{compra.id}/reabrir")
        assert r.status_code == 200, r.text
        assert r.json()["estado"] == "EN_REVISION"

    async def test_reabrir_y_confirmar_deja_el_stock(self, async_client, db_session, test_company):
        """El ciclo entero: rechazar, arrepentirse, autorizar."""
        compra = await self._compra(db_session, test_company)
        await async_client.post(f"/api/v1/inventario/compras/{compra.id}/rechazar", json={})
        await async_client.post(f"/api/v1/inventario/compras/{compra.id}/reabrir")

        confirmar = await async_client.post(
            f"/api/v1/inventario/compras/{compra.id}/confirmar", json={}
        )
        assert confirmar.status_code == 200, confirmar.text

        movimientos = await async_client.get(
            "/api/v1/inventario/movimientos", params={"company_id": str(test_company.id)}
        )
        assert len(movimientos.json()) == 2

    async def test_reabrir_una_compra_procesada_da_409(self, async_client, db_session, test_company):
        """Reabrir y deshacer un rechazo son cosas distintas, y se separan.

        Un unico endpoint que aceptara las dos necesitaria adivinar cual es, y el
        error —dar por reversible una compra que ya movio stock— no tiene salida.
        """
        compra = await self._compra(db_session, test_company)
        await async_client.post(f"/api/v1/inventario/compras/{compra.id}/confirmar", json={})

        r = await async_client.post(f"/api/v1/inventario/compras/{compra.id}/reabrir")
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# Las cuentas
# ---------------------------------------------------------------------------


class TestAdministrarCuentas:
    async def test_listar_no_saca_el_hash(self, async_client):
        """`UsuarioResponse` no declara `password_hash`, y con eso no sale.

        Es el motivo de que la forma de salida se escriba a mano en vez de exponer
        el modelo entero: un campo que no esta declarado, no sale.
        """
        r = await async_client.get("/api/v1/usuarios")
        assert r.status_code == 200, r.text
        assert len(r.json()) >= 1
        for cuenta in r.json():
            assert "password_hash" not in cuenta
            assert "password" not in cuenta

    async def test_el_detalle_tampoco(self, async_client, usuario_de_prueba):
        r = await async_client.get(f"/api/v1/usuarios/{usuario_de_prueba.id}")
        assert r.status_code == 200
        assert "password_hash" not in r.json()

    async def test_dar_de_baja_necesita_la_contrasena(self, async_client, usuario_de_prueba):
        """Un token robado no basta para cerrar cuentas ajenas.

        `UsuarioVerificado` exige el header `X-Contrasena-Actual`. Sin el, el
        atacante tendria el sistema entero y no necesitaria ni la contrasena para
        dejar sin puerta de entrada a la instalacion.
        """
        r = await async_client.patch(
            f"/api/v1/usuarios/{usuario_de_prueba.id}", json={"is_active": False}
        )
        assert r.status_code == 401

    async def test_una_contrasena_equivocada_no_da_de_baja(self, async_client, usuario_de_prueba):
        r = await async_client.patch(
            f"/api/v1/usuarios/{usuario_de_prueba.id}",
            json={"is_active": False},
            headers={"X-Contrasena-Actual": "no-es-la-contrasena"},
        )
        assert r.status_code == 401

    async def test_dar_de_baja_con_la_contrasena_correcta(self, async_client, db_session, usuario_de_prueba):
        """Renombrar y dar de baja, que es lo que se hace en la practica.

        Hace falta una segunda cuenta activa: con una sola, el endpoint da 409 (ver
        `test_la_ultima_cuenta_activa_no_se_puede_dar_de_baja`), y ese 409 es
        correcto.
        """
        from app.core.security import hashear_contrasena
        from app.models.user import UserModel

        otra = UserModel(
            email=f"ayudante{uuid4().hex[:8]}@test.local",
            nombre="Ayudante",
            password_hash=hashear_contrasena(CONTRASENA),
        )
        db_session.add(otra)
        await db_session.commit()
        await db_session.refresh(otra)

        r = await async_client.patch(
            f"/api/v1/usuarios/{otra.id}",
            json={"nombre": "Ana Renombrada", "is_active": False},
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["nombre"] == "Ana Renombrada"
        assert cuerpo["is_active"] is False

    async def test_renombrar_la_propia_cuenta_no_necesita_otra_contrasena(self, async_client, usuario_de_prueba):
        """Renombrarse no deja a nadie fuera: se puede con la propia contrasena.

        El header sigue haciendo falta —es la prueba de que no hay un token
        robado— pero no hace falta conocer la contrasena de OTRA cuenta para
        cambiarte el nombre a ti mismo.
        """
        r = await async_client.patch(
            f"/api/v1/usuarios/{usuario_de_prueba.id}",
            json={"nombre": "Operador Renombrado"},
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 200, r.text
        assert r.json()["nombre"] == "Operador Renombrado"

    async def test_la_ultima_cuenta_activa_no_se_puede_dar_de_baja(self, async_client, usuario_de_prueba):
        """Si se pudiera, el sistema se queda sin puerta de entrada.

        La unica recuperacion seria entrar a Postgres a mano. Con una sola cuenta
        —que es como se instala esto— esto bloquea el escenario de dejar la
        instalacion inutilizable desde la propia aplicacion.
        """
        otros = await async_client.get(
            "/api/v1/usuarios", params={"solo_activas": True}
        )
        assert len(otros.json()) == 1

        r = await async_client.patch(
            f"/api/v1/usuarios/{usuario_de_prueba.id}",
            json={"is_active": False},
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 409
        assert "ultima cuenta" in r.json()["detail"]

    async def test_cambiar_la_contrasena_necesita_la_actual(self, async_client, usuario_de_prueba):
        """La segunda puerta: la del CUERPO, distinta de la del HEADER.

        El header dice "quien eres tu"; este campo dice "de quien es esta cuenta".
        Con una sola, un atacante con un token robado podria rotar la contrasena de
        cualquiera y el dueno ya no podria recuperarla.
        """
        r = await async_client.post(
            f"/api/v1/usuarios/{usuario_de_prueba.id}/contrasena",
            json={"contrasena_actual": "equivocada", "contrasena_nueva": "nueva-clave-123"},
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 400

    async def test_cambiar_la_contrasena_rota_de_verdad(self, async_client, usuario_de_prueba, db_session):
        from app.core.security import hashear_contrasena, verificar_contrasena

        r = await async_client.post(
            f"/api/v1/usuarios/{usuario_de_prueba.id}/contrasena",
            json={"contrasena_actual": CONTRASENA, "contrasena_nueva": "nueva-clave-123"},
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 204, r.text

        await db_session.refresh(usuario_de_prueba)
        assert verificar_contrasena("nueva-clave-123", usuario_de_prueba.password_hash)
        assert not verificar_contrasena(CONTRASENA, usuario_de_prueba.password_hash)

    async def test_no_se_puede_cambiar_el_correo(self, async_client, usuario_de_prueba):
        """El correo va dentro de la clave del rate limit del login.

        Cambiarlo deja de contar los intentos de fuerza bruta de la cuenta
        anterior. Es un detalle de implementacion del rate limit que se vuelve una
        decision de API.
        """
        r = await async_client.patch(
            f"/api/v1/usuarios/{usuario_de_prueba.id}",
            json={"email": "otro@test.local"},
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 422

    async def test_no_se_puede_poner_un_hash_directo(self, async_client, usuario_de_prueba):
        """Un `PATCH {"password_hash": "..."}` aceptaria un hash cualquiera.

        Sin verificar la contrasena de nadie: cambiar una clave por la via que no
        comprueba nada deja de ser cambiar una clave.
        """
        r = await async_client.patch(
            f"/api/v1/usuarios/{usuario_de_prueba.id}",
            json={"password_hash": "scrypt-v1$...$inventado"},
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 422

    async def test_la_contrasena_nueva_no_puede_ser_la_actual(self, async_client, usuario_de_prueba):
        """Si no, el endpoint responde 204 y no cambia nada.

        Quien lo pidio se va creyendo que la rotacion funciono.
        """
        r = await async_client.post(
            f"/api/v1/usuarios/{usuario_de_prueba.id}/contrasena",
            json={"contrasena_actual": CONTRASENA, "contrasena_nueva": CONTRASENA},
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 422

    async def test_sin_token_no_se_administra_nada(self, async_client_sin_autenticar, usuario_de_prueba):
        """`async_client_sin_autenticar` NO sobrescribe `get_current_user`.

        Con `async_client` todos los tests de seguridad estarian probando el
        override, que siempre deja pasar, y no el codigo que decide.
        """
        r = await async_client_sin_autenticar.get("/api/v1/usuarios")
        assert r.status_code == 401

        r = await async_client_sin_autenticar.patch(
            f"/api/v1/usuarios/{usuario_de_prueba.id}", json={"is_active": False}
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Mapeos contables y conciliaciones
# ---------------------------------------------------------------------------


class TestMapeosContables:
    async def test_crear_actualizar_y_borrar(self, async_client, test_company):
        """El CRUD que faltaba y que no tiene implicacion de negocio.

        Un mapeo es un diccionario de traduccion de columnas, no una fila de la que
        se deriven saldos. Por eso el borrado es definitivo y no una baja logica: no
        hay historial que conservar, y guardarlo anadiria una columna y una
        condicion al unico consumidor.
        """
        creado = await async_client.post(
            "/api/v1/reconciliations/mappings",
            json={
                "company_id": str(test_company.id),
                "software_name": "CONTPAQI",
                "column_mappings": {"cuenta": "poliza"},
            },
        )
        assert creado.status_code == 201, creado.text
        mapping_id = creado.json()["id"]

        actualizado = await async_client.patch(
            f"/api/v1/reconciliations/mappings/{mapping_id}",
            json={"software_name": "CONTPAQI 2026"},
        )
        assert actualizado.status_code == 200, actualizado.text
        assert actualizado.json()["software_name"] == "CONTPAQI 2026"
        # `exclude_unset`: mandar solo el nombre no borra el diccionario, que es
        # el cuerpo del mapeo.
        assert actualizado.json()["column_mappings"] == {"cuenta": "poliza"}

        borrado = await async_client.delete(
            f"/api/v1/reconciliations/mappings/{mapping_id}"
        )
        assert borrado.status_code == 204

        assert (
            await async_client.get(f"/api/v1/reconciliations/mappings/{mapping_id}")
        ).status_code == 404

    async def test_borrar_un_mapeo_no_toca_el_historico(self, async_client, db_session, test_company):
        """Lo que el borrado NO borra es lo que importa.

        Tickets, movimientos y conciliaciones no lo referencian: por eso el borrado
        no toca el historico contable y por eso es seguro.
        """
        async_client_crea = await async_client.post(
            "/api/v1/reconciliations/mappings",
            json={
                "company_id": str(test_company.id),
                "software_name": "Temporal",
                "column_mappings": {},
            },
        )
        ticket = TicketModel(
            company_id=test_company.id,
            provider_name="PROVEEDOR",
            total_amount=Decimal("100.00"),
            tax_amount=Decimal("16.00"),
            expense_date=date(2026, 9, 15),
            raw_text="texto",
            extraction_status="APROBADO",
        )
        db_session.add(ticket)
        await db_session.commit()
        await db_session.refresh(ticket)

        await async_client.delete(
            f"/api/v1/reconciliations/mappings/{async_client_crea.json()['id']}"
        )

        await db_session.refresh(ticket)
        assert ticket.total_amount == Decimal("100.00")


class TestRevisarConciliacion:
    async def _conciliacion(self, db_session, empresa) -> ReconciliationModel:
        ticket = TicketModel(
            company_id=empresa.id,
            provider_name="PROVEEDOR",
            total_amount=Decimal("100.00"),
            tax_amount=Decimal("16.00"),
            expense_date=date(2026, 9, 15),
            raw_text="texto",
            extraction_status="APROBADO",
        )
        db_session.add(ticket)
        await db_session.commit()
        await db_session.refresh(ticket)

        conciliacion = ReconciliationModel(
            ticket_id=ticket.id, match_status="DISCREPANCY"
        )
        db_session.add(conciliacion)
        await db_session.commit()
        await db_session.refresh(conciliacion)
        return conciliacion

    async def test_corregir_a_mano_deja_rastro_de_quien(self, async_client, db_session, test_company, usuario_de_prueba):
        """LA DEFENSA. Sin `revisado_por`, la correccion es invisible.

        `match_status` decide que se exporta (`MATCHED_STATUSES`), asi que un
        `PERFECT` que aprobo una persona y uno que produjo el motor son la misma
        fila en la base. Es el mismo motivo por el que existe
        `confidence_source`: "lo automatico" y "una persona" no se suman.
        """
        conciliacion = await self._conciliacion(db_session, test_company)

        r = await async_client.patch(
            f"/api/v1/reconciliations/{conciliacion.id}",
            json={"match_status": "PERFECT"},
        )
        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["match_status"] == "PERFECT"
        assert cuerpo["revisado_por"] == usuario_de_prueba.email
        assert cuerpo["revisado_at"] is not None

    async def test_no_se_puede_forjar_quien_reviso(self, async_client, db_session, test_company):
        """`revisado_por` sale del token. Aceptarlo en el body seria firmarlo por otro."""
        conciliacion = await self._conciliacion(db_session, test_company)

        r = await async_client.patch(
            f"/api/v1/reconciliations/{conciliacion.id}",
            json={"match_status": "PERFECT", "revisado_por": "jefe@empresa.mx"},
        )
        assert r.status_code == 422

    async def test_no_se_puede_mover_el_emparejamiento_por_este_endpoint(self, async_client, db_session, test_company):
        """Un emparejamiento equivocado se corrige con `DELETE` + `POST`.

        Cambiar el estado y cambiar los dos lados son afirmaciones distintas, y una
        sola columna no puede decir cual de las dos se toco.
        """
        conciliacion = await self._conciliacion(db_session, test_company)

        r = await async_client.patch(
            f"/api/v1/reconciliations/{conciliacion.id}",
            json={"match_status": "PERFECT", "ticket_id": str(uuid4())},
        )
        assert r.status_code == 422

    async def test_un_estado_inventado_da_422(self, async_client, db_session, test_company):
        """El patron sale del enum, no de una cadena escrita a mano."""
        conciliacion = await self._conciliacion(db_session, test_company)

        r = await async_client.patch(
            f"/api/v1/reconciliations/{conciliacion.id}",
            json={"match_status": "CUALQUIER_COSA"},
        )
        assert r.status_code == 422

    async def test_una_conciliacion_sin_revisar_no_trae_autor(self, async_client, db_session, test_company):
        """`NULL` es el hecho "lo puso el motor y nadie lo ha tocado".

        `''` seria "alguien lo toco y no lo dijo", que es peor que no tenerlo.
        """
        conciliacion = await self._conciliacion(db_session, test_company)

        r = await async_client.get(f"/api/v1/reconciliations/{conciliacion.id}")
        assert r.status_code == 200, r.text
        assert r.json()["revisado_por"] is None
        assert r.json()["revisado_at"] is None
