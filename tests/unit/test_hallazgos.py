"""Pruebas de `app.services.hallazgos`.

Por que hay tantas y por que estan marcadas
--------------------------------------------
Un tablero con una conclusion equivocada es peor que un tablero sin conclusion:
la primera se cree y la segunda se nota. Estas pruebas existen para que eso no
pase en silencio, y la mayoria cubren **el caso en que no hay que concluir nada**
o **en que hay que concluir lo contrario de lo que parece**. No es desconfianza
del codigo: es que un hallazgo se genera comparando numeros, y comparar numeros
equivocado no lanza error, sale bonito.

El modulo es de funciones puras sin base de datos a proposito: una conclusion se
prueba con una lista de meses, no levantando Postgres.
"""

from decimal import Decimal

import pytest

from app.services import hallazgos
from app.services.hallazgos import hallazgos_tendencia


def serie(valores: list[int]) -> list[dict]:
    """Construye una serie de meses con esos montos, de mas antiguo a mas nuevo.

    **El ultimo elemento es el mes en curso.** El servidor los manda asi, y
    `hallazgos_tendencia` saca el ultimo de la lista de cerrados cuando
    `mes_en_curso` es verdadero. Por eso, para probar "el mes anterior estaba
    bajo", hay que pasar doce valores: los once cerrados y el que va.
    """
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    return [
        {"anio": 2026, "mes": i + 1, "etiqueta": meses[i], "nombre": meses[i].capitalize(),
         "monto": Decimal(str(v)), "tickets": 10}
        for i, v in enumerate(valores)
    ]


def serie_cerrada(valores: list[int]) -> list[dict]:
    """Igual, pero el ultimo mes esta cerrado y entra en las estadisticas."""
    return serie(valores)


def por_tipo(resultado: list[dict], tipo: str) -> list[dict]:
    return [h for h in resultado if h["tipo"] == tipo]


# ---------------------------------------------------------------------------


class TestNoSeConcluyeDeMas:
    def test_sin_ningun_mes_no_hay_hallazgos(self):
        assert hallazgos_tendencia(
            [], mes_en_curso=False, monto_mes_en_curso=Decimal("0"),
            variacion_a_la_fecha=None, nombre_anterior="",
        ) == []

    def test_con_dos_meses_no_se_saca_conclusion(self):
        """Dos puntos no son una serie.

        Con dos meses, cualquier conclusion ("el gasto cayo", "el gasto subio")
        es una observacion, no una tendencia, y hacerla sonar a tendencia
        entrena al lector a tratar cualquier ruido como senal.
        """
        r = hallazgos_tendencia(
            serie([100, 200]), mes_en_curso=False, monto_mes_en_curso=Decimal("0"),
            variacion_a_la_fecha=None, nombre_anterior="",
        )
        assert len(r) == 1
        assert r[0]["tipo"] == "datos_insuficientes"
        assert "4" in r[0]["detalle"]

    def test_un_mes_de_100_mil_no_genera_un_titulo_por_todos(self):
        """Un mes fuera de serie se reporta sobre ESE mes, no sobre el periodo.

        El hallazgo tiene que decir cual fue y cuanto, no "hubo un mes atipico":
        saber que paso no sirve de nada si no se sabe donde mirar.
        """
        r = hallazgos_tendencia(
            serie_cerrada([100, 100, 100, 100, 100, 100, 100, 100, 100, 500]),
            mes_en_curso=False, monto_mes_en_curso=Decimal("0"),
            variacion_a_la_fecha=None, nombre_anterior="",
        )
        atipicos = por_tipo(r, "atipico")
        assert len(atipicos) == 1
        # Se comprueba el monto y no el nombre del mes: el nombre depende de en
        # que posicion caiga el valor en la serie, que es un detalle del helper.
        assert "$500" in atipicos[0]["detalle"]


class TestNivelHabitual:
    def test_usa_la_mediana_y_no_el_promedio(self):
        """Un mes enorme no debe subir el centro de todos los demas.

        El promedio con un pico de 1000 entre nueve meses de 100 da 190, y con
        eso "gasto habitual" es 190 cuando en realidad el dia normal es 100. La
        mediana se queda en 100.
        """
        r = hallazgos_tendencia(
            serie([100, 100, 100, 100, 100, 100, 100, 100, 100, 1000]),
            mes_en_curso=False, monto_mes_en_curso=Decimal("0"),
            variacion_a_la_fecha=None, nombre_anterior="",
        )
        nivel = por_tipo(r, "nivel")[0]
        assert "$100" in nivel["detalle"]
        assert "$190" not in nivel["detalle"]

    def test_el_mes_en_curso_no_entra_en_la_estadistica(self):
        """Si no, el mes a medias baja el centro de referencia.

        Es el mismo error que comparar contra un mes completo, aplicado a las
        estadisticas: un mes con tres dias de gasto no es "gasto habitual".
        """
        valores = [100_000] * 11
        conCurso = hallazgos_tendencia(
            serie_cerrada(valores + [1_000]), mes_en_curso=True, monto_mes_en_curso=Decimal("1000"),
            variacion_a_la_fecha=None, nombre_anterior="",
        )
        sinCurso = hallazgos_tendencia(
            serie_cerrada(valores + [1_000]), mes_en_curso=False, monto_mes_en_curso=Decimal("0"),
            variacion_a_la_fecha=None, nombre_anterior="",
        )
        assert por_tipo(conCurso, "nivel")[0]["detalle"] == por_tipo(sinCurso, "nivel")[0]["detalle"]


