"""El informe de cierre mensual: el unico eslabon que faltaba en la cadena.

`AGENTS.md` describe la cadena de custodia asi:

    archivo -> hash -> lectura -> confianza -> muestra del 5% -> veredicto firmado -> informe

Todo lo anterior a "informe" existe. Esto es lo que la cierra, y por eso es la
Fase 1 de `docs/contrato-producto.md`: sin el, esto es una canalizacion de datos
que prepara un insumo y no entrega un documento.

LO QUE ESTE SERVICIO NO HACE
----------------------------
No calcula la exactitud. La **reutiliza** (`accuracy_service.compute_accuracy_report`)
porque una segunda implementacion del mismo numero es la forma mas rapida de
tener dos veredictos distintos: si el informe dice 94% y el tablero dice 92%, el
tablero y el informe se contradicen y no hay forma de saber cual mintio. El
criterio A7 del contrato existe exactamente para que cambiar el servicio cambie
el informe, y se cumple por construccion: aqui no hay aritmetica de exactitud,
hay una llamada.

DETERMINISMO (R4) Y EL RELOJ
---------------------------
Mismo periodo + misma base -> el MISMO objeto, y por tanto el mismo PDF byte a
byte. Eso obliga a una decision incomoda: **este servicio no puede mirar el reloj
para afirmar una cifra.** De ahi que el schema no tenga `emitido_en` sino
`fecha_referencia`, derivado del periodo.

La UNICA excepcion esta declarada y es deliberada: `hoy` se recibe como
parametro y solo se usa para responder "¿este periodo ya termino?". Un periodo a
medio mes se compara contra el mes anterior **cortado al mismo dia**, que es la
comparacion honesta, y el informe lo declara no concluyente. Para todo periodo
completo —el caso normal y el que el contador entrega— el resultado no depende de
cuando se pida.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fpdf import FPDF

from app.core.enums import ExtractionStatus, MatchStatus, SpotCheckStatus
from app.models.cierre_periodo import CierrePeriodoModel
from app.models.company import CompanyModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel
from app.schemas.reporte import (
    ComparativoMes,
    ReporteCierreMensual,
    ResumenGasto,
    SeccionConciliacion,
    SeccionLectura,
    SeccionPendientes,
    SeccionVerificacionHumana,
)
from app.services import accuracy_service, analitica, hallazgos

# El formato del periodo. Va aqui y no solo en el `Query` del router porque el
# servicio tambien se llama desde los tests y desde scripts: una validacion que
# vive unicamente en la capa HTTP deja el resto del sistema aceptando "2026-13".
PATRON_PERIODO = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class PeriodoInvalido(ValueError):
    """El periodo no existe. No es un 422 de pydantic: es del servicio."""


def validar_periodo(periodo: str) -> tuple[int, int]:
    """`(anio, mes)` de un `YYYY-MM`, o `PeriodoInvalido`.

    Mes 13 no existe y `2026-1` no es un periodo: no se "normaliza" ninguno de los
    dos. Un periodo que se corrige solo es un periodo del que nadie sabe cual
    se quiso escribir.
    """
    if not periodo or not PATRON_PERIODO.match(periodo):
        raise PeriodoInvalido(
            f"El periodo {periodo!r} no existe. Se espera AAAA-MM, "
            "por ejemplo 2026-01."
        )
    anio, mes = int(periodo[:4]), int(periodo[5:7])
    return anio, mes


def ventana_del_periodo(periodo: str) -> tuple[datetime, datetime, date]:
    """`(inicio, fin_exclusivo, ultimo_dia)` del periodo.

    `fin` es EXCLUSIVO porque asi comparan las funciones de `analitica`
    (`expense_date < hasta.date()`), y usar el mismo criterio evita el
    `31/12 23:59:59` que duplica el ultimo dia o lo pierde segun el motor.
    """
    anio, mes = validar_periodo(periodo)
    inicio = datetime(anio, mes, 1)
    if mes == 12:
        fin = datetime(anio + 1, 1, 1)
    else:
        fin = datetime(anio, mes + 1, 1)
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    return inicio, fin, date(anio, mes, ultimo_dia)


def _dec(valor) -> Decimal:
    return Decimal(str(valor or 0))


def _pct(diferencia: Decimal, base: Decimal) -> float | None:
    """Variacion porcentual, o `None` si no hay base con que dividir.

    Principio 11: lo que no se pudo calcular se declara. Un `0` de base daria
    `Infinity` o una excepcion, y los dos son peores que un campo vacio: uno se
    imprime y el otro tumba el informe entero.
    """
    if base == 0:
        return None
    return float((diferencia / base) * Decimal("100"))


# ---------------------------------------------------------------------------
# Seccion 2: resumen del gasto y comparativo
# ---------------------------------------------------------------------------

async def _comparativo(
    db: AsyncSession,
    company_id: UUID,
    periodo: str,
    total: Decimal,
    ultimo_dia: date,
    hoy: date,
) -> ComparativoMes:
    """El mes anterior, cortado al mismo dia del mes.

    La fecha de corte es el dia en que termina el periodo, o HOY si el periodo
    todavia no termina. Ese es el unico motivo por el que se pasa `hoy`: comparar
    6 dias contra 31 produce una caida que no existe, y esa es la trampa que
    `analitica.comparacion_a_la_fecha` ya documenta para el tablero. Aqui la
    segunda mitad de esa funcion no se puede reutilizar tal cual porque trabaja
    contra "el mes actual", y este informe es de un periodo arbitrario.
    """
    anio, mes = validar_periodo(periodo)
    en_curso = ultimo_dia > hoy

    if en_curso:
        dia_corte = hoy.day
    else:
        dia_corte = ultimo_dia.day

    # El mes anterior, al mismo dia. `dias_del_mes` porque el dia 31 no existe
    # en febrero y `replace(day=31)` revienta con ValueError.
    if mes == 1:
        ant_anio, ant_mes = anio - 1, 12
    else:
        ant_anio, ant_mes = anio, mes - 1
    dia_ant = min(dia_corte, analitica.dias_del_mes(ant_anio, ant_mes))

    ant_inicio = datetime(ant_anio, ant_mes, 1)
    # `total_hasta` compara `expense_date < hasta.date()`, asi que `hasta` es
    # EXCLUSIVO: el dia de corte va inclusive y por eso el fin es ese dia MAS UNO.
    # Asi el 28 de febrero es "hasta el 28" y no "hasta el 27", y el 31 de un mes
    # de 31 dias no necesita un `23:59:59` que depende del motor.
    ant_fin = datetime(ant_anio, ant_mes, dia_ant) + timedelta(days=1)

    monto_ant, tickets_ant = await analitica.total_hasta(
        db, company_id, ant_inicio, ant_fin
    )

    nombre_anterior = f"{analitica.nombre_mes_largo(ant_mes)} de {ant_anio}"
    diferencia = total - monto_ant
    pct = _pct(diferencia, monto_ant)

    return ComparativoMes(
        nombre_anterior=nombre_anterior,
        monto_anterior=monto_ant,
        tickets_anterior=tickets_ant,
        diferencia=diferencia,
        diferencia_pct=pct,
        # Un mes a medias no se compara contra uno completo. Decirlo es mejor
        # que imprimir la caida: un contador que ve "-80%" busca un problema
        # que no existe, y el informe pierde credibility en la primera pantalla.
        conclusivo=not en_curso,
        nota=(
            f"El periodo sigue abierto: el comparativo va contra {nombre_anterior} "
            f"cortado al dia {dia_corte}. No es concluyente hasta que cierre el mes."
            if en_curso
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Seccion 3: hallazgos
# ---------------------------------------------------------------------------

async def _hallazgos(
    db: AsyncSession, company_id: UUID, inicio: datetime, total: Decimal
) -> list[dict]:
    """Lo que `hallazgos.py` ya sabe decir de la serie, sobre este periodo.

    Se le pasa `ahora=inicio` para que la serie termine en el periodo del informe y
    no en el mes de hoy: un informe de marzo que compara contra junio no es un
    informe de marzo.

    `mes_en_curso=False` porque el corte ya lo hizo `_comparativo`: aqui se
    informa de la serie cerrada, y el mes a medias ya tiene su nota.
    """
    meses = await analitica.comparativo_mensual(
        db, company_id, meses=6, ahora=inicio
    )
    if not meses:
        return []

    nombre_anterior = ""
    if len(meses) >= 2:
        prev = meses[-2]
        nombre_anterior = f"{prev['nombre']} de {prev['anio']}"

    return hallazgos.hallazgos_tendencia(
        meses,
        mes_en_curso=False,
        monto_mes_en_curso=Decimal("0"),
        variacion_a_la_fecha=None,
        nombre_anterior=nombre_anterior,
    )


# ---------------------------------------------------------------------------
# Seccion 4: conciliacion del periodo
# ---------------------------------------------------------------------------

async def _conciliacion(
    db: AsyncSession, company_id: UUID, inicio: datetime, fin: datetime
) -> SeccionConciliacion:
    """Estado de conciliacion de los tickets DEL PERIODO.

    Se cuenta por la fecha del TICKET y no por `matched_at`, que es la fecha en que
    se corrio el matching: un gasto de marzo conciliado en abril es de marzo, y
    contarlo en abril hace que el cierre de marzo salga incompleto aunque se
    hayaconciliado bien.
    """
    filas = (await db.execute(
        select(ReconciliationModel.match_status, func.count(ReconciliationModel.id))
        .join(TicketModel, ReconciliationModel.ticket_id == TicketModel.id)
        .where(
            and_(
                TicketModel.company_id == company_id,
                TicketModel.expense_date >= inicio.date(),
                TicketModel.expense_date < fin.date(),
            )
        )
        .group_by(ReconciliationModel.match_status)
    )).all()

    por_estado = {estado: int(cantidad) for estado, cantidad in filas}

    # Los tres estados reales se reportan aunque esten en cero. Un estado que no
    # aparece se lee como "no aplica"; uno que aparece en cero se lee como "no
    # hubo". Son cosas distintas.
    for estado in (MatchStatus.PERFECT, MatchStatus.MANUAL, MatchStatus.DISCREPANCY):
        por_estado.setdefault(estado.value, 0)

    conciliados = por_estado.get(MatchStatus.PERFECT.value, 0)
    discrepancias = por_estado.get(MatchStatus.DISCREPANCY.value, 0)

    monto_disc = (await db.execute(
        select(func.coalesce(func.sum(TicketModel.total_amount), 0))
        .join(ReconciliationModel, ReconciliationModel.ticket_id == TicketModel.id)
        .where(
            and_(
                TicketModel.company_id == company_id,
                TicketModel.expense_date >= inicio.date(),
                TicketModel.expense_date < fin.date(),
                ReconciliationModel.match_status == MatchStatus.DISCREPANCY.value,
            )
        )
    )).scalar_one()

    total_tickets = (await db.execute(
        select(func.count(TicketModel.id)).where(
            and_(
                TicketModel.company_id == company_id,
                TicketModel.expense_date >= inicio.date(),
                TicketModel.expense_date < fin.date(),
            )
        )
    )).scalar_one()

    return SeccionConciliacion(
        por_estado=por_estado,
        conciliados=conciliados,
        # Lo que tiene ticket pero NO hay conciliacion. El resto no esta conciliado
        # porque no se ha corrido el matching, y esa distincion es la accion.
        sin_conciliar=max(int(total_tickets) - conciliados - por_estado.get(MatchStatus.MANUAL.value, 0), 0),
        monto_discrepancias=_dec(monto_disc),
    )


# ---------------------------------------------------------------------------
# Seccion 6: lectura y verificacion humana
# ---------------------------------------------------------------------------

async def _lectura_y_verificacion(
    db: AsyncSession, company_id: UUID, inicio: datetime, fin: datetime
) -> tuple[SeccionLectura, SeccionVerificacionHumana]:
    """R5 separado en dos campos, y el registro de quien reviso.

    `leido_por_el_sistema` cuenta `AUTO_APROBADO`: el automatismo se hace
    responsable de esa lectura. `verificado_por_una_persona` cuenta `APROBADO` con
    `reviewed_at`: alguien lo miro. Son afirmaciones de distinta fuerza y por eso
    no comparten campo ni etiqueta.
    """
    estados = (await db.execute(
        select(TicketModel.extraction_status, func.count(TicketModel.id))
        .where(
            and_(
                TicketModel.company_id == company_id,
                TicketModel.expense_date >= inicio.date(),
                TicketModel.expense_date < fin.date(),
            )
        )
        .group_by(TicketModel.extraction_status)
    )).all()
    por_estado = {estado: int(cantidad) for estado, cantidad in estados}

    lectura = SeccionLectura(
        leido_por_el_sistema=por_estado.get(ExtractionStatus.AUTO_APROBADO.value, 0),
        verificado_por_una_persona=sum(
            por_estado.get(e.value, 0)
            for e in (ExtractionStatus.APROBADO,)
        ),
        pendiente_de_revision=sum(
            por_estado.get(e.value, 0)
            for e in (
                ExtractionStatus.REQUIERE_REVISION,
                ExtractionStatus.PENDIENTE,
            )
        ),
    )

    muestra_filas = (await db.execute(
        select(TicketModel.spot_check_status, func.count(TicketModel.id))
        .where(
            and_(
                TicketModel.company_id == company_id,
                TicketModel.expense_date >= inicio.date(),
                TicketModel.expense_date < fin.date(),
                TicketModel.spot_check_status.is_not(None),
            )
        )
        .group_by(TicketModel.spot_check_status)
    )).all()
    por_muestra = {estado: int(cantidad) for estado, cantidad in muestra_filas}

    revisados = por_muestra.get(SpotCheckStatus.CORRECTO.value, 0) + por_muestra.get(
        SpotCheckStatus.INCORRECTO.value, 0
    )

    revisores = [r for r in (await db.execute(
        select(TicketModel.spot_checked_by)
        .where(
            and_(
                TicketModel.company_id == company_id,
                TicketModel.expense_date >= inicio.date(),
                TicketModel.expense_date < fin.date(),
                TicketModel.spot_checked_by.is_not(None),
            )
        )
        .distinct()
    )).scalars() if r]

    ultima = (await db.execute(
        select(func.max(TicketModel.spot_checked_at)).where(
            and_(
                TicketModel.company_id == company_id,
                TicketModel.expense_date >= inicio.date(),
                TicketModel.expense_date < fin.date(),
                TicketModel.spot_checked_at.is_not(None),
            )
        )
    )).scalar_one()

    verificacion = SeccionVerificacionHumana(
        en_muestra=sum(por_muestra.values()),
        revisadas=revisados,
        firmadas=revisados,
        pendientes_de_firmar=por_muestra.get(SpotCheckStatus.PENDIENTE.value, 0),
        revisores=sorted(revisores),
        ultima_verificacion=ultima.date() if ultima else None,
    )
    return lectura, verificacion


# ---------------------------------------------------------------------------
# Seccion 7: pendientes
# ---------------------------------------------------------------------------

async def _pendientes(
    db: AsyncSession, company_id: UUID, inicio: datetime, fin: datetime
) -> SeccionPendientes:
    """Lo que impide cerrar el periodo.

    **El "sin categoria" se cuenta SOLO sobre el periodo, y a proposito.**
    `analitica.pendientes_de_clasificar` cuenta todo el historico porque la
    pregunta que responde ahi es "cuanto trabajo tengo pendiente". Aqui la
    pregunta es distinta: "puedo declarar cerrado ESTE mes". Un informe de marzo
    que no cierra porque enero tiene cuatro tickets sin categoria seria un
    informe que nadie puede cerrar nunca, y dejaria de servir para lo unico que
    tiene que servir. Los tickets de enero aparecen en el informe de enero, que
    es donde pueden corregirse.
    """
    sin_cat = (await db.execute(
        select(func.count(TicketModel.id), func.coalesce(func.sum(TicketModel.total_amount), 0))
        .where(
            and_(
                TicketModel.company_id == company_id,
                TicketModel.expense_date >= inicio.date(),
                TicketModel.expense_date < fin.date(),
                or_(
                    TicketModel.category.is_(None),
                    func.trim(TicketModel.category) == "",
                ),
            )
        )
    )).one()

    conciliacion = await _conciliacion(db, company_id, inicio, fin)

    return SeccionPendientes(
        sin_categoria_tickets=int(sin_cat[0] or 0),
        sin_categoria_monto=_dec(sin_cat[1]),
        sin_conciliar_tickets=conciliacion.sin_conciliar,
        sin_conciliar_monto=Decimal("0.00"),
        discrepancias_abiertas=conciliacion.por_estado.get(MatchStatus.DISCREPANCY.value, 0),
        discrepancias_monto=conciliacion.monto_discrepancias,
    )


# ---------------------------------------------------------------------------
# El informe
# ---------------------------------------------------------------------------

async def construir_reporte(
    db: AsyncSession,
    company_id: UUID,
    periodo: str,
    firmado_por: str,
    hoy: date | None = None,
) -> ReporteCierreMensual:
    """El objeto completo. `hoy` es inyectable por determinismo (ver el modulo)."""
    inicio, fin, ultimo_dia = ventana_del_periodo(periodo)
    hoy = hoy or analitica.utcnow().date()

    empresa = (await db.execute(
        select(CompanyModel).where(CompanyModel.id == company_id)
    )).scalar_one_or_none()
    if empresa is None:
        raise LookupError(f"No existe la empresa {company_id}")

    total, tickets = await analitica.total_del_periodo(db, company_id, inicio, fin)
    categorias = await analitica.por_categoria(db, company_id, inicio, fin, total)

    comparativo = await _comparativo(db, company_id, periodo, total, ultimo_dia, hoy)

    conciliacion = await _conciliacion(db, company_id, inicio, fin)
    lectura, verificacion = await _lectura_y_verificacion(db, company_id, inicio, fin)
    pendientes = await _pendientes(db, company_id, inicio, fin)
    lista_hallazgos = await _hallazgos(db, company_id, inicio, total)

    # --- Seccion 5: la exactitud, REUTILIZADA. Nunca reimplementada. ---
    exactitud = None
    advertencia: str | None = None
    try:
        exactitud = await accuracy_service.compute_accuracy_report(db, company_id)
    except Exception:  # noqa: BLE001 - ver abajo
        # R7: una seccion que no se pudo calcular dice "no disponible". Nunca 0%,
        # nunca un numero de la fila anterior, nunca omitirla. Un 0% aqui se
        # leeria como "el sistema fallo en todo el mes", que es una afirmacion
        # distinta y falsa.
        #
        # Se captura `Exception` a proposito y no una excepcion concreta: este
        # servicio no debe ser el que decide que errores del reporte de
        # exactitud son "esperados". Un error raro que se traga aqui produce un
        # informe honesto con una seccion vacia; uno que sube tumba el informe
        # entero, que es lo que el contrato §11 prohibe.
        exactitud = None
        advertencia = (
            "La exactitud de lectura NO ESTA DISPONIBLE para este periodo. "
            "No se puede afirmar ni affirmar en contra."
        )

    if exactitud is not None and exactitud.veredicto_global != "CUMPLE":
        # R2: en la PRIMERA pagina, no como nota al pie.
        base = (
            f"La evidencia disponible NO alcanza para afirmar el objetivo de "
            f"exactitud ({exactitud.objetivo:.0%}). Veredicto: {exactitud.veredicto_global}."
        )
        advertencia = f"{base} {advertencia}" if advertencia else base

    cierre = (await db.execute(
        select(CierrePeriodoModel).where(
            and_(
                CierrePeriodoModel.company_id == company_id,
                CierrePeriodoModel.periodo == periodo,
            )
        )
    )).scalar_one_or_none()

    return ReporteCierreMensual(
        empresa_id=str(empresa.id),
        empresa_nombre=empresa.name,
        empresa_tax_id=empresa.tax_id,
        periodo=periodo,
        periodo_inicio=inicio.date(),
        periodo_fin=ultimo_dia,
        fecha_referencia=ultimo_dia,
        firmado_por=firmado_por,
        gasto=ResumenGasto(
            total=total,
            tickets=tickets,
            por_categoria=categorias,
            comparativo=comparativo,
        ),
        hallazgos=lista_hallazgos,
        conciliacion=conciliacion,
        exactitud=exactitud,
        exactitud_disponible=exactitud is not None,
        advertencia_principal=advertencia,
        lectura=lectura,
        verificacion=verificacion,
        pendientes=pendientes,
        cerrado=cierre is not None,
        cerrado_por=cierre.cerrado_por if cierre else None,
    )


# ---------------------------------------------------------------------------
# El PDF
# ---------------------------------------------------------------------------
#
# `fpdf2` con las fuentes core de PDF (Helvetica) y NO una fuente Unicode
# embebida, por dos razones que no son esteticas:
#
#  1. **Determinismo (R4).** Una fuente Unicode viene de un archivo binario. Si
#     dos maquinas tienen versiones distintas de ese archivo, el mismo informe
#     produce bytes distintos, y la garantia de "mismo periodo, mismos bytes"
#     deja de valer sin que nadie notice por que.
#  2. **Sin dependencias.** Una fuente embebida seria un binario mas en el repo.
#     Las core son del propio formato PDF.
#
# El precio es que las core son latin-1, y eso obliga a `_latin1()`.

# Sustituciones explicitas, y NO una transliteracion generica. Cada entrada es un
# caracter que el codigo de este servicio emite y latin-1 no tiene.
_TRADUCCIONES = str.maketrans({
    "→": "->",   # la flecha de la cadena de custodia
    "—": "-",    # raya larga
    "–": "-",    # raya corta
    "“": '"', "”": '"',
    "‘": "'", "’": "'",
    "…": "...",
    "×": "x",
    "·": "-",
    "≈": "~",
    "±": "+/-",
    "°": " grados",
})

NO_DISPONIBLE = "no disponible"


def _latin1(texto: str) -> str:
    """Deja el texto en latin-1 sin romper el PDF.

    **Un `?` es preferible a una excepcion a media pagina.** Un nombre de
    proveedor con un emoji o un caracter de otro alfabeto no debe tumbar el
    informe entero: el contador necesita el resto de las secciones mas que una
    falta de tilde en una razon social. Y un `?` en medio de un nombre es visible:
    se nota, y se puede preguntar. Un `None` silencioso no.

    Los importes NUNCA pasan por aqui con caracteres raros: son `Decimal`, y el
    redondeo se hace con `:` y punto. Lo que se sanitiza son etiquetas y razones
    sociales, que son texto libre.
    """
    limpio = texto.translate(_TRADUCCIONES)
    return limpio.encode("latin-1", errors="replace").decode("latin-1")


def _dinero(monto: Decimal | None) -> str:
    """Un importe con separador de miles y dos decimales.

    `Decimal` en todo el camino (A6). El `f"{monto:,.2f}"` de un `float` ya
    habria perdido centavos antes de llegar aqui, y este es el ultimo sitio donde
    se decide el redondeo que ve el contador.
    """
    if monto is None:
        return NO_DISPONIBLE
    return f"${monto:,.2f}"


# El sello de fecha del PDF. `fpdf2` lo pone SIEMPRE y con `datetime.now()`, lo que
# hace que dos descargas del mismo periodo difieran en un minuto. Se fija al ultimo
# dia del periodo, que sale del propio dato y no del reloj: por eso R4 se cumple
# y por eso el campo del schema se llama `fecha_referencia` y no `emitido_en`.
def _fecha_sello(periodo_fin: date) -> datetime:
    return datetime(periodo_fin.year, periodo_fin.month, periodo_fin.day)


def generar_pdf(reporte: ReporteCierreMensual) -> bytes:
    """Las 7 secciones del contrato, en orden, en el PDF.

    El objeto que entra es el MISMO que el del JSON. Aqui no se recalcula nada:
    si el PDF dijera 92% y el tablero 94%, la causa seria que este metodo calcula
    su propia exactitud, y por eso no calcula ninguna.
    """
    pdf = FPDF(format="letter")
    pdf.set_creation_date(_fecha_sello(reporte.periodo_fin))
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    pdf.add_page()
    ancho = pdf.w - 30

    def titulo(txt: str) -> None:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(ancho, 8, _latin1(txt), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(180, 180, 180)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(2)

    def parrafo(txt: str, size: float = 10) -> None:
        pdf.set_font("Helvetica", "", size)
        pdf.multi_cell(ancho, 5.2, _latin1(txt), new_x="LMARGIN", new_y="NEXT")

    def fila(etiqueta: str, valor: str) -> None:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(ancho * 0.45, 5.4, _latin1(etiqueta))
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(
            ancho * 0.55, 5.4, _latin1(valor), new_x="LMARGIN", new_y="NEXT"
        )

    # --- Encabezado ---
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(ancho, 9, _latin1("Cierre mensual de gastos"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(
        ancho, 5,
        _latin1(f"{reporte.empresa_nombre} · {reporte.empresa_tax_id} · {reporte.periodo}"),
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(3)

    # --- R2: la advertencia del veredicto, EN LA PRIMERA PAGINA ---
    #
    # Va antes que cualquier cifra y sin caja ni gris: si la evidencia no alcanza,
    # eso es lo primero que se lee. Como nota al pie el lector llegaria al
    # conclusion equivocada antes de llegar a la nota.
    if reporte.advertencia_principal:
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(
            ancho, 5.2, _latin1("AVISO"), new_x="LMARGIN", new_y="NEXT"
        )
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(
            ancho, 5.2, _latin1(reporte.advertencia_principal),
            new_x="LMARGIN", new_y="NEXT",
        )
        pdf.ln(3)

    # --- 1. Identificacion ---
    titulo("1. Identificacion")
    fila("Empresa", reporte.empresa_nombre)
    fila("RFC", reporte.empresa_tax_id)
    fila("Periodo", f"{reporte.periodo} ({reporte.periodo_inicio} a {reporte.periodo_fin})")
    fila("Fecha de referencia", str(reporte.fecha_referencia))
    fila("Firmado por", reporte.firmado_por)
    fila("Estado del periodo", reporte.estado_periodo)
    # El motivo SOLO si existe. Con `pendientes.motivo` a `None` esto imprimia
    # literalmente "El periodo NO se puede declarar cerrado: None." — que es un
    # dato inventado en un documento firmado, y exactamente lo que R6 prohibe.
    if reporte.pendientes.motivo:
        fila(
            "Motivo",
            f"El periodo NO se puede declarar cerrado: {reporte.pendientes.motivo}.",
        )
    if reporte.cerrado and reporte.cerrado_por:
        fila("Cerrado por", reporte.cerrado_por)

    # --- 2. Resumen del gasto ---
    titulo("2. Resumen del gasto")
    fila("Total del periodo", _dinero(reporte.gasto.total))
    fila("Comprobantes", str(reporte.gasto.tickets))

    comp = reporte.gasto.comparativo
    if comp is None:
        fila("Comparativo", NO_DISPONIBLE)
    else:
        fila(f"Contra {comp.nombre_anterior}", _dinero(comp.monto_anterior))
        if comp.diferencia is not None:
            signo = "+" if comp.diferencia > 0 else ""
            fila("Diferencia", f"{signo}{_dinero(comp.diferencia).lstrip('$')}")
        if comp.diferencia_pct is not None and comp.conclusivo:
            # R1 no aplica aqui: esto no es una estimacion de exactitud, es el
            # cociente de dos cantidades que ya estan medidas. El intervalo que
            # R1 exige es para porcentajes de aciertos, no para una variacion
            # entre dos meses cerrados.
            fila("Variacion", f"{comp.diferencia_pct:+.1f}%")
        if comp.nota:
            parrafo(comp.nota, size=9)

    if reporte.gasto.por_categoria:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(ancho, 5.4, _latin1("Por categoria"))
        for cat in reporte.gasto.por_categoria:
            nombre = cat.get("categoria") or "sin clasificar"
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(ancho * 0.6, 5.2, _latin1(str(nombre)))
            pdf.cell(ancho * 0.25, 5.2, _dinero(cat.get("monto")), align="R")
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(
                ancho * 0.15, 5.2,
                _latin1(f"{int(cat.get('tickets') or 0)} c."),
                new_x="LMARGIN", new_y="NEXT",
            )

    # --- 3. Hallazgos ---
    titulo("3. Hallazgos")
    if not reporte.hallazgos:
        # R7: se dice que no hay, no se omite la seccion. Un vacio sin explicar
        # se lee como "no se calculo".
        parrafo("No hay hallazgos con la evidencia disponible.")
    for h in reporte.hallazgos:
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(ancho, 5.2, _latin1(str(h.get("titulo", ""))),
                       new_x="LMARGIN", new_y="NEXT")
        parrafo(str(h.get("detalle", "")), size=9)
        pdf.ln(1)

    # --- 4. Estado de conciliacion ---
    titulo("4. Estado de conciliacion")
    for estado in (MatchStatus.PERFECT, MatchStatus.MANUAL, MatchStatus.DISCREPANCY):
        n = reporte.conciliacion.por_estado.get(estado.value, 0)
        fila(estado.value, str(n))
    if reporte.conciliacion.monto_discrepancias:
        fila(
            "Monto en discrepancia",
            _dinero(reporte.conciliacion.monto_discrepancias),
        )
    parrafo(
        "DISCREPANCY no cuenta como conciliado: hay un movimiento en la fecha "
        "correcta con otro monto, y eso lo tiene que ver una persona.",
        size=9,
    )

    # --- 5. Bloque de exactitud. EL CORAZON ---
    titulo("5. Exactitud de la lectura automatica")
    if reporte.exactitud is None:
        # R7: "no disponible". NUNCA "0%".
        parrafo(
            "Exactitud NO DISPONIBLE. No se puede afirmar nada en contra ni a "
            "favor: la consulta no se pudo completar."
        )
    else:
        ex = reporte.exactitud
        fila("Objetivo", f"{ex.objetivo:.0%}")
        fila("Nivel de confianza", f"{ex.nivel_confianza:.0%}")
        fila("Veredicto global", ex.veredicto_global)
        parrafo(ex.explicacion, size=9)
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(ancho, 5.4, _latin1("Por via de lectura"))
        for o in ex.por_origen:
            pdf.ln(1)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(ancho, 5, _latin1(str(o.origen)))
            pdf.set_font("Helvetica", "", 9)
            # R1: un porcentaje sin su intervalo al lado no significa nada.
            # Los tres van juntos o no va ninguno.
            if o.exactitud is None:
                linea = (
                    f"{o.revisados} revisados · exactitud "
                    f"{NO_DISPONIBLE} (sin evidencia)"
                )
            else:
                linea = (
                    f"{o.revisados} revisados · {o.exactitud:.1%} "
                    f"[IC 95%: {o.intervalo_inferior:.1%} - "
                    f"{o.intervalo_superior:.1%}]"
                )
            pdf.multi_cell(ancho, 5, _latin1(linea), new_x="LMARGIN", new_y="NEXT")
            if o.campo_mas_fallido:
                parrafo(
                    f"El campo que mas falla en esta via: {o.campo_mas_fallido}.",
                    size=8,
                )
            if o.motivo_faltante:
                parrafo(_latin1(o.motivo_faltante), size=8)

    # --- 6. Registro de verificacion humana ---
    titulo("6. Registro de verificacion humana")
    # R5: las dos etiquetas son distintas y no se pueden confundir. "Leido por el
    # sistema" y "verificado por una persona" son afirmaciones de distinta fuerza.
    fila("Leido por el sistema (automatico)", str(reporte.lectura.leido_por_el_sistema))
    fila(
        "Verificado por una persona",
        str(reporte.lectura.verificado_por_una_persona),
    )
    fila("Pendiente de revision", str(reporte.lectura.pendiente_de_revision))
    pdf.ln(2)
    fila("En la muestra de verificacion", str(reporte.verificacion.en_muestra))
    fila("Revisadas", str(reporte.verificacion.revisadas))
    fila("Firmadas", str(reporte.verificacion.firmadas))
    fila("Pendientes de firmar", str(reporte.verificacion.pendientes_de_firmar))
    if reporte.verificacion.revisores:
        fila("Revisores", ", ".join(reporte.verificacion.revisores))
    if reporte.verificacion.ultima_verificacion:
        fila(
            "Ultima verificacion",
            str(reporte.verificacion.ultima_verificacion),
        )
    if not reporte.verificacion.en_muestra:
        parrafo(
            "No hay ninguna lectura automatica en la muestra de este periodo. "
            "Sin muestras no hay exactitud que afirmar.",
            size=9,
        )

    # --- 7. Pendientes ---
    titulo("7. Pendientes")
    p = reporte.pendientes
    if not p.hay_pendientes:
        parrafo("No hay pendientes: no hay tickets sin categoria, sin conciliar ni "
                "discrepancias abiertas en este periodo.")
    else:
        fila("Sin categoria", f"{p.sin_categoria_tickets} ({_dinero(p.sin_categoria_monto)})")
        fila("Sin conciliar", f"{p.sin_conciliar_tickets} ({_dinero(p.sin_conciliar_monto)})")
        fila(
            "Discrepancias abiertas",
            f"{p.discrepancias_abiertas} ({_dinero(p.discrepancias_monto)})",
        )
        parrafo(
            f"Por eso este periodo NO se declara cerrado: {p.motivo}.",
            size=9,
        )

    pdf.ln(6)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(
        ancho, 4.4,
        _latin1(
            "Documento de soporte interno. No es un comprobante fiscal ante el SAT "
            "y no sustituye el criterio profesional de un contador."
        ),
        new_x="LMARGIN", new_y="NEXT",
    )

    # `pdf.output()` devuelve un `bytearray` en fpdf2 2.8, no `bytes`. Pasarlo tal
    # cual a un `Response(content=...)` revienta con
    # `'bytearray' object has no attribute 'encode'` dentro de starlette, y el
    # sintoma es un 500 en una ruta cuyo JSON si funciona bien.
    return bytes(pdf.output())


def nombre_del_pdf(reporte: ReporteCierreMensual) -> str:
    """`cierre_<rfc>_<periodo>.pdf`, segun §5.2.

    El RFC va al nombre porque es lo que el contador reconoce de un vistazo en una
    carpeta con veinte cierres, y lo que evita el error de guardar el pdf del
    cliente equivocado.
    """
    rfc = (reporte.empresa_tax_id or "sin-rfc").replace(" ", "")
    return f"cierre_{rfc}_{reporte.periodo}.pdf"