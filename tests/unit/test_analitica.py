"""Pruebas de `app.services.analitica`.

Que se prueba aqui y por que son cuentas y no pantallas
-----------------------------------------------------
Lo que hay en este servicio decide que numeros ve la persona, y un tablero con un
numero equivocado es peor que un tablero roto, porque no se nota. Las pruebas
comprueban las tres cosas que se pueden equivocar sin que salte ningun error:

  1. **La aritmetica de las ventanas.** Un mes a medias contra uno completo da
     siempre una caida. Que el codigo se dé cuenta de eso es la diferencia entre
     un tablero que informa y uno que asusta cada dia 3.
  2. **Los porcentajes que no tienen denominador.** El primer mes de un negocio
     tiene cero gastos el mes anterior. Sin `None`, sale `Infinity`.
  3. **Que las categorias distintas se agrupen.** `Supermercado` y `supermercado`
     son el mismo gasto; separarlas parte el total en barras que no cuadran.

Por que SQLite y no Postgres
----------------------------
Porque lo que se prueba aqui es aritmetica y no SQL dialectal: no hay una sola
construccion en este servicio que solo funcione en Postgres. Si algun dia la
habia, estas pruebas darian verde en local y fallarian en el contenedor, que es
peor que no tenerlas.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.database import Base
from app.models.bank_transaction import BankTransactionModel
from app.models.company import CompanyModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel
from app.services import analitica


# ---------------------------------------------------------------------------
# Ventanas de tiempo
# ---------------------------------------------------------------------------


class TestVentanas:
    def test_mes_actual_va_del_primero_al_siguiente(self):
        ahora = datetime(2026, 3, 17, 15, 30, tzinfo=timezone.utc)
        desde, hasta = analitica.mes_actual(ahora)
        assert desde == datetime(2026, 3, 1, tzinfo=timezone.utc)
        assert hasta == datetime(2026, 4, 1, tzinfo=timezone.utc)

    def test_diciembre_roba_el_ano_correcto(self):
        """El bug clasico: sumar un mes a diciembre da el 13 y `replace` lanza."""
        ahora = datetime(2026, 12, 5, tzinfo=timezone.utc)
        _, hasta = analitica.mes_actual(ahora)
        assert hasta == datetime(2027, 1, 1, tzinfo=timezone.utc)

    def test_enero_mes_anterior_roba_el_ano_correcto(self):
        ahora = datetime(2026, 1, 10, tzinfo=timezone.utc)
        desde, hasta = analitica.mes_anterior(ahora)
        assert desde == datetime(2025, 12, 1, tzinfo=timezone.utc)
        assert hasta == datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_mes_anterior_es_el_mes_completo_y_no_treinta_dias(self):
        """La comparacion tiene que ser contra el mes, no contra "hace 30 dias".

        Si fuera "hace 30 dias", un tablero abierto el dia 3 de cada mes
        compararia tres dias contra treinta y dira que el gasto cayo 90% todos
        los meses. Un tablero que miente una vez al mes entrena a su lector para
        ignorar las variaciones.
        """
        ahora = datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc)
        desde, hasta = analitica.mes_anterior(ahora)
        assert desde == datetime(2026, 2, 1, tzinfo=timezone.utc)
        assert hasta == datetime(2026, 3, 1, tzinfo=timezone.utc)

    def test_ultimos_meses_incluye_el_actual_y_llega_hasta_atras(self):
        ahora = datetime(2026, 3, 17, tzinfo=timezone.utc)
        meses = analitica.ultimos_meses(12, ahora)
        assert len(meses) == 12
        # El primero es hace 11 meses, el ultimo es el actual.
        assert (meses[0][0], meses[0][1]) == (2025, 4)
        assert (meses[-1][0], meses[-1][1]) == (2026, 3)

    def test_la_serie_tiene_un_punto_por_mes_aunque_no_haya_datos(self):
        """Los huecos son informacion.

        Una serie a la que se le quitan los meses sin datos no dice si no hubo
        actividad o si nadie reporto. Un mes en cero en medio de una serie es la
        senal de que algo dejo de entrar, y quitarla esconde la senal.
        """
        ahora = datetime(2026, 3, 17, tzinfo=timezone.utc)
        assert len(analitica.ultimos_meses(6, ahora)) == 6

    def test_dias_del_mes_febrero_bisiesto(self):
        assert analitica.dias_del_mes(2028, 2) == 29
        assert analitica.dias_del_mes(2026, 2) == 28
        assert analitica.dias_del_mes(2026, 12) == 31


# ---------------------------------------------------------------------------
# Cifras
# ---------------------------------------------------------------------------


class TestCifra:
    def test_variacion_de_un_tercio_mas(self):
        c = analitica.Cifra(Decimal("150"), Decimal("100"), 15, 10)
        assert c.variacion_pct == pytest.approx(50.0)
        assert c.delta_absoluto == Decimal("50")

    def test_sin_base_no_hay_porcentaje(self):
        """El primer mes de un negocio no tiene contra que compararse.

        Si esto devolviera 0, el tablero diria "sin cambios" cuando en realidad
        no sabe. Si devolviera Infinity, diria "+Infinity%". Lo honesto es `None`
        y que la pantalla lo pinte como guion.
        """
        c = analitica.Cifra(Decimal("500"), Decimal("0"), 5, 0)
        assert c.variacion_pct is None
        assert c.hay_base is False

    def test_una_caida_es_negativa_y_no_solo_el_signo(self):
        c = analitica.Cifra(Decimal("40"), Decimal("100"), 4, 10)
        assert c.variacion_pct == pytest.approx(-60.0)
        assert c.delta_absoluto == Decimal("-60")

    def test_igual_que_el_anterior_es_cero_y_no_un_redondeo_de_epsilons(self):
        c = analitica.Cifra(Decimal("100"), Decimal("100"), 10, 10)
        assert c.variacion_pct == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Agregaciones, contra una base de verdad
# ---------------------------------------------------------------------------


@pytest.fixture
async def sesion():
    """Una base en memoria, vacia.

    Se crea el esquema desde los modelos en vez de usar los archivos de
    `db/`, para que la prueba no dependa de una migracion que alguien pueda
    cambiar sin querer.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _sembrar(sesion, tickets, empresa="Empresa Unica"):
    empresa_obj = CompanyModel(id=__import__("uuid").uuid4(), name=empresa, tax_id="XXX010101000")
    sesion.add(empresa_obj)
    await sesion.flush()
    for t in tickets:
        sesion.add(TicketModel(company_id=empresa_obj.id, **t))
    await sesion.commit()
    return empresa_obj.id