class TestBaseAtipica:
    def test_avisa_cuando_el_mes_anterior_fue_bajo_y_ahora_subio(self):
        """El caso real: +119% no es un aumento, es volver a la normalidad.

        Es la trampa que hace util este modulo. El porcentaje que muestra la
        grafica es aritmeticamente correcto y se lee como unaDUPLICADA de gasto.
        """
        # Mediana 100, y el mes anterior en 41: bastante bajo pero no atipico.
        r = hallazgos_tendencia(
            serie([100] * 10 + [59, 129]), mes_en_curso=True,
            monto_mes_en_curso=Decimal("129"),
            variacion_a_la_fecha=118.6, nombre_anterior="agosto",
        )
        avisos = por_tipo(r, "base_atipica")
        assert len(avisos) == 1
        assert "agosto" in avisos[0]["titulo"]
        # Y tiene que decir que NO es un aumento del gasto.
        assert "normalidad" in avisos[0]["detalle"]

    def test_avisa_tambien_al_contrario_si_el_mes_anterior_fue_alto(self):
        """El caso simetrico: venia de un mes altisimo, asi que bajar es normal."""
        r = hallazgos_tendencia(
            serie([100] * 10 + [180, 95]), mes_en_curso=True,
            monto_mes_en_curso=Decimal("95"),
            variacion_a_la_fecha=-47.2, nombre_anterior="julio",
        )
        assert len(por_tipo(r, "base_atipica")) == 1

    def test_no_avisa_si_el_mes_anterior_era_normal(self):
        """Con una base normal, el porcentaje es de fiar y no hay nada que avisar."""
        r = hallazgos_tendencia(
            serie([100] * 11 + [120]), mes_en_curso=True,
            monto_mes_en_curso=Decimal("120"),
            variacion_a_la_fecha=20.0, nombre_anterior="agosto",
        )
        assert por_tipo(r, "base_atipica") == []

    def test_no_avisa_si_el_gasto_sigue_en_la_misma_direccion_que_la_anomalia(self):
        """Base baja y el gasto SIGUE bajando no es volver a la normalidad.

        Sigue siendo una mala noticia y hay que decirla como tal, no vestida de
        "vuelves a tu nivel".
        """
        r = hallazgos_tendencia(
            serie([100] * 10 + [59, 30]), mes_en_curso=True,
            monto_mes_en_curso=Decimal("30"),
            variacion_a_la_fecha=-49.2, nombre_anterior="agosto",
        )
        assert por_tipo(r, "base_atipica") == []

    def test_el_umbral_de_base_es_mas_bajo_que_el_de_atipico(self):
        """El fallo que motivo estas pruebas.

        La comprobacion usaba el umbral de "atipico" (50%) para decidir si la base
        era inusual. Con un mes anterior al -41% no se disparaba el aviso, y es
        justo el caso que mas confunde. Los dos umbrales existen porque son dos
        preguntas distintas.
        """
        from decimal import Decimal as D

        assert hallazgos.UMBRAL_BASE_ATIPICA < hallazgos.UMBRAL_ATIPICO

        r = hallazgos_tendencia(
            serie([100] * 10 + [50, 120]), mes_en_curso=True,
            monto_mes_en_curso=D("120"),
            variacion_a_la_fecha=140.0, nombre_anterior="agosto",
        )
        assert por_tipo(r, "base_atipica")

    def test_sin_variacion_no_hay_que_avisar_de_la_base(self):
        """Si no se puede calcular la variacion, no hay conclusion que dar."""
        r = hallazgos_tendencia(
            serie([100] * 10 + [59, 100]), mes_en_curso=True,
            monto_mes_en_curso=Decimal("100"),
            variacion_a_la_fecha=None, nombre_anterior="agosto",
        )
        assert por_tipo(r, "base_atipica") == []


