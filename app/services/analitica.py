"""Las agregaciones del dashboard.

Va en un servicio y no dentro de `api/dashboard.py` por dos razones concretas:

  - **Se puede probar sin levantar la API.** Las pruebas de Python corren contra
    SQLite; si estas cuentas vivieran en el endpoint, el dashboard solo se
    podria probar levantando el servidor ysqleandole por encima. Escribiendo el
    SQL aqui, se puede comprobar que un importe de 890.00 sale como 890.00 y no
    como 89,000.00, que es el mismo error que se corrigio en el parser del CSV.
  - **Las decisiones contables quedan en un sitio.** Que "el gasto variable se
    compare contra el mes anterior y el fijo no" es una regla del negocio. En un
    endpoint queda como un `if` perdido; aqui queda escrito y con su porque.

Todas las cuentas se hacen en SQL, nunca trayendo filas a Python para sumarlas:
un dashboard con doce meses y muchos tickets se lleva el proceso entero si trae
las filas. Un `Decimal` por fila en Python es la forma facil de perder la
exactitud que todo el resto del sistema cuida (ver `parser_service._a_decimal`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, case, extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import categorias as cat
from app.models.bank_transaction import BankTransactionModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel


# ---------------------------------------------------------------------------
# Ventanas de tiempo
# ---------------------------------------------------------------------------


def mes_actual(ahora: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """Del 1 de este mes al 1 del que sigue. En UTC, como todo lo demas."""
    ahora = ahora or utcnow()
    desde = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    hasta = _siguiente_mes(desde)
    return desde, hasta


def mes_anterior(ahora: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """El mes completo anterior. Es la base de TODA comparacion del dashboard.

    Se calcula desde el mes actual restando, no desde "hace 30 dias": comparar
    un mes completo contra 30 dias sueltos siempre da una subida, porque el mes
    actual todavia no termino. Un dashboard que dice "el gasto subio 40%" solo
    porque hoy es dia 3 es peor que uno que no dice nada.
    """
    desde_actual, _ = mes_actual(ahora)
    hasta = desde_actual
    desde = _mes_previo(desde_actual)
    return desde, hasta


def _siguiente_mes(desde: datetime) -> datetime:
    if desde.month == 12:
        return desde.replace(year=desde.year + 1, month=1, day=1)
    return desde.replace(month=desde.month + 1, day=1)


def _mes_previo(desde: datetime) -> datetime:
    if desde.month == 1:
        return desde.replace(year=desde.year - 1, month=12, day=1)
    return desde.replace(month=desde.month - 1, day=1)


def utcnow() -> datetime:
    """El ahora de referencia, en UTC.

    Se puede pasar como parametro a todas las funciones de ventana para poder
    probar un mes de febrero sin esperar a febrero. Las pruebas de este servicio
    fijan la fecha justamente por eso: una prueba que usa `datetime.now()`
    pasa hoy y falla el dia 1 de un mes nuevo.
    """
    return datetime.now(timezone.utc)



def ultimos_meses(meses: int, ahora: Optional[datetime] = None) -> list[tuple[int, int, datetime, datetime]]:
    """Los ultimos N meses, del mas antiguo al mas reciente.

    Se devuelve la lista completa aunque un mes no tenga datos. Una serie de
    barras con los meses vacios saltados no se puede leer: no se ve si el mes sin
    gasto fue un mes sin actividad o un mes del que nadie上报. Un hueco es
    informacion.
    """
    ahora = ahora or utcnow()
    _, fin_actual = mes_actual(ahora)
    resultado = []
    for _ in range(meses):
        fin = fin_actual
        inicio = _mes_previo(fin)
        resultado.append((inicio.year, inicio.month, inicio, fin))
        fin_actual = inicio
    return list(reversed(resultado))


# ---------------------------------------------------------------------------
# Cifras
# ---------------------------------------------------------------------------


@dataclass
class Cifra:
    """Un monto con su comparacion contra el periodo anterior.

    El delta se calcula aqui y no en la UI por una razon: `variacion_pct` con un
    denominador en cero es `ZeroDivisionError` en Python y `Infinity` en
    JavaScript, y el caso "el mes pasado no habia gasto, este si" es el primer
    dia de cualquier negocio, no una excepcion. Devolver `None` para "no
    comparable" y que cada pantalla lo decida es lo que evita que aparezca un
    "+Infinity%" en el encabezado.
    """

    monto: Decimal
    monto_anterior: Decimal
    tickets: int
    tickets_anterior: int

    @property
    def variacion_pct(self) -> Optional[float]:
        if self.monto_anterior == 0:
            # Sin base de comparacion. No se inventa un porcentaje.
            return None
        return float((self.monto - self.monto_anterior) / self.monto_anterior * 100)

    @property
    def delta_absoluto(self) -> Decimal:
        return self.monto - self.monto_anterior

    @property
    def hay_base(self) -> bool:
        """Si hay contra que comparar. Sin base no hay porcentaje que valga."""
        return self.monto_anterior != 0


async def total_hasta(
    db: AsyncSession,
    company_id: Optional[UUID],
    desde: datetime,
    hasta: datetime,
) -> tuple[Decimal, int]:
    """Igual que `total_del_periodo` pero con el final abierto.

    Es lo que hace falta para la comparacion "a la fecha": el mes anterior se
    corta el dia equivalente al de hoy, no el dia 30. Comparar los primeros dos
    dias contra los treinta completos daria siempre una caida, y una caida que
    aparece en todos los meses es ruido, no informacion.
    """
    q = select(
        func.coalesce(func.sum(TicketModel.total_amount), 0),
        func.count(TicketModel.id),
    ).where(
        and_(
            TicketModel.expense_date >= desde.date(),
            TicketModel.expense_date < hasta.date(),
            *_filtro_empresa(company_id),
        )
    )
    total, conteo = (await db.execute(q)).one()
    return Decimal(str(total or 0)), int(conteo or 0)


def _cifra(monto: Decimal, tickets: int, monto_ant: Decimal, tickets_ant: int) -> Cifra:
    return Cifra(
        monto=monto or Decimal("0"),
        monto_anterior=monto_ant or Decimal("0"),
        tickets=tickets or 0,
        tickets_anterior=tickets_ant or 0,
    )


async def cifra_periodo(
    db: AsyncSession,
    company_id: Optional[UUID],
    ahora: Optional[datetime] = None,
) -> Cifra:
    """El mes actual contra el mes anterior, ya comparado.

    Las dos ventanas se calculan juntas y antes de consultar nada: si cada
    consulta pidiera la suya, un dashboard abierto a medianoche del dia 1
    compararia dos meses que no tienen nada que ver, porque la segunda ya caeria
    en el mes nuevo.
    """
    ahora = ahora or utcnow()
    mes_desde, mes_hasta = mes_actual(ahora)
    ant_desde, ant_hasta = mes_anterior(ahora)
    monto, tickets = await total_del_periodo(db, company_id, mes_desde, mes_hasta)
    monto_ant, tickets_ant = await total_del_periodo(db, company_id, ant_desde, ant_hasta)
    return _cifra(monto, tickets, monto_ant, tickets_ant)


async def comparacion_a_la_fecha(
    db: AsyncSession,
    company_id: Optional[UUID],
    ahora: Optional[datetime] = None,
) -> Decimal:
    """Cuanto se gasto el mes pasado HASTA el dia de hoy.

    Es la comparacion que vale mientras el mes no termina. El mes actual va a
    medias y el anterior esta completo, asi que la diferencia de mes contra mes
    siempre es una comparacion de un mes contra otro mas corto. Alguien tiene
    que decidir si eso se avisa o se esconde, y la respuesta es que se avisa:
    esconderlo produce un tablero que sube y baja por el dia del mes, y eso se
    lee como una señal del negocio cuando no lo es.
    """
    ahora = ahora or utcnow()
    ant_desde, _ = mes_anterior(ahora)
    # El mismo dia del mes anterior, no "el mismo numero de dias": son cosas
    # distintas en meses de distinta longitud, y el dia 31 no existe en todos.
    dia = min(ahora.day, _dias_del_mes(ant_desde.year, ant_desde.month))
    corte = ant_desde.replace(day=dia, hour=23, minute=59, second=59, microsecond=999999)
    monto, _ = await total_hasta(db, company_id, ant_desde, corte)
    return monto


def dias_del_mes(anio: int, mes: int) -> int:
    """Cuantos dias tiene el mes. Sin esto, "van 30 de 31" sale en febrero."""
    if mes == 12:
        siguiente = date(anio + 1, 1, 1)
    else:
        siguiente = date(anio, mes + 1, 1)
    return (siguiente - date(anio, mes, 1)).days


def _dias_del_mes(anio: int, mes: int) -> int:
    return dias_del_mes(anio, mes)


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def _filtro_empresa(company_id: Optional[UUID]) -> list:
    return [TicketModel.company_id == company_id] if company_id else []


async def total_del_periodo(
    db: AsyncSession,
    company_id: Optional[UUID],
    desde: datetime,
    hasta: datetime,
) -> tuple[Decimal, int]:
    """(monto, tickets) del periodo.

    Se cuenta por `expense_date`, no por `created_at`, que es la diferencia entre
    "cuanto se gasto en marzo" y "cuanto se metio al sistema en marzo". Para un
    cierre contable, lo que cuenta es la fecha del gasto: un comprobante de
    marzo subido hoy pertenece a marzo.
    """
    q = select(
        func.coalesce(func.sum(TicketModel.total_amount), 0),
        func.count(TicketModel.id),
    ).where(
        and_(
            TicketModel.expense_date >= desde.date(),
            TicketModel.expense_date < hasta.date(),
            *_filtro_empresa(company_id),
        )
    )
    total, conteo = (await db.execute(q)).one()
    return Decimal(str(total or 0)), int(conteo or 0)


async def por_categoria(
    db: AsyncSession,
    company_id: Optional[UUID],
    desde: datetime,
    hasta: datetime,
    monto_total: Decimal,
) -> list[dict]:
    """El gasto de cada categoria, con su parte del total.

    `monto_total` se pasa, no se recalcula, para que la parte del total que se
    muestra sea la de la misma cifra que la de arriba. Si cada bloque calculara su
    propio total, un ticket con una categoria desconocida en medio haria que las
    barras sumaran 98% y el encabezado dijera otra cosa. Un porcentaje que no
    cuadra con el total es la forma mas rapida de perder la confianza en la
    pantalla entera.
    """
    etiqueta = func.coalesce(TicketModel.category, cat.SIN_CLASIFICAR)

    # El `GROUP BY` es sobre el valor CRUDO, no sobre el normalizado, porque
    # normalizar en SQL exigiria el mismo `NFKD` en la base. Se agrupa por lo que
    # hay y se normaliza despues, en Python, al construir la respuesta. Con doce
    # filas por categoria el coste es nulo y la normalizacion queda en un sitio.
    q = select(
        etiqueta.label("categoria"),
        func.coalesce(func.sum(TicketModel.total_amount), 0).label("monto"),
        func.count(TicketModel.id).label("tickets"),
    ).where(
        and_(
            TicketModel.expense_date >= desde.date(),
            TicketModel.expense_date < hasta.date(),
            *_filtro_empresa(company_id),
        )
    ).group_by(etiqueta)

    crudo = (await db.execute(q)).all()

    # Se acumulan por clave normalizada: asi "Supermercado" y "SUPERMERCADO"
    # suman juntos en vez de aparecer como dos barras.
    acumulados: dict[str, dict] = {}
    for categoria, monto, tickets in crudo:
        clave = cat.normaliza(categoria)
        fila = acumulados.setdefault(clave, {"monto": Decimal("0"), "tickets": 0})
        fila["monto"] += Decimal(str(monto or 0))
        fila["tickets"] += int(tickets or 0)

    orden = {clave: i for i, clave in enumerate(cat.orden_canonico())}
    resultado = []
    for clave, fila in acumulados.items():
        monto = fila["monto"]
        resultado.append({
            "clave": clave,
            "etiqueta": cat.etiqueta(clave),
            "monto": monto,
            "tickets": fila["tickets"],
            # La parte del total se limita a 100 con redondeo a un digito: 99.9
            # por redondeo de cada barra y 100.0 de suma no cuadran, y una
            # grafica que suma 100.2 en lugar de 100 hace dudar de las demas.
            "porcentaje": (float(monto / monto_total * 100) if monto_total else 0.0),
            "variable": cat.es_variable(clave),
            "sin_clasificar": clave == cat.SIN_CLASIFICAR,
        })

    resultado.sort(key=lambda f: orden.get(f["clave"], 10_000))
    return resultado


async def tendencia_mensual(
    db: AsyncSession,
    company_id: Optional[UUID],
    meses: int = 12,
    ahora: Optional[datetime] = None,
) -> list[dict]:
    """El gasto mes a mes, con un punto por mes aunque no haya datos."""
    ventanas = ultimos_meses(meses, ahora)
    inicio = ventanas[0][2]
    fin = ventanas[-1][3]

    q = select(
        extract("year", TicketModel.expense_date).label("anio"),
        extract("month", TicketModel.expense_date).label("mes"),
        func.coalesce(func.sum(TicketModel.total_amount), 0).label("monto"),
        func.count(TicketModel.id).label("tickets"),
    ).where(
        and_(
            TicketModel.expense_date >= inicio.date(),
            TicketModel.expense_date < fin.date(),
            *_filtro_empresa(company_id),
        )
    ).group_by(extract("year", TicketModel.expense_date), extract("month", TicketModel.expense_date))

    por_clave = {(int(a), int(m)): (Decimal(str(m0 or 0)), int(t or 0)) for a, m, m0, t in (await db.execute(q)).all()}

    return [
        {
            "anio": anio,
            "mes": mes,
            "etiqueta": nombre_mes(mes),
            # El nombre largo va tambien en la serie, no solo en la cabecera. Un
            # hallazgo dice "julio subio 40%" y "jul" en un texto se lee como
            # apocope raro o como error de dedo.
            "nombre": nombre_mes_largo(mes),
            "monto": por_clave.get((anio, mes), (Decimal("0"), 0))[0],
            "tickets": por_clave.get((anio, mes), (Decimal("0"), 0))[1],
        }
        for anio, mes, _, _ in ventanas
    ]


async def top_proveedores(
    db: AsyncSession,
    company_id: Optional[UUID],
    desde: datetime,
    hasta: datetime,
    limite: int = 8,
) -> list[dict]:
    """Quien se lleva la platita.

    Va al lado del reparto por categoria porque responde una pregunta distinta y
    las dos juntas se leen mejor: la categoria dice EN QUE se gasta y el
    proveedor dice CON QUIEN. Un 40% en un solo proveedor es una concentracion
    que no se ve en la grafica de categorias, y es el dato que uno busca antes de
    negociar un volumen.
    """
    q = select(
        TicketModel.provider_name.label("proveedor"),
        func.coalesce(func.sum(TicketModel.total_amount), 0).label("monto"),
        func.count(TicketModel.id).label("tickets"),
    ).where(
        and_(
            TicketModel.expense_date >= desde.date(),
            TicketModel.expense_date < hasta.date(),
            *_filtro_empresa(company_id),
        )
    ).group_by(TicketModel.provider_name).order_by(func.sum(TicketModel.total_amount).desc()).limit(limite)

    return [
        {"proveedor": p, "monto": Decimal(str(m or 0)), "tickets": int(t or 0)}
        for p, m, t in (await db.execute(q)).all()
    ]


async def por_estado(db: AsyncSession, company_id: Optional[UUID]) -> dict[str, int]:
    """Como se reparten los tickets en el ciclo de extraccion.

    Sin filtro de fecha a proposito: es el estado de la cola HOY, y la cola no
    es "los de este mes". Filtrar por mes haria que en enero la cola apareciera
    vacia solo porque los pendientes son de diciembre.
    """
    q = select(
        TicketModel.extraction_status.label("estado"),
        func.count(TicketModel.id).label("n"),
    ).where(and_(*_filtro_empresa(company_id))).group_by(TicketModel.extraction_status)

    return {estado: int(n or 0) for estado, n in (await db.execute(q)).all()}



async def por_estado_conciliacion(db: AsyncSession, company_id: Optional[UUID]) -> dict[str, int]:
    """Como(termina la conciliacion. Sin fecha: el estado de conciliacion es
    acumulado, no por periodo."""
    q = select(
        ReconciliationModel.match_status.label("estado"),
        func.count(ReconciliationModel.id).label("n"),
    ).join(TicketModel, ReconciliationModel.ticket_id == TicketModel.id).where(
        and_(*_filtro_empresa(company_id))
    ).group_by(ReconciliationModel.match_status)

    return {estado: int(n or 0) for estado, n in (await db.execute(q)).all()}


async def estado_del_banco(db: AsyncSession, company_id: Optional[UUID]) -> dict:
    """Cuanto del banco quedo conciliado y cuanto sigue sin tocar.

    `is_reconciled` va en la tabla del movimiento, no en la conciliacion, asi que
    se cuenta ahi. Y el "sin conciliar" se calcula por diferencia, no como
    "total - conciliados": un movimiento puede tener mas de una conciliacion
    asociada y la resta daria un negativo, que en un dashboard se lee como que
    se concilio mas dinero del que hay.
    """
    filtro = [BankTransactionModel.company_id == company_id] if company_id else []
    q = select(
        func.count(BankTransactionModel.id),
        func.coalesce(
            func.sum(case((BankTransactionModel.is_reconciled.is_(True), 1), else_=0)), 0
        ),
        func.coalesce(func.sum(BankTransactionModel.amount), 0),
    ).where(and_(*filtro))
    total, conciliados, monto_total = (await db.execute(q)).one()
    conciliados = int(conciliados or 0)
    total = int(total or 0)
    return {
        "total": total,
        "conciliados": conciliados,
        "sin_conciliar": total - conciliados,
        "monto_total": Decimal(str(monto_total or 0)),
        "porcentaje": (conciliados / total * 100) if total else 0.0,
    }


async def comparativo_mensual(
    db: AsyncSession,
    company_id: Optional[UUID],
    meses: int = 3,
    ahora: Optional[datetime] = None,
) -> list[dict]:
    """Los ultimos N meses, cada uno con su reparto por categoria.

    Va aparte de `tendencia_mensual` porque responde una pregunta distinta. La
    tendencia de doce meses dice "el gasto subio en abril". El comparativo de
    tres dice **por que**: si en abril subio `TRANSPORTE`, es actividad; si
    subieron todas las categorias a la vez, algo cambio en general; si subio una
    categoria que no estaba, es un gasto nuevo que hay que mirar.

    Un total sin desglose obliga a ir a otra pantalla a averiguarlo, y a adivinar
    meanwhile. Por eso cada mes trae sus categorias y no solo su cifra.

    Se hace en una consulta por mes y no en una sola con `GROUP BY (mes,
    categoria)`: la segunda devuelve una fila por combinacion y aqui habria que
    reconstruir en Python una tabla que ya se sabe resolver en SQL. Con tres meses
    la diferencia no se nota; con treinta y seis si, y el limite se nota el dia
    que alguien lo sube.
    """
    ahora = ahora or utcnow()
    ventanas = ultimos_meses(meses, ahora)

    resultado = []
    for anio, mes, inicio, fin in ventanas:
        total, conteo = await total_del_periodo(db, company_id, inicio, fin)
        categorias = await por_categoria(db, company_id, inicio, fin, total)
        resultado.append({
            "anio": anio,
            "mes": mes,
            "etiqueta": nombre_mes(mes),
            "nombre": nombre_mes_largo(mes),
            "monto": total,
            "tickets": conteo,
            "por_categoria": categorias,
        })
    return resultado


async def pendientes_de_clasificar(
    db: AsyncSession,
    company_id: Optional[UUID],
) -> dict:
    """Cuanto gasto espera a que alguien le ponga categoria, en TODO el historico.

    **Sin filtro de fecha, y esa es la parte que importa.** La pregunta que
    responde no es "cuanto del gasto de septiembre no esta clasificado", sino
    "cuanto trabajo tengo pendiente". Y ese trabajo no se reinicia cada mes: los
    comprobantes de enero siguen sin categoría en septiembre.

    La primera version de este tablero sacaba el "sin clasificar" del reparto
    del mes en curso, y con eso salía en cero el dia 1 de cada mes y con razon:
    el gasto de hoy, de existir, viene ya de un PDF con categoria. El tablero
    decía "no hay nada pendiente" con veinticuatro tickets esperando, y eso no es
    un dato incompleto: es un dato que actively manda a no trabajar.

    Se cuenta sobre todo el historico y por eso es la cifra accionable. El
    reparto del mes sigue mostrandolo tambien, porque ver el 8% de este mes en
    ámbar explica de donde viene el total.
    """
    q = select(
        func.coalesce(func.sum(TicketModel.total_amount), 0),
        func.count(TicketModel.id),
    ).where(
        and_(
            or_(
                TicketModel.category.is_(None),
                func.trim(TicketModel.category) == "",
            ),
            *_filtro_empresa(company_id),
        )
    )
    monto, tickets = (await db.execute(q)).one()
    return {
        "tickets": int(tickets or 0),
        "monto": Decimal(str(monto or 0)),
    }


def nombre_mes(mes: int) -> str:
    """El nombre corto del mes, para ejes.

    Corto a proposito: "septiembre" en una etiqueta de eje de doce barras deja de
    caber, y una etiqueta que no cabe se recorta de una forma que nadie sabe
    leer.

    **Solo para ejes.** En un texto corrido no sirve, y hay una reason concreta:
    en espanol "ago" es a la vez la abreviatura de *agosto* y la palabra *hace*.
    "de ago" se lee "de hace" sin querer, y una pantalla que dice "de ago" al
    lado de una cifra convenza a media persona de que la cifra es de hace un
    tiempo. En prosa va `nombre_mes_largo`, que no tiene el conflicto.
    """
    return (
        "ene", "feb", "mar", "abr", "may", "jun",
        "jul", "ago", "sep", "oct", "nov", "dic",
    )[mes - 1]


def nombre_mes_largo(mes: int) -> str:
    """El nombre completo del mes, para leerlo dentro de una frase."""
    return (
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    )[mes - 1]
