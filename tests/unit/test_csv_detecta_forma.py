"""El CSV del banco tiene la forma que le da el banco, no la que espera el codigo.

MEDIDO el 2026-10-12. Cuatro CSVs que un banco mexicano exporta de verdad, y lo
que pasaba con cada uno:

    Fecha;Descripcion;Importe / 05/10/2026;FARMACIA;-89.50   -> 400
    fecha,descripcion,importe / 2026-10-05,PANADERIA,-250.00    -> 400
    Fecha,Descripcion,Importe / 2026-10-05,PANADERIA,-250.00    -> 400
    Fecha,Importe,Concepto / 05/10/2026,-250.00,PANADERIA       -> 200

**Uno de cuatro.** Y los tres que fallaban son el mismo archivo con distinta
forma: separador, orden de fecha y nombre de encabezado. Los tres estaban a la
vista.

El mensaje de error era actively ENGAÑOSO: un CSV de punto y coma reportedly
"faltan las columnas importe, fecha y concepto", cuando lo que pasaba es que se
leia como UNA sola columna de nombre gigante. El error describia la consecuencia
y no la causa, que es la peor forma de error porque lleva al usuario a buscar
donde no esta el problema.

LO QUE SE ARREGLA
=================
  1. El separador se detecta del contenido (`csv.Sniffer`) antes de leer.
  2. Los encabezados se resuelven por sinonimo: `Descripcion` -> concepto,
     `Amount` -> importe, `Date` -> fecha.
  3. El formato de fecha se prueba sobre las filas reales en orden de
     especificidad, con el orden mexicano primero.

LO QUE NO SE ARREGLA, Y POR QUE NO SE TOCA
==========================================
Los separadores de DECIMAL y de MILES. Se dejan como los manda el cliente a
propósito, y hay una razon medida para no adivinarlos:

    Un banco reporta 890.00 y la conciliacion guarda 89,000.00 no producen un
    400. Producen una conciliacion "correcta" con todos los tickets en
    discrepancia y ningun indicio de por que.

Cien veces el importe real, sin error ni aviso. Adivinar cual de los dos estilos
usa el archivo es exactamente la clase de error que este producto no se permite.
"""

from decimal import Decimal

import pytest

from app.services.parser_service import parse_bank_csv


class TestElSeparadorSeDetecta:
    """El error mas caro era aqui: un CSV de `;` se leia como UNA columna."""

    def test_punto_y_coma(self):
        filas = parse_bank_csv(
            b"Fecha;Descripcion;Importe\n05/10/2026;FARMACIA;-89.50\n"
        )
        assert len(filas) == 1
        assert filas[0].description == "FARMACIA"
        assert filas[0].amount == Decimal("-89.50")

    def test_coma_sigue_funcionando(self):
        """La contraparte: arreglar el `;` no puede romper la coma."""
        filas = parse_bank_csv(
            b"Fecha,Descripcion,Importe\n05/10/2026,FARMACIA,-89.50\n"
        )
        assert filas[0].description == "FARMACIA"

    def test_tabulador(self):
        filas = parse_bank_csv(
            b"Fecha\tDescripcion\tImporte\n05/10/2026\tFARMACIA\t-89.50\n"
        )
        assert filas[0].description == "FARMACIA"

    def test_el_error_dice_las_columnas_que_si_hay(self):
        """Un mensaje de "faltan columnas" sin decir cuales hay es medio arreglo.

        MEDIDO: el error original decia `Missing required columns: {'importe',
        'fecha', 'concepto'}` para un archivo que TENIA las tres, separadas por
        punto y coma. Eso manda a buscar un problema de nombres donde el problema
        era de separador.

        Aqui ya no se dispara porque el `;` se detecta, pero el mensaje se queda
        para el caso real que queda: un archivo que de verdad no trae un importe.
        """
        with pytest.raises(ValueError) as exc:
            parse_bank_csv(b"A,B\ntexto,otro\n")

        texto = str(exc.value)
        assert "a" in texto and "b" in texto, (
            f"el mensaje no lista las columnas que si hay: {texto}"
        )


