"""Lo que el escaneo devuelve de cada ticket, y que un `Decimal` no se pierda.

Dos cosas se comprueban aqui, y las dos son la razon de que este archivo exista:

1. `POST /scan` devuelve los DATOS de cada ticket, no solo el metadata del
   archivo. Antes habia que pedir un `GET /tickets/{id}` por cada archivo de la
   corrida, y el que armaba el JSON tenía que cruzar N respuestas y pegar los
   campos a mano.

2. Las LINEAS del comprobante se pueden guardar. `items` es una columna JSON y el
   LLM mete `Decimal` en cantidad, precio e importe; con `JSON` a secas el INSERT
   revienta y el comprobante se pierde como `accion=ERROR`. Es el bug mas caro de
   los dos para el inventario, porque solo aparece con facturas que TIENEN
   detalle de partidas: las que no, se guardan bien y el camino parece sano.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.enums import ScanStatus
from app.models.ticket import TicketModel
from app.services import scan_service

COMPROBANTE = """Tiendas Ramirez SA de CV
RFC: RAX1109138P6
Ticket: 0001
Fecha: 2026-03-15
Subtotal: 100.00
IVA: 16.00
Total: 116.00
"""


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    """Una carpeta de tickets de verdad, apuntada por `TICKETS_INPUT_DIR`.

    Es el mismo aislamiento que el de `test_scan_service.py`, y con el mismo
    motivo: `settings.TICKETS_INPUT_DIR` lo lee el servicio en cada llamada, y
    las dos rutas —la de entrada y la de escaneados— tienen que quedar dentro de
    `tmp_path` o `carpeta_de_escaneados()` escribe contra el sistema de archivos
    de verdad.
    """
    from app.core.config import settings

    destino = tmp_path / "tickets"
    destino.mkdir()
    monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(destino))
    monkeypatch.setattr(settings, "TICKETS_SCAN_OUTPUT_DIR", str(tmp_path / "escaneados"))
    monkeypatch.setattr(settings, "TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR", False)
    return destino


def _pdf_con_texto(monkeypatch, texto: str):
    """Fuerza la rama de PDF con texto, sin modelo y sin OCR."""
    from app.services import capture

    monkeypatch.setattr(capture, "extract_pdf_text", lambda _bytes: texto)


# ---------------------------------------------------------------------------
# 1. El JSON con los datos de cada ticket
# ---------------------------------------------------------------------------


class TestElEscaneoDevuelveLosDatosDelTicket:

    async def test_trae_el_proveedor_el_total_y_la_fecha(
        self, db_session, test_company, carpeta, monkeypatch
    ):
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "datos.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        assert resumen.nuevos == 1
        datos = resumen.detalles[0].datos
        assert datos is not None, "el detalle tiene que traer los datos del ticket"
        assert datos["provider_name"] == "Tiendas Ramirez SA de CV"
        assert datos["provider_tax_id"] == "RAX1109138P6"
        assert datos["total_amount"] == Decimal("116.00")
        assert datos["subtotal"] == Decimal("100.00")
        assert datos["tax_amount"] == Decimal("16.00")
        assert datos["expense_date"] == date(2026, 3, 15)

    async def test_trae_el_veredicto_al_lado_de_los_datos(
        self, db_session, test_company, carpeta, monkeypatch
    ):
        """El dato sin el veredicto es una afirmacion que el sistema no hizo.

        Si el gate mando la lectura a revision, un JSON con los numeros pero sin
        `extraction_status` deja a quien lo consume creyendo que el sistema
        responde por ellos. Por eso van en el MISMO objeto y no en dos campos
        sueltos que se pueden leer por separado.
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "veredicto.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        datos = resumen.detalles[0].datos
        assert datos is not None
        for campo in ("extraction_status", "confidence", "confidence_source"):
            assert campo in datos, f"el veredicto {campo} tiene que viajar con los datos"
        # Y tiene que ser el veredicto REAL del gate, no uno inventado aqui.
        assert datos["extraction_status"] in {
            "AUTO_APROBADO", "REQUIERE_REVISION", "PENDIENTE", "APROBADO", "RECHAZADO",
        }

    async def test_los_datos_vienen_del_ticket_y_no_de_la_lectura_cruda(
        self, db_session, test_company, carpeta, monkeypatch
    ):
        """Corrige el ticket a mano y el JSON tiene que reflejarlo.

        Si `datos` se armara con lo que devolvio el lector, esto seguiria
        marcando el total viejo despues de que alguien lo corrigio, y el JSON
        seria una segunda version de la verdad, mas degradada que la primera.
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "corregido.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        await scan_service.escanear(db_session, test_company.id, "ana")

        ticket = (await db_session.execute(_todos_los_tickets())).scalar_one()
        ticket.total_amount = Decimal("999.00")
        await db_session.commit()

        # La segunda pasada no relee —el contenido no cambio— pero igual tiene que
        # reportar el total corregido. Por eso los datos se resuelven al final de
        # la corrida y no se dejan puestos en el momento de la lectura.
        resumen = await scan_service.escanear(db_session, test_company.id, "ana")
        assert resumen.sin_cambios == 1
        assert resumen.detalles[0].datos["total_amount"] == Decimal("999.00")

    async def test_un_archivo_sin_ticket_no_inventa_datos(
        self, db_session, test_company, carpeta
    ):
        """`datos=None` cuando no hay ticket, y no un objeto de campos vacios.

        Un objeto lleno de `None` es PEOR que un `null`: parece que el sistema leyo
        el comprobante y no encontro nada, cuando lo que paso es que no hay ticket
        del que sacar datos. Son dos cosas que piden acciones distintas.
        """
        (carpeta / "basura.xyz").write_bytes(b"no es un comprobante")

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        assert len(resumen.detalles) == 1
        assert resumen.detalles[0].ticket_id is None
        assert resumen.detalles[0].datos is None

    async def test_un_duplicado_reporta_los_datos_del_ticket_original(
        self, db_session, test_company, carpeta, monkeypatch
    ):
        """El DUPLICADO no crea ticket, pero el que ya existe tiene datos.

        Dos archivos con los mismos bytes dan un ticket y un puntero. Quien lee la
        corrida quiere ver los datos una vez, no dos con el segundo en blanco: un
        `null` en el segundo es indistinguible de "este archivo no se pudo leer",
        y el operador no puede saber que solo es una copia.
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "original.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())
        (carpeta / "copia.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        duplicados = [d for d in resumen.detalles if d.status is ScanStatus.DUPLICADO]
        assert len(duplicados) == 1
        assert duplicados[0].datos is not None
        assert duplicados[0].datos["provider_name"] == "Tiendas Ramirez SA de CV"

    async def test_el_json_no_arrastra_el_texto_crudo(
        self, db_session, test_company, carpeta, monkeypatch
    ):
        """`raw_text` no va en la respuesta.

        Son hasta 20 000 caracteres por ticket y no es un dato: es la evidencia.
        Meterlo aqui multiplica el tamano de la respuesta sin agregar un solo campo
        estructurado, y quien lo necesite lo pide con `GET /tickets/{id}`, que
        ademas lo sirve con el documento al lado.
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE + ("X" * 30_000))
        (carpeta / "largo.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        assert "raw_text" not in resumen.detalles[0].datos


def _todos_los_tickets():
    from sqlalchemy import select

    return select(TicketModel)


# ---------------------------------------------------------------------------
# 2. Las lineas con Decimal se pueden guardar
# ---------------------------------------------------------------------------


class TestLasLineasConDecimalSeGuardan:

    def _lineas(self):
        """Lineas como las arma `ai_extractor`: con `Decimal`, no con `float`."""
        return [
            {
                "description": "TORTA DE PASTOR",
                "quantity": Decimal("1"),
                "unit_price": Decimal("85.00"),
                "total": Decimal("85.00"),
            },
            {
                "description": "REFRESCO COCA 600ML",
                "quantity": Decimal("2"),
                "unit_price": Decimal("35.00"),
                "total": Decimal("70.00"),
            },
        ]

    async def test_una_factura_con_partidas_se_guarda(
        self, db_session, test_company
    ):
        """El commit ES la prueba: sin `JSONConDecimal` esto revienta.

        El sintoma real medido en el escaner era
        "Object of type Decimal is not JSON serializable", y `procesar_archivo`
        lo reportaba como `accion=ERROR` con `datos=null`: el comprobante se
        perdia y nada decia por que.
        """
        ticket = TicketModel(
            company_id=test_company.id,
            provider_name="Mercado Local",
            total_amount=Decimal("229.68"),
            expense_date=date(2026, 3, 15),
            raw_text="x",
            items=self._lineas(),
        )
        db_session.add(ticket)
        await db_session.commit()

        guardado = (await db_session.execute(_todos_los_tickets())).scalar_one()
        assert guardado.items is not None
        assert len(guardado.items) == 2
        assert guardado.items[0]["description"] == "TORTA DE PASTOR"

    async def test_las_lineas_se_vuelven_a_leer_como_decimal(
        self, db_session, test_company
    ):
        """Un importe guardado no pierde exactitud al releerlo.

        Si se guardara como `float`, `0.1 + 0.2` no daria `0.3` y la aritmetica que
        decide si una compra cuadra con su total fallaria por centavos. Con texto,
        `"12.50"` vuelve a `Decimal("12.50")` sin haber pasado por binario.
        """
        from app.services.inventario_service import interpretar_items

        ticket = TicketModel(
            company_id=test_company.id,
            provider_name="Mercado Local",
            total_amount=Decimal("229.68"),
            expense_date=date(2026, 3, 15),
            raw_text="x",
            items=self._lineas(),
        )
        db_session.add(ticket)
        await db_session.commit()

        guardado = (await db_session.execute(_todos_los_tickets())).scalar_one()
        lineas, inservibles = interpretar_items(guardado.items)

        assert inservibles == []
        assert len(lineas) == 2
        # El importe que decide si la compra cuadra sigue siendo exacto.
        assert sum((l["total"] for l in lineas), Decimal("0")) == Decimal("155.00")

    def test_un_decimal_anidado_dentro_de_una_lista_tambien_se_convierte(self):
        """La recursion tiene que llegar a dos niveles: `items` es `list[dict]`.

        Un recorrido de un solo nivel dejaria el `Decimal` dentro de la linea y el
        INSERT seguiria reventando, que es el bug original con un caso mas
        estrecho: dificil de ver y facil de reintroducir.
        """
        from app.core.json_decimal import _sin_decimal

        entrada = {"grupos": [{"lineas": [{"total": Decimal("5.00")}]}]}
        assert _sin_decimal(entrada) == {"grupos": [{"lineas": [{"total": "5.00"}]}]}

    def test_no_se_tocan_los_floats_ni_los_demas_tipos(self):
        """Solo se convierte `Decimal`.

        Un `float` que ya esta en la columna se deja como estaba: convertirlo a
        texto cambiaria el formato de datos que hoy funcionan, y no es el problema
        que este modulo viene a arreglar.
        """
        from app.core.json_decimal import _sin_decimal

        assert _sin_decimal({"f": 1.5, "i": 2, "s": "x", "b": True, "n": None}) == {
            "f": 1.5, "i": 2, "s": "x", "b": True, "n": None,
        }

    def test_none_sigue_siendo_none_y_no_el_literal_json_null(self):
        """`items=None` tiene que seguir siendo NULL en la base.

        `none_as_null=True` es lo que hace que `WHERE items IS NULL` funcione, y la
        columna lo promete explicitamente (`models/ticket.py:145-157`). Si el
        `TypeDecorator` se comiera ese `None`, la consulta devolveria la fila y el
        bug seria silencioso otra vez.
        """
        from app.core.json_decimal import _sin_decimal

        assert _sin_decimal(None) is None

    def test_una_lista_vacia_sigue_siendo_lista_vacia(self):
        """`items=[]` y `items=None` significan cosas distintas y no se mezclan.

        `[]` es "el lector produjo lineas y no eran ninguna"; `None` es "el lector
        no produjo lineas", que es el caso normal de la ruta OCR. Confundirlas
        hace que un comprobante sin detalle parezca un comprobante que no se leyo.
        """
        from app.core.json_decimal import _sin_decimal

        assert _sin_decimal([]) == []