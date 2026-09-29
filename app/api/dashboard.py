"""El dashboard.

Que es y que no es
-----------------
El dashboard responde tres preguntas, en este orden, y solo a estas:

  1. **Cuanto gasté y como va contra el mes pasado.** Con la diferencia ya
     calculada. El trabajo de comparar dos meses lo hacia la persona, en la
     cabeza, con las dos cifras delante: un dashboard que obliga a restar no ha
     ahorrado nada del trabajo de mirar.
  2. **En que se va la platita.** El reparto por categoria con montos y con la
     parte de cada una. Es la pregunta que se hace al abrir la aplicacion, y la
     que antes no se podia contestar.
  3. **Que tengo que hacer ahora.** La cola de revision, el banco sin conciliar y
     el veredicto de exactitud.

Que NO hace, a proposito
-----------------------
**No repite el mismo bloque cinco veces.** La version anterior pintaba seis
tarjetas identicas --hoy, mes, mes anterior, anio, anio anterior, totales-- con
los mismos doce numeros en cada una, distinguidas solo por el rango de fechas.
Sesenta y pico cifras, de las cuales la mayoria eran el mismo dato con otra
etiqueta. Un tablero con mucho numero no es un tablero con mucha informacion: es
uno en el que no se sabe donde mirar, y por eso se vuelve a leer entero cada
vez que se abre.

**No compara un mes contra "hace 30 dias".** Toda comparacion va contra el mes
completo anterior. Un mes que apenas va por el dia 3, comparado con 30 dias
siftos, siempre da una subida, y un tablero que sube solo porque el mes esta
incompleto es peor que uno que no dice nada.

**No pone el porcentaje de cada barra sin el monto.** El porcentaje responde
"en que proportion", que es la pregunta secundaria. La principal es "cuanto", y
una grafica que solo dice 34.2% obliga a ir a otra pantalla a mirar la cifra. Las
dos van juntas siempre.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import UsuarioActual
from app.models.company import CompanyModel
from app.services import analitica, hallazgos
from app.services.accuracy_service import compute_accuracy_report

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ---------------------------------------------------------------------------
# Formas de la respuesta
# ---------------------------------------------------------------------------


class ResumenPeriodo(BaseModel):
    """El mes que se esta mirando y el mes anterior, con su diferencia.

    `variacion_pct` es `null` cuando el mes anterior no tiene gasto, y eso NO es
    un error: es el primer dia de cualquier negocio. Un porcentaje calculable
    solo cuando hay base de comparacion evita el "+Infinity%" que sale al
    dividir entre cero, y evita que la pantalla tenga que decidir que inventar.
    """

    desde: datetime
    hasta: datetime
    monto: Decimal
    monto_anterior: Decimal
    tickets: int
    tickets_anterior: int
    variacion_pct: Optional[float] = None
    delta_absoluto: Decimal = Decimal("0")
    # El mes anterior por su nombre, para que el encabezado diga "contra agosto"
    # y no "contra el periodo anterior". Un periodo anterior no se puede
    # interpretar; "agosto" si.
    etiqueta_anterior: str = ""
    # El nombre completo, para el texto del encabezado. "ago" en un eje de
    # barras no da problema; "de ago" en una frase se lee "de hace" y es
    # exactamente lo que paso.
    nombre_anterior: str = ""
    # Que tan completo esta el mes que se mira, de 0 a 1.
    #
    # Sin esto, un tablero abierto el dia 2 del mes dice "el gasto cayo 90%" y el
    # numero es aritmeticamente correcto y practicamente una mentira: se compara
    # un mes de dos dias contra uno de treinta. Con el avance, la pantalla puede
    # poner "van 2 de 30 dias" al lado del porcentaje, y quien lo lee entiende
    # que comparar todavia no significa nada. Es la diferencia entre un tablero
    # que informa y uno que impresiona.
    dias_transcurridos: int = 0
    dias_del_mes: int = 0
    # True cuando el mes que se mira no ha terminado. El servidor lo dice y no lo
    # deduce el cliente: si las dos copias de la regla de "que dia es hoy" no
    # coinciden, el tablero se contradice solo.
    mes_en_curso: bool = False
    # La parte del mes anterior que corresponde a los mismos dias transcurridos.
    # Es la unica comparacion que vale mientras el mes no acaba, y por eso se
    # calcula en el servidor, donde estan las dos ventanas.
    monto_anterior_a_la_fecha: Decimal = Decimal("0")
    variacion_pct_a_la_fecha: Optional[float] = None


class CategoriaGasto(BaseModel):
    clave: str
    etiqueta: str
    monto: Decimal
    tickets: int
    porcentaje: float
    variable: bool
    sin_clasificar: bool = False


class MesGasto(BaseModel):
    anio: int
    mes: int
    etiqueta: str
    nombre: str
    monto: Decimal
    tickets: int


class Hallazgo(BaseModel):
    """Una conclusion sobre los datos, con su tono.

    El backend decide QUE paso y no COMO se cuenta: la parte que decide se puede
    probar, y una conclusion equivocada en un tablero no se ve, se nota cuando
    alguien pregunta. La redaccion vive en el front, donde es mas barato
    cambiarla.
    """

    tipo: str
    titulo: str
    detalle: str
    tono: str = "neutro"


class ProveedorGasto(BaseModel):
    proveedor: str
    monto: Decimal
    tickets: int


class MesConCategorias(BaseModel):
    """Un mes con su reparto, para comparar meses y no solo totales.

    Es la pieza que responde "por que subio". La `tendencia` de doce meses dice
    que en abril se gasto mas; esto dice en que se gasto ese mas, que es la
    pregunta que sigue inmediatamente.
    """
    anio: int
    mes: int
    # Corto para la columna, largo para el encabezado. Ver `analitica.nombre_mes`:
    # "ago" en un eje es claro y en una frase se lee "hace".
    etiqueta: str
    nombre: str
    monto: Decimal
    tickets: int
    por_categoria: list[CategoriaGasto] = Field(default_factory=list)


class Banco(BaseModel):
    total: int
    conciliados: int
    sin_conciliar: int
    monto_total: Decimal
    porcentaje: float


class SinClasificar(BaseModel):
    """El trabajo pendiente de clasificacion, en todo el historico.

    Va aparte de `por_categoria` y sin filtro de fecha a proposito. El reparto
    del mes dice "de este mes, cuanto no esta clasificado"; esto dice "cuanto
    tienes encima", que es lo que hace que la cifra sea accionable. Con los dos
    juntos se ve el origen (este mes) y el total (todo lo pendiente)."""

    tickets: int
    monto: Decimal


class DashboardResponse(BaseModel):
    company_id: Optional[UUID] = None
    company_name: Optional[str] = None
    mes: ResumenPeriodo
    por_categoria: list[CategoriaGasto] = Field(default_factory=list)
    sin_clasificar: SinClasificar = Field(
        default_factory=lambda: SinClasificar(tickets=0, monto=Decimal("0"))
    )
    # Los ultimos tres meses, cada uno con su reparto. Es la vista que dice por
    # que cambio el gasto, no solo cuanto.
    comparativo: list[MesConCategorias] = Field(default_factory=list)
    # Lo que se puede concluir de la serie de meses, en orden de importancia.
    # Vacio significa "no hay datos suficientes", y eso tambien es una respuesta.
    hallazgos: list[Hallazgo] = Field(default_factory=list)
    # Doce meses, con los vacios incluidos. Un hueco en la serie tiene que verse
    # como un hueco; quitarlo hace que la linea de tiempo mienta sobre cuando
    # hubo actividad.
    tendencia: list[MesGasto] = Field(default_factory=list)
    top_proveedores: list[ProveedorGasto] = Field(default_factory=list)
    por_estado: dict[str, int] = Field(default_factory=dict)
    por_estado_conciliacion: dict[str, int] = Field(default_factory=dict)
    banco: Banco
    cola_revision: dict[str, int] = Field(default_factory=dict)
    # Si la empresa no tiene NADA (ni tickets, ni movimientos), el frontend lo
    # dice con esas palabras. "Sin datos" con tres movimientos bancarios en la
    # tabla es un tablero que no explica lo que ve.
    hay_datos: bool = True
    exactitud: Optional[dict] = None
    # El gasto de todo el historico, para tener una cifra de referencia y no
    # solo el mes. No es "otra tarjeta de periodos": es el denominador contra el
    # que se lee el mes.
    total_historico: Decimal = Decimal("0")
    tickets_historico: int = 0


async def _resumen_mes(
    db: AsyncSession, company_id: Optional[UUID], ahora: datetime
) -> "ResumenPeriodo":
    """El mes actual con su comparacion, incluyendo si el mes ya termino.

    La comparacion trae DOS numeros a proposito:

      - contra el mes anterior **completo**, que es la cifra que se usa cuando el
        mes ya acabo;
      - contra el mes anterior **hasta el dia de hoy**, que es la unica que
        significa algo mientras el mes va a medias.

    Publicar solo la primera es el error clasico de estos tableros: el dia 3 de
    cada mes todos los negocios parecen quebrando, porque el mes entero se
    compara contra tres dias. Publicar solo la segunda es el error opuesto: en un
    mes ya cerrado se comparan treinta dias contra treinta, pero el dia 29 el
    tablero deja de moverse y nadie sabe por que.
    """
    mes_desde, mes_hasta = analitica.mes_actual(ahora)
    ant_desde, _ = analitica.mes_anterior(ahora)

    cifra = await analitica.cifra_periodo(db, company_id, ahora=ahora)
    monto_a_la_fecha = await analitica.comparacion_a_la_fecha(db, company_id, ahora=ahora)

    dias_totales = analitica.dias_del_mes(ahora.year, ahora.month)
    dias_transcurridos = min(ahora.day, dias_totales)
    en_curso = ahora < mes_hasta

    variacion_a_la_fecha = (
        float((cifra.monto - monto_a_la_fecha) / monto_a_la_fecha * 100)
        if monto_a_la_fecha != 0
        else None
    )

    return ResumenPeriodo(
        desde=mes_desde,
        hasta=mes_hasta,
        monto=cifra.monto,
        monto_anterior=cifra.monto_anterior,
        tickets=cifra.tickets,
        tickets_anterior=cifra.tickets_anterior,
        variacion_pct=cifra.variacion_pct,
        delta_absoluto=cifra.delta_absoluto,
        etiqueta_anterior=analitica.nombre_mes(ant_desde.month),
        nombre_anterior=analitica.nombre_mes_largo(ant_desde.month),
        dias_transcurridos=dias_transcurridos,
        dias_del_mes=dias_totales,
        mes_en_curso=en_curso,
        monto_anterior_a_la_fecha=monto_a_la_fecha,
        variacion_pct_a_la_fecha=variacion_a_la_fecha,
    )


# ---------------------------------------------------------------------------
# El endpoint
# ---------------------------------------------------------------------------


@router.get("/", response_model=DashboardResponse)
async def get_dashboard(
    usuario: UsuarioActual,
    company_id: Optional[UUID] = Query(None, description="Filtrar por empresa"),
    meses: int = Query(12, ge=1, le=36, description="Meses de la tendencia"),
    db: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    """El tablero.

    `usuario` se declara y no se usa: la proteccion la pone el router entero en
    `api_router.py`, no este endpoint. Se deja para que quede a la vista que
    aqui no hay puerta, y no porque sirva de algo.
    """
    company_name = None
    if company_id:
        company = await db.get(CompanyModel, company_id)
        company_name = company.name if company else None

    ahora = analitica.utcnow()
    # El mes ya comparado lo pide el servicio: las dos ventanas se calculan
    # juntas para que un cambio de dia a mitad no compare meses distintos.
    cifra = await analitica.cifra_periodo(db, company_id, ahora=ahora)
    mes_desde, mes_hasta = analitica.mes_actual(ahora)
    ant_desde, _ = analitica.mes_anterior(ahora)

    tendencia = await analitica.tendencia_mensual(db, company_id, meses=meses, ahora=ahora)
    # El total historico usa la ventana mas amplia de la serie, no "todo": si
    # alguien pide 3 meses de tendencia, el historico tambien es de 3 meses. Un
    # "total" que no corresponde al rango dibujado hace que las barras no
    # cuadren con el pie de la pagina.
    total_historico = sum((m["monto"] for m in tendencia), Decimal("0"))
    tickets_historico = sum(m["tickets"] for m in tendencia)

    banco = await analitica.estado_del_banco(db, company_id)
    por_estado = await analitica.por_estado(db, company_id)
    por_estado_conc = await analitica.por_estado_conciliacion(db, company_id)

    # La cola de revision son los estados que todavia piden a una persona. Se
    # deriva de la lista de `enums.OPEN_STATUSES` y no de una copia escrita
    # aqui: si el motor de captura empieza a producir un estado nuevo y esta
    # lista no lo sabe, el dashboard dira que la cola esta vacia mientras hay
    # trabajo esperando, que es el peor fallo posible en este numero.
    from app.core.enums import OPEN_STATUSES

    cola = {e.value: por_estado.get(e.value, 0) for e in OPEN_STATUSES}
    cola = {k: v for k, v in cola.items() if v}

    exactitud = None
    if company_id:
        # El reporte de exactitud no tiene nada que ver con el gasto por
        # categoria y puede fallar por sus propios motivos. Que falle no puede
        # tumbar el dashboard entero, asi que se degrada a `null` y la pantalla
        # lo trata como "no hay veredicto", no como "error".
        try:
            report = await compute_accuracy_report(db, company_id)
            exactitud = {
                "objetivo": report.objetivo,
                "veredicto_global": report.veredicto_global,
                "explicacion": report.explicacion,
                "por_origen": [
                    {
                        "origen": o.origen,
                        "revisados": o.revisados,
                        "aciertos": o.aciertos,
                        "incorrectos": o.incorrectos,
                        "pendientes": o.pendientes,
                        "exactitud": o.exactitud,
                        "intervalo_inferior": o.intervalo_inferior,
                        "intervalo_superior": o.intervalo_superior,
                        "veredicto": o.veredicto,
                        "motivo_faltante": o.motivo_faltante,
                        "total_revisiones_necesarias": o.total_revisiones_necesarias,
                        "campo_mas_fallido": o.campo_mas_fallido,
                    }
                    for o in report.por_origen
                ],
            }
        except Exception:
            exactitud = None

    resumen_mes = await _resumen_mes(db, company_id, ahora)

    return DashboardResponse(
        company_id=company_id,
        company_name=company_name,
        mes=resumen_mes,
        por_categoria=await analitica.por_categoria(db, company_id, mes_desde, mes_hasta, cifra.monto),
        comparativo=await analitica.comparativo_mensual(db, company_id, meses=3, ahora=ahora),
        hallazgos=[
            Hallazgo(**h)
            for h in hallazgos.hallazgos_tendencia(
                tendencia,
                mes_en_curso=resumen_mes.mes_en_curso,
                monto_mes_en_curso=resumen_mes.monto,
                variacion_a_la_fecha=resumen_mes.variacion_pct_a_la_fecha,
                nombre_anterior=resumen_mes.nombre_anterior,
            )
        ],
        tendencia=tendencia,
        top_proveedores=await analitica.top_proveedores(db, company_id, mes_desde, mes_hasta),
        por_estado=por_estado,
        por_estado_conciliacion=por_estado_conc,
        banco=Banco(**banco),
        cola_revision=cola,
        sin_clasificar=SinClasificar(**await analitica.pendientes_de_clasificar(db, company_id)),
        # "Hay datos" significa hay ALGO que mirar: un ticket o un movimiento. Con
        # cero de los dos, la pantalla esta de veras vacia y hay que decirlo, no
        # dejar seis tarjetas en cero que parecen un tablero que no carga.
        hay_datos=(tickets_historico > 0 or banco["total"] > 0),
        exactitud=exactitud,
        total_historico=total_historico,
        tickets_historico=tickets_historico,
    )