class TestLosEncabezadosSeResuelvenPorSinonimo:
    """Un banco mexicano exporta en español y en ingles."""

    @pytest.mark.parametrize(
        "encabezado_descripcion,encabezado_importe,encabezado_fecha",
        [
            ("Descripcion", "Importe", "Fecha"),
            ("description", "amount", "date"),
            ("Concepto", "Monto", "Date"),
            ("detalle", "deposito_retiro", "fecha_operacion"),
        ],
    )
    def test_variaciones_de_nombre(
        self, encabezado_descripcion, encabezado_importe, encabezado_fecha
    ):
        """Cada fila de esta tabla es un banco distinto exporting distinto.

        Se prueban los cuatro juntos porque el fallo original era que CUALQUIERA
        de ellos podia tumbar la importacion, y probarlos de a uno deja el hueco
        de los otros tres.
        """
        csv = (
            f"{encabezado_fecha},{encabezado_descripcion},{encabezado_importe}\n"
            "05/10/2026,FARMACIA,-89.50\n"
        ).encode()

        filas = parse_bank_csv(csv)

        assert filas[0].description == "FARMACIA"
        assert filas[0].amount == Decimal("-89.50")

    def test_lo_que_manda_el_cliente_manda(self):
        """Si el cliente dice que la columna es `x`, no se decide por el.

        Detectar el separador es leer el archivo. Elegir entre dos columnas que
        se parecen es adivinar cual de las dos es la correcta, y adivinar eso es
        la clase de error que este producto no se permite.
        """
        csv = b"cuando,concepto,cual_importo\n2026-10-05,FARMACIA,-89.50\n"

        filas = parse_bank_csv(
            csv,
            date_column="cuando",
            amount_column="cual_importo",
            description_column="concepto",
        )

        assert filas[0].amount == Decimal("-89.50")

    def test_un_nombre_equivocado_se_reporta_como_tal(self):
        """Si el cliente pide una columna que no existe, se dice cual hay.

        No se cae al sinonimo: el cliente sabe de que columna habla y si se
        equivoco, avisarle "quizas quisiste decir Descripcion" es un intento de
        adivinar. Se le da la lista y que elija.
        """
        with pytest.raises(ValueError) as exc:
            parse_bank_csv(
                b"Fecha,Descripcion,Importe\n05/10/2026,FARMACIA,-89.50\n",
                amount_column="columna_que_no_existe",
            )

        texto = str(exc.value)
        assert "columna_que_no_existe" in texto
        assert "importe" in texto, "no dice que columnas SI hay"


class TestElFormatoDeFechaSeDetecta:
    """Un ISO y un dd/mm son los mismos movimientos."""

    @pytest.mark.parametrize(
        "valor_esperado",
        [
            b"05/10/2026",   # como imprime un banco mexicano
            b"2026-10-05",   # ISO, que es lo que exporta cualquier sistema moderno
            b"05-10-2026",
        ],
    )
    def test_formatos_de_fecha(self, valor_esperado):
        csv = (
            b"Fecha,Descripcion,Importe\n"
            + valor_esperado
            + b",FARMACIA,-89.50\n"
        )

        filas = parse_bank_csv(csv)

        assert filas[0].transaction_date.isoformat() == "2026-10-05", (
            f"la fecha {valor_esperado!r} no se resolvio a 2026-10-05"
        )

    def test_el_formato_mexicano_gana_a_el_americano(self):
        """`01/02/2026` es ambiguo, y no da igual cual se elija.

        MEDIDO: el orden de los formatos candidatos pone `%d/%m/%Y` antes que
        `%m/%d/%Y`, y a proposito. Un extracto que va a un contador en Mexico no
        viene en orden americano, asi que cuando dos formatos explican las mismas
        cifras gana el del pais. Este test falla si alguien invierte el orden
        "porque es el de JavaScript", que es un argumento que suena razonable y
        cambia 13 de cada 12 fechas.
        """
        filas = parse_bank_csv(
            b"Fecha,Descripcion,Importe\n01/02/2026,FARMACIA,-89.50\n"
        )

        assert filas[0].transaction_date.isoformat() == "2026-02-01"

    def test_un_formato_explicito_manda(self):
        """Si el cliente sabe el formato de SU papel, se respeta."""
        filas = parse_bank_csv(
            b"Date,Description,Amount\n01/02/2026,FARMACIA,-89.50\n",
            date_column="date",
            amount_column="amount",
            description_column="description",
            date_format="%m/%d/%Y",
        )

        assert filas[0].transaction_date.isoformat() == "2026-01-02"


class TestElArchivoRealDeLaSesionAnterior:
    """El caso que se rompio, tal cual, para que no vuelva a romperse."""

    def test_extracto_completo(self):
        """Un extracto de banco de los que salen de la banca en linea.

        Mezcla las cuatro cosas que antes caian por separado: punto y coma,
        encabezados en mayusculas con `Descripcion`, fecha dd/mm y miles con
        coma. Antes esto no pasaba de "faltan columnas".
        """
        csv = (
            b"Fecha;Descripcion;Importe;Referencia\n"
            b"05/10/2026;RENTA OCTUBRE;-8,500.00;R001\n"
            b"06/10/2026;PANADERIA NORTE;-250.00;R002\n"
        )

        filas = parse_bank_csv(csv)

        assert len(filas) == 2
        assert filas[0].amount == Decimal("-8500.00"), (
            "los miles con coma no se quitaron"
        )
        assert filas[0].reference == "R001"
        assert filas[1].description == "PANADERIA NORTE"


class TestLoQueNoSeAdivina:
    """Las defensas que NO se tocan, porque adivinar aqui es peor que fallar."""

    def test_un_importe_que_no_es_numero_falla_en_vez_de_inventarse(self):
        """Un texto donde va el importe se rechaza; no se pone cero.

        Poner 0.00 seria fabricar: el archivo tiene algo y el sistema decidio que
        era cero. Y ahi un contador veria un movimiento de 0.00 que no existe.
        """
        with pytest.raises(ValueError):
            parse_bank_csv(
                b"Fecha,Descripcion,Importe\n05/10/2026,FARMACIA,NO APLICA\n"
            )