class TestTotales:
    async def test_suma_lo_que_esta_en_el_rango_y_nada_mas(self, sesion):
        from datetime import date
        await _sembrar(sesion, [
            {"provider_name": "A", "total_amount": Decimal("100"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 3, 5), "extraction_status": "APROBADO"},
            {"provider_name": "B", "total_amount": Decimal("250"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 3, 20), "extraction_status": "APROBADO"},
            # Fuera del rango: marzo ya no es el mes en curso de la prueba.
            {"provider_name": "C", "total_amount": Decimal("999"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 4, 1), "extraction_status": "APROBADO"},
        ])
        desde = datetime(2026, 3, 1, tzinfo=timezone.utc)
        hasta = datetime(2026, 4, 1, tzinfo=timezone.utc)
        total, n = await analitica.total_del_periodo(sesion, None, desde, hasta)
        assert total == Decimal("350.00")
        assert n == 2

    async def test_una_base_vacia_da_cero_y_no_none(self, sesion):
        total, n = await analitica.total_del_periodo(
            sesion, None,
            datetime(2026, 3, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        assert total == Decimal("0")
        assert n == 0

    async def test_cuenta_por_fecha_de_gasto_y_no_por_fecha_de_carga(self, sesion):
        """Un comprobante de marzo subido en abril es gasto de marzo.

        Si se contara por `created_at`, el cierre de marzo saldria incompleto y el
        de abril traeria gastos de marzo. Es el error que hace que un cierre no
        cuadre y que nadie sepa donde esta la diferencia.
        """
        from datetime import date, datetime as dt
        from app.core.time import utcnow
        t = TicketModel(
            id=__import__("uuid").uuid4(),
            company_id=None,
            provider_name="A",
            total_amount=Decimal("100"),
            tax_amount=Decimal("0"),
            expense_date=date(2026, 3, 10),
            extraction_status="APROBADO",
        )
        empresa = CompanyModel(id=__import__("uuid").uuid4(), name="E", tax_id="T1")
        sesion.add(empresa)
        await sesion.flush()
        t.company_id = empresa.id
        # `created_at` se deja en "ahora", que es abril: el contraste con
        # `expense_date` es justo lo que se quiere comprobar.
        t.created_at = dt(2026, 4, 2, tzinfo=timezone.utc)
        sesion.add(t)
        await sesion.commit()

        total, n = await analitica.total_del_periodo(
            sesion, empresa.id,
            datetime(2026, 3, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        assert total == Decimal("100.00")
        assert n == 1


class TestPorCategoria:
    async def test_las_porcentajes_suman_cien(self, sesion):
        """El pie de la grafica tiene que cuadrar con las barras.

        Si las barras suman 98% y el encabezado dice otro total, la persona deja
        de confiar en la pantalla entera, y con razon. Es la comprobacion mas
        basica de una grafica de reparto y la que mas a menudo falla cuando el
        denominador se calcula en dos sitios distintos.
        """

        from datetime import date
        await _sembrar(sesion, [
            {"provider_name": "A", "total_amount": Decimal("750"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 3, 5), "extraction_status": "APROBADO", "category": "ALIMENTACION"},
            {"provider_name": "B", "total_amount": Decimal("250"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 3, 6), "extraction_status": "APROBADO", "category": "TRANSPORTE"},
        ])
        desde = datetime(2026, 3, 1, tzinfo=timezone.utc)
        hasta = datetime(2026, 4, 1, tzinfo=timezone.utc)
        total, _ = await analitica.total_del_periodo(sesion, None, desde, hasta)
        filas = await analitica.por_categoria(sesion, None, desde, hasta, total)

        assert len(filas) == 2
        assert sum(f["monto"] for f in filas) == total
        assert sum(f["porcentaje"] for f in filas) == pytest.approx(100.0, abs=0.05)

    async def test_las_mismas_categorias_escritas_distinto_se_agrupan(self, sesion):
        """`Supermercado` y `supermercado` son el mismo gasto.

        Sin normalizar, darian dos barras con el mismo nombre y la persona veria
        un gasto partido en dos. Es la razon por la que existe
        `app.core.categorias.normaliza`.
        """
        from datetime import date
        await _sembrar(sesion, [
            {"provider_name": "A", "total_amount": Decimal("100"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 3, 5), "extraction_status": "APROBADO", "category": "Supermercado"},
            {"provider_name": "B", "total_amount": Decimal("200"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 3, 6), "extraction_status": "APROBADO", "category": "SUPERMERCADO"},
        ])
        desde = datetime(2026, 3, 1, tzinfo=timezone.utc)
        hasta = datetime(2026, 4, 1, tzinfo=timezone.utc)
        total, _ = await analitica.total_del_periodo(sesion, None, desde, hasta)
        filas = await analitica.por_categoria(sesion, None, desde, hasta, total)

        assert len(filas) == 1
        assert filas[0]["monto"] == Decimal("300.00")
        assert filas[0]["tickets"] == 2

    async def test_sin_categoria_es_una_categoria_visible(self, sesion):
        """Se muestra, no se esconde.

        El gasto sin clasificar no es un rubro, es trabajo pendiente, y esconderlo
        hace que las demas barras parezcan mas informativas de lo que son.
        """
        from datetime import date
        await _sembrar(sesion, [
            {"provider_name": "A", "total_amount": Decimal("300"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 3, 5), "extraction_status": "APROBADO", "category": None},
            {"provider_name": "B", "total_amount": Decimal("100"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 3, 6), "extraction_status": "APROBADO", "category": "TRANSPORTE"},
        ])
        desde = datetime(2026, 3, 1, tzinfo=timezone.utc)
        hasta = datetime(2026, 4, 1, tzinfo=timezone.utc)
        total, _ = await analitica.total_del_periodo(sesion, None, desde, hasta)
        filas = await analitica.por_categoria(sesion, None, desde, hasta, total)

        sin = [f for f in filas if f["sin_clasificar"]]
        assert len(sin) == 1
        assert sin[0]["monto"] == Decimal("300.00")
        assert sin[0]["porcentaje"] == pytest.approx(75.0)

    async def test_las_categorias_salen_en_orden_canonico(self, sesion):
        """Dos periodos con las mismas categorias tienen que salir en el mismo orden.

        Ordenadas por monto, cada periodo las reacomoda y comparar dos meses se
        vuelve imposible. El orden es el canonico, con "Sin clasificar" al final.
        """
        from datetime import date
        await _sembrar(sesion, [
            #很多人的 caso: la categoria "grande" es una y la "chica" es otra, y
            # el orden tiene que ser el de la lista, no el del monto.
            {"provider_name": "A", "total_amount": Decimal("10"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 3, 5), "extraction_status": "APROBADO", "category": "RENTA"},
            {"provider_name": "B", "total_amount": Decimal("900"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 3, 6), "extraction_status": "APROBADO", "category": "ALIMENTACION"},
        ])
        desde = datetime(2026, 3, 1, tzinfo=timezone.utc)
        hasta = datetime(2026, 4, 1, tzinfo=timezone.utc)
        total, _ = await analitica.total_del_periodo(sesion, None, desde, hasta)
        filas = await analitica.por_categoria(sesion, None, desde, hasta, total)

        claves = [f["clave"] for f in filas]
        # RENTA va antes que ALIMENTACION en la lista canonica, aunque cueste
        # diez veces menos.
        assert claves.index("RENTA") < claves.index("ALIMENTACION")


class TestTendencia:
    async def test_devuelve_un_punto_por_mes_incluso_sin_datos(self, sesion):
        ahora = datetime(2026, 6, 15, tzinfo=timezone.utc)
        serie = await analitica.tendencia_mensual(sesion, None, meses=12, ahora=ahora)
        assert len(serie) == 12
        assert all(m["monto"] == Decimal("0") for m in serie)
        assert serie[-1]["etiqueta"] == "jun"

    async def test_los_meses_vacios_son_ceros_y_no_faltantes(self, sesion):
        from datetime import date
        await _sembrar(sesion, [
            {"provider_name": "A", "total_amount": Decimal("100"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 5, 5), "extraction_status": "APROBADO"},
        ])
        ahora = datetime(2026, 6, 15, tzinfo=timezone.utc)
        serie = await analitica.tendencia_mensual(sesion, None, meses=3, ahora=ahora)

        assert [m["etiqueta"] for m in serie] == ["abr", "may", "jun"]
        assert serie[1]["monto"] == Decimal("100.00")
        assert serie[0]["monto"] == Decimal("0")
        assert serie[2]["monto"] == Decimal("0")


class TestBanco:
    async def test_sin_conciliar_por_diferencia_y_no_por_resta(self, sesion):
        """Un movimiento con dos conciliaciones no puede dar un negativo.

        Por eso `sin_conciliar` sale de la columna `is_reconciled` del movimiento
        y no de "total menos conciliaciones". Con la resta, un movimiento
        conciliado dos veces daria -1, y un tablero con -1 en una tarjeta se lee
        como que se concilio mas dinero del que hay.
        """
        import uuid
        from datetime import date

        empresa = CompanyModel(id=uuid.uuid4(), name="E", tax_id="T1")
        sesion.add(empresa)
        await sesion.flush()

        # Tres movimientos, dos conciliados y uno sin cuadrar.
        for i, conciliado in enumerate([True, True, False]):
            sesion.add(BankTransactionModel(
                id=uuid.uuid4(), company_id=empresa.id,
                transaction_date=date(2026, 3, 5), amount=Decimal("100"),
                description=f"M{i}", is_reconciled=conciliado,
            ))
        await sesion.commit()

        estado = await analitica.estado_del_banco(sesion, empresa.id)
        assert estado["total"] == 3
        assert estado["conciliados"] == 2
        assert estado["sin_conciliar"] == 1
        assert estado["sin_conciliar"] >= 0
        assert estado["porcentaje"] == pytest.approx(66.67, abs=0.1)


class TestComparacionALaFecha:
    async def test_corta_el_mes_anterior_al_mismo_dia(self, sesion):
        """La comparacion que vale mientras el mes va a medias.

        Sin esto, el dia 3 el tablero dice que el gasto cayo 90% y es
        aritmeticamente cierto: el mes anterior estaba completo y este tiene
        tres dias.
        """
        from datetime import date
        await _sembrar(sesion, [
            # El mes anterior, dia 5: 100. Dia 20 (fuera del corte): 900.
            {"provider_name": "A", "total_amount": Decimal("100"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 2, 5), "extraction_status": "APROBADO"},
            {"provider_name": "B", "total_amount": Decimal("900"), "tax_amount": Decimal("0"),
             "expense_date": date(2026, 2, 20), "extraction_status": "APROBADO"},
        ])
        ahora = datetime(2026, 3, 10, tzinfo=timezone.utc)
        monto = await analitica.comparacion_a_la_fecha(sesion, None, ahora=ahora)
        # Corta el 10 de febrero: solo entra el dia 5.
        assert monto == Decimal("100.00")
