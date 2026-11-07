"""Los lotes, desde el lado del escaneo.

Este modulo es el unico sitio donde el escaneo toca los lotes. Vive en
`services/` y no en `app/api/lotes.py` por la razon de siempre: el servicio
de escaneo no puede importar de un router (ver `ticket_persistence.py`),
y el router de lotes tampoco puede importar del servicio de escaneo sin
cerrar un circulo. Lo que los dos necesitan —el folio secuencial y la
resolucion del lote de una corrida— vive aqui, y los dos lo usan.

SOBRE EL FOLIO, Y POR QUE HAY DOS COPIAS DE LA MISMA FORMULA
------------------------------------------------------------
`_siguiente_folio` sigue existiendo en `app/api/lotes.py`: ese archivo no
se toca en este cambio, asi que el router no puede delegar todavia. La
funcion de aqui, `siguiente_folio`, es LA MISMA formula (mismo patron,
misma expresion regular, mismo `maximo + 1`) y no una variante: dos
formulas distintas darian dos folios distintos para el mismo mes, y la
constraint unica (`ux_lotes_empresa_folio`) decidiria cual vive. El
siguiente paso, cuando `lotes.py` se pueda tocar, es que el router delegue
en esta funcion y su copia desaparezca. Hasta entonces, si cambia la
formula hay que cambiarla en los dos sitios, y este comentario es el que
lo dice.

EL LOTE DE LA CORRIDA, Y EL PERIODO CONTABLE DE UN TICKET
---------------------------------------------------------
Son dos cosas distintas y este modulo las mantiene separadas a proposito:

- El LOTE DE LA CORRIDA es el grupo de lo que se digitalizo junto, y su
  periodo es el de la FECHA DE ESCANEO: un escaneo de noviembre entra en
  el lote de noviembre, aunque el papel sea de octubre. Es lo que hace que
  "agregar los archivos que faltaron" entre en el mismo lote ABIERTO.
- El PERIODO CONTABLE de un ticket es el de SU FECHA DE PAPEL: un ticket
  del 06/10/2026 escaneado el 06/11/2026 pertenece a octubre, y es
  EXTEMPORANEO porque llego tarde (ver `es_extemporaneo` en
  `app/models/lote.py`). El ticket se queda en el lote de la corrida y se
  marca extemporaneo; no se mueve al lote de octubre, porque el lote dice
  cuando se digitalizo, no cuando se gasto. El cierre mensual lee la fecha
  del papel, asi que el gasto sigue contando en octubre.

La marca de extemporaneo NO es una columna nueva en `tickets`: no hay
columna libre adecuada (ver `app/models/ticket.py` — `validation_errors`
es el veredicto del gate y `review_notes` es de una persona) y no se
inventa una migracion para un dato que ya se puede derivar: un ticket es
extemporaneo cuando su `expense_date` real (no provisional) es anterior
a `fecha_inicio` de su lote. Tampoco es una accion nueva en
`scan_events`: `ck_scan_events_action` solo permite VISTO, CREADO,
ACTUALIZADO, SIN_CAMBIOS, OMITIDO, ERROR, REINTENTO y BORRADO, y anadir
una accion es un cambio de DDL (modelo + migracion + `init.sql`) para un
dato que ya cabe en otro sitio. La marca queda en dos sitios sin
migracion:

- En `scan_events`, en el DETALLE del evento CREADO o ACTUALIZADO del
  archivo (acciones permitidas): "extemporaneo: fecha de papel ...". Se
  busca con `action IN ('CREADO', 'ACTUALIZADO') AND detail LIKE
  '%extemporaneo%'`.
- En `auditoria`, con `accion="TICKET_EXTEMPORANEO"` y `modulo="lotes"`
  (texto libre, sin CHECK): es donde viven los eventos de los lotes
  (cerrar y reabrir ya escriben ahi), con folio, fecha de papel y
  periodo contable en JSON.

Y en la respuesta del escaneo (`ScanItemResponse.extemporaneo` y la
lista `extemporaneos` del resumen).

LO QUE ESTE MODULO NO HACE
--------------------------
No asigna tickets ni archivos: eso lo hace `scan_service`, que es quien
sabe cuando nace un ticket y cuando un archivo es duplicado. Aqui solo se
resuelve QUE lote toca y se crea si hace falta. Los contadores cache del
lote (`total_escaneado`, `importe_total`) los mantiene `scan_service` al
asignar; la cuenta en vivo esta en `GET /lotes/{id}`.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import CompanyModel
from app.models.lote import (
    ESTADO_ABIERTO,
    ESTADO_CERRADO,
    ESTADO_EN_REVISION,
    LoteModel,
    config_lote_para,
    periodo_de_fecha,
)

# La accion de `auditoria` para un ticket que llego tarde. `auditoria.accion`
# es texto libre (sin CHECK), a diferencia de `scan_events.action`: por eso
# la marca vive ahi con nombre propio, y en `scan_events` va en el detalle
# del CREADO/ACTUALIZADO (ver el docstring del modulo).
ACCION_AUDITORIA_EXTEMPORANEO = "TICKET_EXTEMPORANEO"


class ErrorLote(ValueError):
    """Un lote que no se puede usar para escanear, con su codigo HTTP.

    El servicio no conoce HTTP, pero el router si: `status_code` es lo que
    `POST /scan` devuelve (404 si el lote no existe, 409 si esta cerrado o
    es de otra empresa, 422 si la empresa exige un dato que no vino). Sin
    el codigo, el router tendria que adivinar por el mensaje, y un mensaje
    es texto, no un contrato.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def siguiente_folio(
    db: AsyncSession,
    company_id: UUID,
    ejercicio: int,
    mes: int,
    prefijo: str,
) -> str:
    """El siguiente folio del mes: LOT-2026-10-001, -002, ...

    LA MISMA formula que `_siguiente_folio` en `app/api/lotes.py` (ver el
    docstring del modulo): se lee el maximo que ya existe y se suma uno.
    Dos creaciones a la vez pueden leer el mismo maximo: para eso esta la
    constraint unica (`ux_lotes_empresa_folio`), y quien crea convierte el
    `IntegrityError` en un 409 que dice "reintenta".
    """
    patron = f"{prefijo}-{ejercicio:04d}-{mes:02d}-%"
    folios = (
        await db.execute(
            select(LoteModel.folio).where(
                LoteModel.company_id == company_id,
                LoteModel.folio.like(patron),
            )
        )
    ).scalars().all()
    maximo = 0
    for folio in folios:
        coincidencia = re.search(r"-(\d+)$", folio)
        if coincidencia:
            maximo = max(maximo, int(coincidencia.group(1)))
    return f"{prefijo}-{ejercicio:04d}-{mes:02d}-{maximo + 1:03d}"


