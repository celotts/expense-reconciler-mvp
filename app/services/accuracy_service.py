"""Medir la exactitud con evidencia, en vez de suponerla.

Este modulo existe porque "96% de exactitud" era una cifra sin forma de
comprobarla. Nadie podia responder de donde salia. Aqui vive lo que hace que
la cifra tenga respaldo: una muestra que una persona revisa contra el papel, y
un calculo que dice que se puede y que no se puede afirmar con esa muestra.

TRES REGLAS, y el por que de cada una

1. Nunca se reporta un porcentaje solo. Siempre `aciertos / revisados`, con su
   intervalo de confianza. Un "96%" sin denominador es el numero que hay que
   evitar: 24 de 25 y 480 de 500 dan el mismo 96% y no significan lo mismo.
   Con 25 revisiones, el intervalo real es [80%, 99%], asi que un 96% sin
   intervalo afirma una certeza que no existe.

2. Nunca se mezcla un solo numero entre metodos de lectura. Un ticket leido
   con un regex y uno leido con un modelo no se deben promediar: el regex casi
   no falla, y mezclarlo con el modelo levanta el promedio y esconde al
   responsabile. Por eso el reporte es por `confidence_source` siempre.

3. Cuando la evidencia no alcanza, se dice que no alcanza, y cuantos faltan.
   Un sistema que dice "todavia no puedo afirmar el 96%" es mas util que uno
   que dice "96%" y no puede sostenerlo, porque el primero dice que trabajo
   falta y el segundo enga.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field

from app.core.enums import SPOT_CHECK_RATE

# Intervalo al 95%. Es el nivel con el que se toma la decision de "cumple" o
# "no cumple", asi que va aqui y no como parametro por defecto disperso: si
# dos partes del reporte usaran niveles distintos, compararian intervals que no
# son comparables.
NIVEL_CONFIANZA_Z = 1.96

# El mismo 95%, pero como numero que un humano pueda leer. Va al lado del z
# para que si alguien cambia uno, el otro quede a la vista. Decir "95%" en el
# reporte y calcular con 1.96 seria lo mismo por coincidencia; decir los dos
# juntos lo hace lo mismo por diseno.
NIVEL_CONFIANZA = 0.95

# El objetivo acordado. Va como constante y no como parametro de la API
# porque no es negociable por quien consulta el reporte: si se pudiera pedir
# el reporte con cualquier objetivo, cada quien leeria el que le conviene.
SLO_EXACTITUD = 0.96

# Origenes que se reportan siempre, aunque no tengan ninguna revision. Un origen
# con cero evidencia y otro con 300 tienen que verse igual de vacios en el
# reporte: si solo aparecen los que tienen datos, la conclusion "el 96% se
# cumple" se apoya en una base que nadie miro.
ORIGENES_A_REPORTAR = ("llm", "pdf_text", "rules")


class Veredicto:
    """Que se puede concluir con la evidencia reunida."""

    SIN_EVIDENCIA = "SIN_EVIDENCIA"
    CUMPLE = "CUMPLE"
    NO_CUMPLE = "NO_CUMPLE"
    INCONCLUYENTE = "INCONCLUYENTE"


def en_muestra(source_hash: str | None, tasa: float = SPOT_CHECK_RATE) -> bool:
    """Decide si un ticket entra a la muestra, por su hash de contenido.

    La decision se toma por hash y no al azar. Tres consecuencias que valen mas
    que la comodidad de un `random()`:

    - Es reproducible. Volver a cargar el mismo archivo decide lo mismo, asi que
      el muestreo no cambia bajo los pies de quien esta revisando.
    - No se puede seleccionada a conveniencia. Un `ORDER BY random() LIMIT n`
      deja que quien elige la muestra decida, sin darse cuenta, tomar los
      tickets que ya se ven bien. Aqui la eleccion la hace el contenido del
      archivo, que no opiniona.
    - No necesita un job. La marca se escribe al insertar, asi que no hay tabla
      que barrer ni proceso que schedulear.

    Devuelve False sin hash a proposito: los tickets de captura manual no
    tienen hash, y ademas no son automaticos. Muestrearlos seria medir la
    captura manual con la metrica del automatismo.
    """
    if not source_hash:
        return False
    if tasa <= 0:
        return False
    if tasa >= 1:
        return True

    # Se usa el hash COMPLETO, no los primeros 32 bits. Con SHA-256 los
    # primeros 8 hex si son uniformes, asi que un prefijo bastaria; la
    # dependencia es innecesaria y fragil. Si el hash se degrada, o si alguien
    # cambia la forma de calcularlo, un prefijo puede quedar sesgado y el
    # muestreo se rompe en la peor direccion: 100% de los tickets a revisar, o
    # ninguno, sin ningun error a la vista. El entero completo y su resto
    # siguen siendo uniformes con cualquier hash de calidad, y no cuestan mas.
    try:
        valor = int(source_hash, 16)
    except ValueError:
        # No deberia pasar: `compute_source_hash` siempre produce hex. Pero si
        # pasara, el respaldo tiene que ser estable ENTRE PROCESOS, no el
        # `hash()` de Python, que esta sembrado por proceso y daria una
        # decision distinta en cada reinicio. Un ticket que entra y sale de la
        # muestra segun cuando se reinicio el servidor destruye la evidencia.
        valor = zlib.crc32(source_hash.encode("utf-8"))

    return (valor % 10_000) < int(tasa * 10_000)


def intervalo_wilson(
    aciertos: int,
    total: int,
    z: float = NIVEL_CONFIANZA_Z,
) -> tuple[float, float] | None:
    """Intervalo de confianza de Wilson al 95%, o None si no hay muestra.

    Wilson y no el intervalo normal (Wald) por una razon que aqui no es
    academica: Wald se rompe justo en los extremos, que es donde vive este
    sistema. Con 25 de 25 aciertos, Wald da un intervalo de ancho casi cero y
    asegura un 100% exacto, cuando la verdad es que podria haber un 90% real
    que solo que no salio en la muestra. Un metodo que sobredeclara la
    certeza en el caso que mas se necesita prudencia no sirve para decidir.

    Devuelve None con total == 0 en vez de (0.0, 1.0): un intervalo que abarca
    todo no es un intervalo, es la ausencia de informacion, y el reporte tiene
    que poder distinguir una cosa de la otra.
    """
    if total <= 0:
        return None
    if aciertos < 0 or aciertos > total:
        raise ValueError(f"aciertos fuera de rango: {aciertos} de {total}")

    p = aciertos / total
    denominador = 1 + z * z / total
    centro = (p + z * z / (2 * total)) / denominador
    margen = (z / denominador) * math.sqrt(
        p * (1 - p) / total + z * z / (4 * total * total)
    )
    return (
        _ajustar_al_borde(max(0.0, centro - margen)),
        _ajustar_al_borde(min(1.0, centro + margen)),
    )


# Margen de error de coma flotante. Un limite que sale 0.9999999999999999 ES
# 1.0: no es un dato mas preciso, es ruido. Y se nota. Un reporte que dice
# "99.99999999999999%" se ve roto, y quien lo lee sospecha del numero entero
# en lugar de la precision. Se ajusta a 1.0 y a 0.0 cuando esta dentro del
# ruido, y se deja intacto en el medio, que es donde la precision si importa.
BORDE_EPS = 1e-9


def _ajustar_al_borde(valor: float) -> float:
    if valor >= 1.0 - BORDE_EPS:
        return 1.0
    if valor <= BORDE_EPS:
        return 0.0
    return valor


def veredicto(
    aciertos: int,
    total: int,
    objetivo: float = SLO_EXACTITUD,
    z: float = NIVEL_CONFIANZA_Z,
) -> str:
    """Que se puede afirmar con estos datos.

    Se decide por el intervalo entero, no por el punto medio. El punto medio
    puede estar por encima de 96% con un intervalo que llega a 80%: ahi no se
    puede afirmar nada, aunque la cifra neta se vea bien.
    """
    if total <= 0:
        return Veredicto.SIN_EVIDENCIA
    limites = intervalo_wilson(aciertos, total, z)
    assert limites is not None  # total > 0 ya esta garantizado arriba
    bajo, alto = limites
    if bajo >= objetivo:
        return Veredicto.CUMPLE
    if alto < objetivo:
        return Veredicto.NO_CUMPLE
    return Veredicto.INCONCLUYENTE


@dataclass
class FaltanMuestras:
    """Cuantas revisiones faltan para poder afirmar, y por que no se puede.

    Un `int | None` obliga a quien llama a distinguir entre "imposible" y "no
    hay nada que medir", y los dos casos pueden parecer lo mismo: un `None` sin
    explicacion se lee como "aun no" cuando muchas veces significa "nunca, con
    este dato". El reporte necesita decir la diferencia en palabras, asi que la
    razon viaja con el numero.
    """

    total_necesario: int | None
    razon: str

    @property
    def es_posible(self) -> bool:
        return self.total_necesario is not None


class Motivo:
    SIN_MUESTRA = "sin_muestra"
    ACIERTO_EN_LA_LINEA = "acierto_en_la_linea"
    ACIERTO_POR_DEBAJO = "acierto_por_debajo"
    SUFICIENTE = "suficiente"
    FUERA_DE_ALCANCE = "fuera_de_alcance"


def faltantes_para_afirmar(
    aciertos: int,
    total: int,
    objetivo: float = SLO_EXACTITUD,
    z: float = NIVEL_CONFIANZA_Z,
) -> FaltanMuestras:
    """Cuantas revisiones hacen falta, al acierto observado, para poder afirmar.

    Se calcula con el acierto REALmente observado, no con un supuesto. Un
    supuesto ("si el acierto fuera 97%") da un numero que depende de una
    suposicion no verificada, que es justo lo que este modulo existe para
    evitar. Con lo medido hasta ahora, el numero dice exactamente cuanto falta.

    Hay un caso que merece razon propia: cuando el acierto observado esta
    exactamente en el objetivo, 96.0% sobre 96.0%, ninguna muestra extra lo
    resuelve. El limite inferior siempre cae por debajo de la linea cuando el
    punto medio esta encima de ella. No es un margen que se agote: es la
    consecuencia de que 96% no es "mas de 96%". Por eso el reporte lo dice como
    lo que es, en vez de prometer un numero grande que nunca llega.

    Y acierto por debajo del objetivo tampoco se arregla midiendo mas: ahi lo
    que hace falta es arreglar el extractor. Por eso su razon tampoco es
    "faltan N".
    """
    if total <= 0:
        return FaltanMuestras(None, Motivo.SIN_MUESTRA)

    proporcion = aciertos / total
    if proporcion < objetivo:
        return FaltanMuestras(None, Motivo.ACIERTO_POR_DEBAJO)
    if proporcion == objetivo:
        return FaltanMuestras(None, Motivo.ACIERTO_EN_LA_LINEA)

    for n in range(total, 200_001, 10):
        if veredicto(round(proporcion * n), n, objetivo, z) == Veredicto.CUMPLE:
            return FaltanMuestras(n, Motivo.SUFICIENTE)
    return FaltanMuestras(None, Motivo.FUERA_DE_ALCANCE)


@dataclass
class MedidaPorOrigen:
    """Como leyo el sistema un tickets, y cuan bien lo hizo segun la muestra."""

    origen: str
    revisados: int = 0
    aciertos: int = 0
    incorrectos: int = 0
    # Campos que salieron mal, acumulados. Es lo que convierte el muestreo en
    # una lista de que arreglar: sin esto, el reporte dice "hay un 4% de
    # error" y nadie sabe si cambiar el modelo, ajustar un regex o fixear una
    # fecha.
    campos_fallidos: dict[str, int] = field(default_factory=dict)
    # Cantidad de tickets AUTO_APROBADOS que quedaron PENDIENTE en el
    # muestreo. Sirve para ver si la muestra alcanza: si hay 40 pendientes y
    # 25 revisados, el numero de hoy no es el de manana.
    pendientes: int = 0

    @property
    def exactitud(self) -> float | None:
        if self.revisados <= 0:
            return None
        return self.aciertos / self.revisados

    @property
    def limites(self) -> tuple[float, float] | None:
        return intervalo_wilson(self.aciertos, self.revisados)

    @property
    def veredicto(self) -> str:
        return veredicto(self.aciertos, self.revisados)

    @property
    def faltantes(self) -> FaltanMuestras:
        return faltantes_para_afirmar(self.aciertos, self.revisados)

    @property
    def campo_mas_fallido(self) -> tuple[str, int] | None:
        return campo_mas_fallido(self.campos_fallidos)


def campo_mas_fallido(campos: dict[str, int]) -> tuple[str, int] | None:
    """El campo que mas se equivoca, para que el reporte tenga un sujeto.

    Sin esto, "96% de exactitud" es una estadistica. Con esto, "el 96% falla
    sobre todo en la fecha" es una decision.
    """
    if not campos:
        return None
    return max(campos.items(), key=lambda par: par[1])
