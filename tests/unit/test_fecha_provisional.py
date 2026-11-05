"""## `fecha_provisional`: una fecha provisional NO DEFINE UN PERIODO

Este archivo fija la regla que hace que el cierre mensual pueda leerse como una
afirmacion sobre un mes, y no como una suma de lo que se digitalizo ese mes.

El problema que arregla, MEDIDO el 2026-10-12 sobre los 8 comprobantes reales de
esta maquina: 5 de 8 tienen `expense_date = 2026-10-12`, que es el dia en que se
digitalizaron y no el dia de la compra. Como las doce consultas del informe
filtran por `expense_date`, esos gastos entraban al total de octubre con toda la
autoridad de gastos reales de octubre.

Los tests son de dos tipos y los dos importan:

  - **De comportamiento**: la fila queda marcada, el total la excluye, una
    persona la limpia. Dies si se quita el filtro o la marca.
  - **De lectura del codigo**: el filtro esta en la CONSULTA y no en un `if` de
    Python. Dies si alguien lo mueve al bucle, porque ahi volveria a depender de
    que alguien recuerde llamarlo.

Y hay un test que parece obvio y no lo es: **el total y el desglose por categoria
tienen que filtrar los mismos gastos**. Si uno filtra y el otro no, las barras
suman mas que el total de arriba y el tablero muestra dos cifras que no pueden ser
ciertas a la vez. Eso es el modo de fallo que hace que un tablero entero pierda
credibilidad, y no se ve en ningun test de una sola funcion.
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.ticket import TicketModel
from app.services import analitica
from app.services.reporte_cierre import construir_reporte

# Un mes ya cerrado, para que el comparativo contra el mes anterior no ensucie las
# cifras que aquí se comparan.
PERIODO = "2026-03"
DESDE = datetime(2026, 3, 1)
HASTA = datetime(2026, 4, 1)
FECHA_EN_MARZO = date(2026, 3, 15)

FUENTE_ANALITICA = Path(__file__).resolve().parents[2] / "app" / "services" / "analitica.py"


async def _ticket(
    db,
    *,
    empresa,
    total: str = "100.00",
    expense_date: date = FECHA_EN_MARZO,
    provisional: bool = False,
    origen: str = "caso",
) -> TicketModel:
    """Un ticket minimo, con el campo que estos tests examinan ya puesto.

    Se escribe la columna a mano y no por `persistir_extraccion` a proposito: la
    pregunta de estos tests es "que hace el INFORME con una fila marcada", y
    atarlo a la captura meteria en el mismo test dos cosas que se pueden romper
    por separado. La marca la pone la captura, y que la ponga bien lo fija
    `tests/integration/test_capture_pipeline.py`; aqui se parte de una fila que ya
    la trae.
    """
    t = TicketModel(
        company_id=empresa.id,
        provider_name=f"COMERCIO {origen}",
        total_amount=Decimal(total),
        tax_amount=Decimal("0.00"),
        expense_date=expense_date,
        fecha_provisional=provisional,
        category="Alimentos",
        extraction_status="APROBADO",
        source_file=f"{origen}.pdf",
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


class TestElTotalNoCuentaLoQueNoPerteneceAlPeriodo:
    """La regla: una fecha provisional NO DEFINE UN PERIODO."""

    @pytest.mark.asyncio
    async def test_un_gasto_con_fecha_del_papel_suma(self, db_session, test_company):
        """La linea base: sin marca, el gasto cuenta.

        Si esto fallara, el filtro de abajo no seria lo que esta excluyendo sino
        que nada contaria nunca — que es el otro modo de fallo del mismo cambio.
        """
        await _ticket(db_session, empresa=test_company, total="100.00")

        total, conteo = await analitica.total_del_periodo(
            db_session, test_company.id, DESDE, HASTA
        )

        assert total == Decimal("100.00")
        assert conteo == 1

    @pytest.mark.asyncio
    async def test_un_gasto_con_fecha_provisional_no_suma(self, db_session, test_company):
        """ESTE ES EL TEST CENTRAL.

        El gasto esta en marzo segun la columna `expense_date`, asi que cualquier
        consulta que solo mire esa columna lo contaria. Y no debe: su fecha es el
        dia de la captura, no el de la compra, asi que **no se sabe de que mes
        es**. Sumarlo daria un total de marzo que el papel no sostiene.

        Si este test muere, el total del periodo volvio a incluir los gastos de
        los que no se sabe el mes — que es el defecto que esta columna existe
        para arreglar, y el que hacia que un comprobante de julio apareciera en
        el cierre de octubre.
        """
        await _ticket(db_session, empresa=test_company, total="100.00",
                      provisional=False, origen="del_papel")
        await _ticket(db_session, empresa=test_company, total="250.00",
                      provisional=True, origen="provisional")

        total, conteo = await analitica.total_del_periodo(
            db_session, test_company.id, DESDE, HASTA
        )

        assert total == Decimal("100.00"), (
            "el gasto con fecha provisional esta en el total del periodo"
        )
        assert conteo == 1

    @pytest.mark.asyncio
    async def test_el_provisional_no_desaparece(self, db_session, test_company):
        """Un gasto invisible es PEOR que uno mal fechado.

        Excluirlo del total no es borrarlo: la fila sigue ahi y el informe la
        cuenta aparte en `sin_fecha`. Si el gasto desapareciera sin que nadie lo
        diga, se cambiaria un numero visible equivocado por un hueco sin
        explicacion, y eso es mas caro.
        """
        await _ticket(db_session, empresa=test_company, total="250.00",
                      provisional=True, origen="provisional")

        filas = (await db_session.execute(
            select(TicketModel).where(TicketModel.company_id == test_company.id)
        )).scalars().all()

        assert len(filas) == 1, "excluir del total borro la fila"
        assert filas[0].total_amount == Decimal("250.00")


class TestElDesgloseCuadraConElTotal:
    """Las barras de abajo tienen que sumar el total de arriba."""

    @pytest.mark.asyncio
    async def test_las_barras_no_suman_mas_que_el_encabezado(self, db_session, test_company):
        """Si `por_categoria` no filtra y `total_del_periodo` si, el tablero
        muestra dos cifras incompatibles: el encabezado dice $100 y las barras
        suman $350.

        Es el test mas importante de este archivo aunque no mire la fecha: mira
        que las dos consultas digan lo mismo sobre el MISMO conjunto de datos. Y
        no se ve en ningun test de una sola funcion, porque cada una por su lado
        esta bien.
        """
        await _ticket(db_session, empresa=test_company, total="100.00",
                      provisional=False, origen="del_papel")
        await _ticket(db_session, empresa=test_company, total="250.00",
                      provisional=True, origen="provisional")

        total, _ = await analitica.total_del_periodo(
            db_session, test_company.id, DESDE, HASTA
        )
        categorias = await analitica.por_categoria(
            db_session, test_company.id, DESDE, HASTA, total
        )

        suma = sum(Decimal(str(c["monto"])) for c in categorias)

        assert suma == total, (
            f"el desglose suma {suma} y el total dice {total}: "
            "las dos cifras no pueden ser ciertas a la vez"
        )


class TestElInformeLoDice:
    @pytest.mark.asyncio
    async def test_el_informe_cuenta_los_sin_fecha(self, db_session, test_company):
        await _ticket(db_session, empresa=test_company, total="100.00",
                      provisional=False, origen="del_papel")
        await _ticket(db_session, empresa=test_company, total="250.00",
                      provisional=True, origen="provisional")

        reporte = await construir_reporte(
            db_session, test_company.id, PERIODO, firmado_por="qa"
        )

        assert reporte.pendientes.sin_fecha_tickets == 1
        assert reporte.pendientes.sin_fecha_monto == Decimal("250.00")
        assert reporte.gasto.total == Decimal("100.00"), (
            "el total del informe incluye un gasto del que no se sabe el mes"
        )

    @pytest.mark.asyncio
    async def test_sin_fecha_impide_cerrar_el_periodo(self, db_session, test_company):
        """Un periodo con un gasto del que no se sabe el mes NO se declara cerrado.

        No por prudencia generica: porque `puede_cerrarse` es la afirmacion que el
        contador se lleva. Si puede decir "octubre cerrado" habiendo un
        comprobante que podria ser de julio, la afirmacion es falsa y no hay
        forma de que el lector lo note.
        """
        await _ticket(db_session, empresa=test_company, total="250.00",
                      provisional=True, origen="provisional")

        reporte = await construir_reporte(
            db_session, test_company.id, PERIODO, firmado_por="qa"
        )

        assert reporte.pendientes.hay_pendientes is True
        assert "sin fecha" in (reporte.pendientes.motivo or "")

    @pytest.mark.asyncio
    async def test_sin_fecha_es_el_primer_motivo(self, db_session, test_company):
        """El orden importa y dice cual se ataca primero.

        Los otros pendientes —clasificar, conciliar— son trabajo normal que se
        puede hacer sin invalidar nada. Este es distinto: mientras exista, el
        total del periodo es una afirmacion sobre un mes que todavia no se sabe,
        y trabajar en los otros tres no lo arregla. Por eso va primero, no por
        jerarquia de estilo.
        """
        await _ticket(db_session, empresa=test_company, total="250.00",
                      provisional=True, origen="provisional", )
        await _ticket(db_session, empresa=test_company, total="80.00",
                      provisional=False, origen="sin_categoria")
        # El segundo queda sin categoria, que es el otro pendiente.
        fila = (await db_session.execute(
            select(TicketModel).where(TicketModel.source_file == "sin_categoria.pdf")
        )).scalar_one()
        fila.category = None
        await db_session.commit()

        reporte = await construir_reporte(
            db_session, test_company.id, PERIODO, firmado_por="qa"
        )

        motivo = reporte.pendientes.motivo or ""
        assert motivo.startswith("1 sin fecha"), (
            f"el motivo no empieza por la falta de fecha: {motivo!r}"
        )


class TestLaMarcaEsDeProcedencia:
    """Quien escribe la fecha la libera; la automatica no."""

    @pytest.mark.asyncio
    async def test_una_fecha_escrita_por_una_persona_no_es_provisional(
        self, db_session, test_company
    ):
        """La marca no dice que la fecha sea mala.

        Una fecha provisional puede ser perfectamente creible y aun asi no venir
        del papel. Lo que la libera es que una persona la escriba mirando el
        comprobante, y eso lo decide la escritura, no el informe.
        """
        t = await _ticket(db_session, empresa=test_company, total="100.00",
                          provisional=True, origen="provisional")
        assert t.fecha_provisional is True

        t.expense_date = date(2026, 3, 2)
        t.fecha_provisional = False
        await db_session.commit()

        reporte = await construir_reporte(
            db_session, test_company.id, PERIODO, firmado_por="qa"
        )

        assert reporte.pendientes.sin_fecha_tickets == 0
        assert reporte.gasto.total == Decimal("100.00")


class TestElFiltroEstaEnLaConsulta:
    """Una defensa que depende de que alguien la llame no es una defensa.

    Se lee el CODIGO y no el comportamiento a proposito: el comportamiento ya lo
    fijan los tests de arriba. Lo que estos fijan es DONDE esta el filtro,
    porque la forma de romperlo sin que salte ninguna alarma es moverlo de la
    consulta a Python —todo sigue dando el mismo numero mientras nadie mire el
    archivo— y un dia el filtro desaparece y el total se va sin que un test lo
    note.
    """

    def test_el_filtro_esta_en_la_consulta_y_no_en_un_if(self):
        fuente = FUENTE_ANALITICA.read_text(encoding="utf-8")

        assert "TicketModel.fecha_provisional.is_(False)" in fuente, (
            "el filtro desaparece de analitica.py"
        )

    def test_el_filtro_esta_en_las_tres_consultas_de_periodo(self):
        """Una sola de las tres seria el mismo defecto, a escala menor.

        Las tres arman un numero que se muestra junto a otro: el total, el total a
        la fecha del comparativo y el desglose por categoria. Si una filtra y las
        otras dos no, el comparativo resta una cifra que el periodo principal
        nunca mostro, y las barras no cuadran con el encabezado.
        """
        fuente = FUENTE_ANALITICA.read_text(encoding="utf-8")

        # `total_del_periodo` + `total_hasta` + `por_categoria`.
        assert fuente.count("TicketModel.fecha_provisional.is_(False)") >= 3