async def _empresa_o_error(db: AsyncSession, company_id: UUID) -> CompanyModel:
    """La empresa, o el mismo 404 que `POST /scan` devuelve hoy."""
    empresa = await db.get(CompanyModel, company_id)
    if empresa is None:
        raise ErrorLote(404, "La empresa indicada no existe")
    return empresa


def periodo_para_escaneo(
    empresa: CompanyModel, fecha_escaneo: date
) -> tuple[int, int, date, date, str]:
    """El periodo de la corrida: (ejercicio, mes, inicio, fin, tipo_periodo).

    El tipo de periodo sale de `lote_config` de la empresa (`mensual` por
    omision). El ejercicio y el mes son los del periodo, no los de la fecha
    a secas: en semanal, una semana que cruza de mes pertenece al mes en
    que empieza (ver `periodo_de_fecha`).
    """
    config = config_lote_para(empresa)
    tipo_periodo = config["tipo_periodo"]
    ejercicio, mes, inicio, fin = periodo_de_fecha(fecha_escaneo, tipo_periodo)
    return ejercicio, mes, inicio, fin, tipo_periodo


async def buscar_lote_abierto(
    db: AsyncSession, company_id: UUID, ejercicio: int, mes: int
) -> LoteModel | None:
    """El lote ABIERTO o EN_REVISION de esa empresa y ese periodo, o `None`.

    Si hay mas de uno —un ABIERTO y un EN_REVISION, o un lote y su
    complementario—, gana el ABIERTO con el folio mas bajo, que es el
    primero que se abrio para ese periodo. Un EN_REVISION tambien acepta
    archivos (es el lote que se esta revisando y al que todavia le faltan
    papeles), y solo se usa si no hay ningun ABIERTO. Un CERRADO nunca
    sale aqui: para lo que llego despues del cierre esta el
    complementario, no reabrir el periodo a escondidas.
    """
    return (
        await db.execute(
            select(LoteModel)
            .where(
                LoteModel.company_id == company_id,
                LoteModel.ejercicio == ejercicio,
                LoteModel.mes == mes,
                LoteModel.estado.in_((ESTADO_ABIERTO, ESTADO_EN_REVISION)),
            )
            .order_by(LoteModel.estado.asc(), LoteModel.folio.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def crear_lote_para_escaneo(
    db: AsyncSession,
    empresa: CompanyModel,
    *,
    ejercicio: int,
    mes: int,
    fecha_inicio: date,
    fecha_fin: date,
    tipo_periodo: str,
    nombre: str,
    total_esperado: int | None,
    creado_por: str | None,
) -> LoteModel:
    """Crea el lote y lo deja guardado (commit), como `POST /lotes`.

    El commit es a proposito y no un descuido: quien llama —el escaneo—
    hace commits por archivo, asi que un lote solo con `flush` se perderia
    si la corrida no procesara ningun archivo. Un lote ABIERTO y vacio es
    un lote que la siguiente corrida reutiliza, no un error.
    """
    config = config_lote_para(empresa)
    folio = await siguiente_folio(
        db, empresa.id, ejercicio, mes, config["prefijo_folio"]
    )
    lote = LoteModel(
        company_id=empresa.id,
        folio=folio,
        nombre=nombre,
        ejercicio=ejercicio,
        mes=mes,
        tipo_periodo=tipo_periodo,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        estado=ESTADO_ABIERTO,
        total_esperado=total_esperado,
        total_escaneado=0,
        importe_total=Decimal("0.00"),
        creado_por=creado_por,
    )
    db.add(lote)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ErrorLote(
            409, "Ya existe un lote con ese folio. Reintenta la creacion."
        ) from exc
    await db.commit()
    await db.refresh(lote)
    return lote


async def validar_lote_explicito(
    db: AsyncSession, lote_id: UUID, company_id: UUID
) -> LoteModel:
    """El lote que pidio el cliente, comprobado, o un `ErrorLote`.

    Tres comprobaciones y en este orden: que exista (404), que sea de
    esta empresa (409 — es un conflicto, no un "no existe": el lote existe
    y es de otra empresa), y que acepte archivos (409 si esta CERRADO).
    Un lote CERRADO no se reabre por aqui: se reabre con
    `POST /lotes/{id}/reabrir` (con motivo y en auditoria) o se le crea
    un complementario.
    """
    lote = await db.get(LoteModel, lote_id)
    if lote is None:
        raise ErrorLote(404, "El lote indicado no existe")
    if lote.company_id != company_id:
        raise ErrorLote(
            409,
            f"El lote {lote.folio} pertenece a otra empresa",
        )
    if lote.estado == ESTADO_CERRADO:
        raise ErrorLote(
            409,
            f"El lote {lote.folio} esta CERRADO y no acepta mas archivos. "
            "Reabrelo con POST /lotes/{id}/reabrir o crea un "
            "complementario con POST /lotes/{id}/complementario.",
        )
    if lote.estado not in (ESTADO_ABIERTO, ESTADO_EN_REVISION):
        raise ErrorLote(
            409,
            f"El lote {lote.folio} esta en estado {lote.estado} "
            "y no acepta archivos",
        )
    return lote


async def resolver_lote_corrida(
    db: AsyncSession,
    company_id: UUID | None,
    *,
    fecha_escaneo: date,
    actor: str | None,
    lote_id: UUID | None = None,
    crear_lote: dict | None = None,
) -> LoteModel | None:
    """El lote de una corrida de escaneo, o `None` si no toca lote.

    `None` solo pasa en el modo inventario (`company_id` es `None`): sin
    empresa no hay ticket, y sin ticket no hay lote. En todo lo demas
    siempre hay lote: explicito, pedido, reutilizado o creado.

    El orden es el del contrato, y no es intercambiable:

    1. `lote_id`: el que pidio el cliente, validado (404/409).
    2. `crear_lote`: se crea con lo pedido (nombre, periodo y
       `total_esperado` opcionales; lo que falte sale de la fecha de
       escaneo y de la config). Si la empresa exige `total_esperado` y no
       viene, es 422, igual que en `POST /lotes`: un lote sin expectativa
       no se puede declarar completo despues.
    3. Si no viene nada: se reutiliza el lote ABIERTO o EN_REVISION de
       esa empresa y ese periodo, para que agregar los archivos que
       faltaron entre en el mismo lote. Si no hay ninguno, se crea uno
       con el nombre `Lote {ejercicio}-{mes:02d}`. Si la empresa exige
       `total_esperado`, aqui tampoco se puede inventar: es 422 y el
       mensaje dice como pasarlo.

    `crear_lote` es un `dict` y no el schema de la API a proposito:
    `services/` no importa de `schemas/` (ver `scan_service.py`). Las
    claves son `nombre`, `ejercicio`, `mes` y `total_esperado`; `ejercicio`
    y `mes` van juntos o no van, igual que en `LoteCreate`.
    """
    if company_id is None:
        return None

    empresa = await _empresa_o_error(db, company_id)

    if lote_id is not None:
        return await validar_lote_explicito(db, lote_id, company_id)

    ejercicio, mes, inicio, fin, tipo_periodo = periodo_para_escaneo(
        empresa, fecha_escaneo
    )
    config = config_lote_para(empresa)

    if crear_lote is not None:
        ejercicio_pedido = crear_lote.get("ejercicio")
        mes_pedido = crear_lote.get("mes")
        if (ejercicio_pedido is None) != (mes_pedido is None):
            raise ErrorLote(
                422, "ejercicio y mes van juntos o no van en crear_lote"
            )
        if ejercicio_pedido is not None and mes_pedido is not None:
            ejercicio, mes = ejercicio_pedido, mes_pedido
            _, _, inicio, fin = periodo_de_fecha(
                date(ejercicio, mes, 1), tipo_periodo
            )
        total_esperado = crear_lote.get("total_esperado")
        if config["exige_total_esperado"] and total_esperado is None:
            raise ErrorLote(
                422,
                "Esta empresa exige total_esperado para crear un lote",
            )
        nombre = crear_lote.get("nombre") or f"Lote {ejercicio}-{mes:02d}"
        return await crear_lote_para_escaneo(
            db,
            empresa,
            ejercicio=ejercicio,
            mes=mes,
            fecha_inicio=inicio,
            fecha_fin=fin,
            tipo_periodo=tipo_periodo,
            nombre=nombre,
            total_esperado=total_esperado,
            creado_por=actor,
        )

    existente = await buscar_lote_abierto(db, company_id, ejercicio, mes)
    if existente is not None:
        return existente

    if config["exige_total_esperado"]:
        raise ErrorLote(
            422,
            "Esta empresa exige total_esperado para crear un lote: "
            "pasalo en crear_lote.total_esperado o crea el lote antes "
            "con POST /lotes.",
        )
    return await crear_lote_para_escaneo(
        db,
        empresa,
        ejercicio=ejercicio,
        mes=mes,
        fecha_inicio=inicio,
        fecha_fin=fin,
        tipo_periodo=tipo_periodo,
        nombre=f"Lote {ejercicio}-{mes:02d}",
        total_esperado=None,
        creado_por=actor,
    )
