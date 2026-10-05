"""Que dice la grafica, en palabras.

Por que existe
--------------
Una grafica de doce barras no se interpreta sola. Alguien tiene que mirar y
concluir, y cada persona concluye distinto. Un tablero que obliga a cada
visitante a sacar su propia conclusion es un tablero que no comunica: es un
ejercicio.

La version de este tablero era exactamente eso. Doce barras, un color distinto
en la ultima, y ninguna palabra. El lector tenia que adivinar por que esa barra
era verde.

Que dice, y con queLimits
------------------------
Un hallazgo **solo se emite si los datos lo sostienen**, y si no se sostienen no
se inventa: se dice que no hay conclusion. Tres reglas que este modulo no rompe:

1. **La mediana, no el promedio.** Un mes de $241,513 sube el promedio de once
   meses y desplaza el centro de todos ellos. La mediana no se mueve con el
   valor extremo, y para decir "gasto habitual" es la unica cifra honesta.

2. **El mes en curso nunca entra en las estadisticas.** Esta a medias, y meterlo
   en la mediana de los meses cerrados baja el centro de referencia con un
   numero que todavia no existe. Es el mismo error que comparar un mes a medias
   contra uno completo, aplicado a las estadisticas.

3. **Se avisa cuando la comparacion es contra una base atipica.** Este es el
   que mas importa y el que ninguna grafica dice: si el mes anterior fue
   atipico, compararse contra el es compararse contra lo excepcional, y el
   porcentaje resultante no significa lo que parece. Cuando agosto es el mes mas
   bajo del año, "subimos 27%" dice menos de lo que parece.

Los hallazgos son datos estructurados, no texto escrito. El backend decide *que*
pasó y con que tono; el frontend decide como se lo cuenta. La parte que decide
va aqui porque es la que se puede probar, y una conclusion equivocada en un
tablero no se ve: se nota cuando alguien pregunta.
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import Optional

# Cuanto se puede alejar un mes de la mediana y seguir siendo "normal".
#
# 50% hacia arriba y hacia abajo, y es un numero redondo a proposito: no se
# afina, porque afinarlo es inventar precision sobre datos de once puntos. Con
# once meses, cualquier umbral sofisticado seria ruido con decimales.
UMBRAL_ATIPICO = Decimal("0.50")

# A partir de aqui la base de comparacion ya no es "normal" y hay que decirlo.
# Mas bajo que UMBRAL_ATIPICO porque no es un outlier, es una base unusual: comparar
# contra el mes mas bajo del año produce un porcentaje que se lee como
# "subimos" cuando en realidad solo volvio a su nivel.
UMBRAL_BASE_ATIPICA = Decimal("0.25")

# Menos de esto no hay conclusion que sacar de una serie de meses.
MINIMO_MESES = 4

TONO = {
    "neutro": "default",
    "bien": "success",
    "atencion": "warning",
    "mal": "danger",
}


def _a_decimal(valor) -> Decimal:
    if valor is None:
        return Decimal("0")
    return Decimal(str(valor))


class _Medicion:
    """Las estadisticas de los meses YA CERRADOS.

    Se construye aparte de los hallazgos para que las comparaciones usen siempre
    los mismos numeros: si cada hallazgo recalculara la mediana, un mes que cae
    en dos reglas daria dos centricas distintas y el tablero se contradiria."""

    def __init__(self, montos: list[Decimal]):
        vals = [float(m) for m in montos if m >= 0]
        self.meses = len(vals)
        self.mediana = Decimal(str(round(statistics.median(vals), 2))) if vals else Decimal("0")
        self.minimo = Decimal(str(round(min(vals), 2))) if vals else Decimal("0")
        self.maximo = Decimal(str(round(max(vals), 2))) if vals else Decimal("0")
        self.promedio = Decimal(str(round(statistics.mean(vals), 2))) if vals else Decimal("0")
        # Desviacion estandar relativa. Dice si el gasto es predecible o si salta.
        # Con menos de tres meses no tiene sentido: la dispersion de dos numeros
        # siempre es enorme y no informa de nada.
        self.dispersion = (
            float(statistics.stdev(vals) / statistics.mean(vals))
            if len(vals) >= 3 and statistics.mean(vals) > 0
            else None
        )

    @property
    def suficiente(self) -> bool:
        return self.meses >= MINIMO_MESES

    def desvio(self, monto: Decimal) -> Decimal:
        """Cuanto se aparta de la mediana, en proporcion. Positivo = por encima."""
        if self.mediana <= 0:
            return Decimal("0")
        return (monto - self.mediana) / self.mediana

    def es_atipico(self, monto: Decimal) -> bool:
        return abs(self.desvio(monto)) > UMBRAL_ATIPICO


def _hallazgo(tipo: str, titulo: str, detalle: str, tono: str = "neutro") -> dict:
    return {"tipo": tipo, "titulo": titulo, "detalle": detalle, "tono": tono}


def hallazgos_tendencia(
    meses: list[dict],
    *,
    mes_en_curso: bool,
    monto_mes_en_curso: Decimal,
    variacion_a_la_fecha: Optional[float],
    nombre_anterior: str,
) -> list[dict]:
    """Lo que se puede concluir de la serie de meses.

    `meses` viene con el mes en curso al final (lo manda el servidor asi). Se
    separa de los cerrados y cada grupo se usa para lo que le toca:

      - los **cerrados** dan el nivel habitual y los atipicos;
      - el **en curso** se compara contra la mediana, nunca se promedia.
    """
    if not meses:
        return []

    cerrados = list(meses[:-1]) if mes_en_curso else list(meses)
    m = _Medicion([_a_decimal(x["monto"]) for x in cerrados])
    hallazgos: list[dict] = []

    if not m.suficiente:
        return [
            _hallazgo(
                "datos_insuficientes",
                " todavia no hay serie para concluir",
                f"Hacen falta {MINIMO_MESES} meses cerrados y hay {m.meses}. "
                "Con menos, cualquier conclusion seria ruido.",
            ),
        ]

    # --- 1. El nivel habitual -------------------------------------------
    hallazgos.append(
        _hallazgo(
            "nivel",
            "Gasto habitual",
            f"alrededor de ${m.mediana:,.0f} al mes",
            "neutro",
        )
    )

    # --- 2. Los meses atipicos ------------------------------------------
    #
    # Se nombran, no solo se cuentan. Saber "hubo un mes atipico" no sirve de
    # nada; saber cual y cuanto es lo que permite ir a buscarlo.
    atipicos = [
        (x, m.desvio(_a_decimal(x["monto"])))
        for x in cerrados
        if m.es_atipico(_a_decimal(x["monto"]))
    ]
    # El mas desviado primero: si hay tres, el que mas se sale es el que
    # conviene mirar.
    atipicos.sort(key=lambda par: abs(par[1]), reverse=True)

    for x, desvio in atipicos[:2]:
        pct = float(abs(desvio)) * 100
        arriba = desvio > 0
        mes_nombre = x.get("nombre") or x["etiqueta"]
        hallazgos.append(
            _hallazgo(
                "atipico",
                f"{mes_nombre} se salió de lo normal",
                f"${_a_decimal(x['monto']):,.0f}, {pct:.0f}% "
                f"{'por encima' if arriba else 'por debajo'} de lo habitual",
                "atencion" if arriba else "neutro",
            )
        )

    # --- 3. El mes en curso ---------------------------------------------
    #
    # Se mide contra la mediana, no contra el mes anterior. La mediana es el
    # "gasto de siempre"; el mes anterior puede haber sido el mas caro o el mas
    # barato del año, y contra los dos la comparacion miente.
    if mes_en_curso:
        desvio = m.desvio(monto_mes_en_curso)
        pct = float(abs(desvio)) * 100
        if abs(desvio) > UMBRAL_ATIPICO:
            arriba = desvio > 0
            hallazgos.append(
                _hallazgo(
                    "mes_en_curso",
                    "Este mes va fuera de lo normal",
                    f"${monto_mes_en_curso:,.0f} para el mes en curso, "
                    f"{pct:.0f}% {'por encima' if arriba else 'por debajo'} de lo habitual",
                    "atencion" if arriba else "neutro",
                )
            )
        else:
            hallazgos.append(
                _hallazgo(
                    "mes_en_curso",
                    "Este mes va en su rango",
                    f"${monto_mes_en_curso:,.0f} contra los ${m.mediana:,.0f} habituales "
                    f"({pct:.0f}% {'por encima' if desvio > 0 else 'por debajo'})",
                    "bien" if desvio < 0 else "neutro",
                )
            )

        # --- 4. La trampa de la base de comparacion ---------------------
        #
        # Este es el hallazgo que hace util el modulo, y el que mas se ha
        # corregido. Compara contra `UMBRAL_BASE_ATIPICA` y **no** contra
        # `es_atipico()`:
        #
        # Son dos preguntas distintas con dos umbrales distintos, y confundirlas
        # hacia que el aviso no saliera nunca. "Es un mes atipico" (50% de
        # desviacion) pregunta si el mes se salio del rango. "Es una base
        # inusual" (25%) pregunta si el porcentaje va a confundir. Un mes al
        # -41% no es atipico, pero es exactamente el tipo de base contra la
        # que un "+119%" se lee mal: no hubo un aumento, se volvio a la
        # normalidad.
        if cerrados and variacion_a_la_fecha is not None:
            anterior = _a_decimal(cerrados[-1]["monto"])
            desvio_anterior = m.desvio(anterior)
            base_inusual = abs(desvio_anterior) > UMBRAL_BASE_ATIPICA

            # Solo tiene sentido avisar cuando la base estaba fuera de lo normal
            # Y el movimiento va en sentido contrario a esa anomalia. Si la base
            # era baja y el gasto subio, se esta volviendo a la normalidad; si
            # era alta y bajo, tambien. Si la base era baja y el gasto sigo
            # bajo, no hay nada raro que avisar.
            if base_inusual:
                base_baja = desvio_anterior < 0
                viniendo_de_atras = (base_baja and variacion_a_la_fecha > 0) or (
                    not base_baja and variacion_a_la_fecha < 0
                )
                if viniendo_de_atras:
                    hallazgos.append(
                        _hallazgo(
                            "base_atipica",
                            f"Cuidado: {nombre_anterior} no fue un mes normal",
                            f"Estuvo {abs(float(desvio_anterior)) * 100:.0f}% "
                            f"{'por debajo' if base_baja else 'por encima'} de lo habitual. "
                            f"Ver '{variacion_a_la_fecha:+.0f}%' contra él es volver a la "
                            "normalidad, no un giro del gasto.",
                            "atencion",
                        )
                    )

    # --- 5. La estabilidad ---------------------------------------------
    #
    # Solo con dispersion alta, que es cuando dice algo. "Variacion del 12%":
    # no hay nada que hacer. "Variacion del 60%, con picos de dos veces lo normal":
    # si, hay que investigar de donde salen.
    if m.dispersion is not None and m.dispersion >= 0.35:
        hallazgos.append(
            _hallazgo(
                "dispersion",
                "El gasto es irregular",
                f"varía {m.dispersion * 100:.0f}% entre meses, entre "
                f"${m.minimo:,.0f} y ${m.maximo:,.0f}. Con este nivel de variación "
                "conviene presupuesto por rango, no por el promedio.",
                "atencion",
            )
        )

    return hallazgos