class TestAtipicos:
    def test_nombra_el_mes_que_mas_se_sale(self):
        """Nombres, no un conteo. Saber "hubo un mes raro" no sirve de nada."""
        valores = [100] * 9 + [500, 100]
        r = hallazgos_tendencia(
            serie_cerrada(valores), mes_en_curso=False, monto_mes_en_curso=Decimal("0"),
            variacion_a_la_fecha=None, nombre_anterior="",
        )
        atipicos = por_tipo(r, "atipico")
        assert atipicos
        # El mas desviado va primero.
        assert "500" in atipicos[0]["detalle"] or "500" in atipicos[0]["titulo"]

    def test_solo_nombra_dos_como_mucho(self):
        """Cinco atipicos no son cinco hallazgos: son ruido.

        Con cinco, la lista deja de informar y pasa a ser una queja.
        """
        valores = [100] * 6 + [500, 500, 500, 500, 500]
        r = hallazgos_tendencia(
            serie_cerrada(valores), mes_en_curso=False, monto_mes_en_curso=Decimal("0"),
            variacion_a_la_fecha=None, nombre_anterior="",
        )
        assert len(por_tipo(r, "atipico")) <= 2

    def test_una_serie_estable_no_reporta_atipicos(self):
        valores = [100, 105, 98, 102, 99, 101, 103, 97, 100, 104]
        r = hallazgos_tendencia(
            serie_cerrada(valores), mes_en_curso=False, monto_mes_en_curso=Decimal("0"),
            variacion_a_la_fecha=None, nombre_anterior="",
        )
        assert por_tipo(r, "atipico") == []


class TestDispersion:
    def test_no_avisa_si_el_gasto_es_estable(self):
        """Variacion del 4%: no hay nada que hacer ni que decir."""
        valores = [100, 104, 96, 103, 98, 101, 99, 102, 97, 100]
        r = hallazgos_tendencia(
            serie_cerrada(valores), mes_en_curso=False, monto_mes_en_curso=Decimal("0"),
            variacion_a_la_fecha=None, nombre_anterior="",
        )
        assert por_tipo(r, "dispersion") == []

    def test_avisa_si_salta(self):
        valores = [100, 100, 100, 100, 100, 100, 100, 100, 100, 300]
        r = hallazgos_tendencia(
            serie_cerrada(valores), mes_en_curso=False, monto_mes_en_curso=Decimal("0"),
            variacion_a_la_fecha=None, nombre_anterior="",
        )
        assert por_tipo(r, "dispersion")


class TestMesEnCurso:
    def test_dice_que_va_en_su_rango_cuando_esta_en_su_rango(self):
        valores = [100] * 11
        r = hallazgos_tendencia(
            serie(valores), mes_en_curso=True, monto_mes_en_curso=Decimal("110"),
            variacion_a_la_fecha=10.0, nombre_anterior="agosto",
        )
        h = por_tipo(r, "mes_en_curso")
        assert len(h) == 1
        assert "rango" in h[0]["titulo"]

    def test_avisa_si_este_mes_se_sale_del_rango(self):
        valores = [100] * 11
        r = hallazgos_tendencia(
            serie(valores), mes_en_curso=True, monto_mes_en_curso=Decimal("400"),
            variacion_a_la_fecha=300.0, nombre_anterior="agosto",
        )
        h = por_tipo(r, "mes_en_curso")
        assert "fuera de lo normal" in h[0]["titulo"]
        assert h[0]["tono"] == "atencion"

    def test_bajar_el_gasto_es_buena_noticia(self):
        """Verde cuando baja. En un tablero de GASTO, subir es malo.

        Aqui no hay ambiguedad como en la cifra grande, donde el gris cubria los
        dos casos: bajar el gasto es siempre algo bueno.
        """
        valores = [100] * 11
        r = hallazgos_tendencia(
            serie(valores), mes_en_curso=True, monto_mes_en_curso=Decimal("80"),
            variacion_a_la_fecha=-20.0, nombre_anterior="agosto",
        )
        assert por_tipo(r, "mes_en_curso")[0]["tono"] == "bien"


class TestFormato:
    def test_todos_los_hallazgos_traen_los_campos_que_necesita_la_pantalla(self):
        """Un hallazgo sin tono o sin detalle se veria roto en la interfaz.

        Se comprueba en bloque porque un hallazgo a medio construir es un
        error de programacion, y en la UI se manifestaria como un texto vacio.
        """
        r = hallazgos_tendencia(
            serie([100] * 9 + [500, 60, 400]), mes_en_curso=True,
            monto_mes_en_curso=Decimal("400"),
            variacion_a_la_fecha=560.0, nombre_anterior="agosto",
        )
        assert r
        for h in r:
            assert h["titulo"].strip(), f"hallazgo sin titulo: {h}"
            assert h["detalle"].strip(), f"hallazgo sin detalle: {h}"
            assert h["tono"] in hallazgos.TONO, f"tono desconocido: {h['tono']}"
            assert h["tipo"].strip()

    def test_los_montos_no_tienen_decimales_de_mas(self):
        """Un gasto de $123,341.00 se lee mejor que $123,341.00 con centavos.

        La coma de miles con el formato de Python y el nombre del mes van en
        el texto, no en el type, porque son decisiones de presentacion.
        """
        r = hallazgos_tendencia(
            serie([100_000] * 11), mes_en_curso=False, monto_mes_en_curso=Decimal("0"),
            variacion_a_la_fecha=None, nombre_anterior="",
        )
        assert "$100,000" in r[0]["detalle"]
