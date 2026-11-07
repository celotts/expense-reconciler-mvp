#!/usr/bin/env python3
"""Asigna el lote inicial a los 7 HEIC ya escaneados (octubre 2026).

Los 7 archivos se escanearon ANTES de que existieran los lotes, asi que sus
`scan_files` y sus tickets tienen `lote_id` en NULL. Este script los agrupa
en el lote LOT-2026-10-001, ejercicio 2026, mes 10, ABIERTO, con el nombre
"Lote inicial octubre 2026 — 7 HEIC".

Uso (dentro del contenedor de la API, que es donde vive la base):
    docker compose exec expense-api python scripts/asignar_lote_inicial.py
    docker compose exec expense-api python scripts/asignar_lote_inicial.py --simular

IDEMPOTENTE Y SIN BORRAR NADA
-----------------------------
- Si el lote ya existe, se reutiliza; si ya tienen `lote_id`, no se toca
  nada. Correrlo dos veces no crea un segundo lote ni duplica conteos: el
  cache del lote (`total_escaneado`, `importe_total`) se RECALCULA desde
  las filas, no se suma, asi que la segunda pasada deja los mismos numeros.
- Los 2 duplicados (Soriana y Bodega Aurrera) NO crean ticket nuevo: su
  `scan_file` apunta al ticket original, y el ticket original es el que
  lleva el `lote_id`. El archivo duplicado tambien lleva el `lote_id` del
  lote, porque ahi se escaneo.
- No borra filas de ninguna tabla. `--simular` no escribe nada.

DECISION DOCUMENTADA: DEL SOL QUEDA EN ESTE LOTE, EXTEMPORANEO
---------------------------------------------------------------
Del Sol (IMG_4409, Grupo Comercial DSW, $279.93) trae fecha de papel
10/01/2026, anterior al periodo del lote (octubre 2026). Se queda en
LOT-2026-10-001 —el lote dice CUANDO SE DIGITALIZO, y los 7 se
digitalizaron juntos— y se marca EXTEMPORANEO: su periodo contable sigue
siendo enero de 2026, y el cierre de octubre no lo suma. La marca queda
en `auditoria` con `accion="TICKET_EXTEMPORANEO"` y `modulo="lotes"`
(la CHECK de `scan_events` no permite una accion nueva, y reescribir el
evento CREADO que ya existe seria tocar el historial), y se puede volver
a derivar en cualquier momento: `expense_date` real anterior a
`fecha_inicio` del lote.

Sin fecha en el papel (Bodega Aurrera, $234.00, `fecha_provisional`) NO
hay extemporaneo: el ticket pertenece al periodo del lote de la corrida,
que es el periodo de escaneo.

La fecha de escaneo de referencia es 2026-11-06, el dia en que se
escanearon los 7. Es la que decide que es extemporaneo aqui.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.time import utcnow
from app.models.auditoria import Auditoria
from app.models.lote import (
    ESTADO_ABIERTO,
    ESTADO_CERRADO,
    LoteModel,
    es_extemporaneo,
    periodo_de_fecha,
)
from app.models.scan_file import ScanFileModel
from app.models.ticket import TicketModel

# Los 7 archivos, por STEM (sin extension): las fotos son HEIC en el
# telefono y .jpeg al exportarlas, y el stem es lo que no cambia.
NOMBRES_7 = {
    "IMG_4388",  # Soriana $65.00, 06/10/2026
    "IMG_4397",  # Soriana $65.00, duplicado de IMG_4388
    "IMG_4399",  # Tony Tiendas $23.20, 06/10/2026
    "IMG_4406",  # Cremeria Hermanos Coronel $80.50, 08/10/2026
    "IMG_4407_prueba",  # Bodega Aurrera $234.00, sin fecha visible
    "IMG_4407",  # Bodega Aurrera $234.00, duplicado de IMG_4407_prueba
    "IMG_4409",  # Del Sol / Grupo Comercial DSW $279.93, 10/01/2026
}

FOLIO_INICIAL = "LOT-2026-10-001"
NOMBRE_LOTE = "Lote inicial octubre 2026 — 7 HEIC"
EJERCICIO_INICIAL = 2026
MES_INICIAL = 10
FECHA_INICIO = date(2026, 10, 1)
FECHA_FIN = date(2026, 10, 31)
# El dia en que se escanearon los 7. Es la referencia de extemporaneidad:
# un papel de antes de octubre, escaneado en noviembre, llego tarde.
FECHA_ESCANEO = date(2026, 11, 6)
ACTOR = "script asignar_lote_inicial"


async def _asignar(simular: bool) -> int:
    async with AsyncSessionLocal() as session:
        filas = (await session.execute(select(ScanFileModel))).scalars().all()
        objetivo = [
            fila
            for fila in filas
            if Path(fila.relative_path).stem in NOMBRES_7
        ]
        if not objetivo:
            print(
                "No se encontro ninguno de los 7 archivos en scan_files. "
                "Revisa que el escaneo ya corrio y que los nombres son "
                "IMG_4388, IMG_4397, IMG_4399, IMG_4406, IMG_4407_prueba, "
                "IMG_4407 e IMG_4409."
            )
            return 1
        faltantes = NOMBRES_7 - {Path(f.relative_path).stem for f in objetivo}
        if faltantes:
            print(
                "Aviso: faltan en scan_files: "
                + ", ".join(sorted(faltantes))
                + ". Se asignan los que si estan."
            )

        # La empresa es la de los archivos y sus tickets. Si hay mas de
        # una, no se adivina: se para y se dice cuales son.
        empresas = {
            fila.company_id for fila in objetivo if fila.company_id is not None
        }
        tickets_por_id: dict = {}
        for fila in objetivo:
            if fila.ticket_id is not None and fila.ticket_id not in tickets_por_id:
                ticket = await session.get(TicketModel, fila.ticket_id)
                if ticket is not None:
                    tickets_por_id[ticket.id] = ticket
                    empresas.add(ticket.company_id)
        if len(empresas) != 1:
            print(
                "Los 7 archivos pertenecen a mas de una empresa "
                f"({len(empresas)} distintas) o a ninguna. No se asigna "
                "nada: revisa a mano a que empresa va cada uno."
            )
            return 1
        empresa_id = next(iter(empresas))

        lote = (
            await session.execute(
                select(LoteModel).where(
                    LoteModel.company_id == empresa_id,
                    LoteModel.folio == FOLIO_INICIAL,
                )
            )
        ).scalar_one_or_none()
        if lote is not None:
            if lote.estado == ESTADO_CERRADO:
                print(
                    f"El lote {FOLIO_INICIAL} ya existe y esta CERRADO. "
                    "No se asigna nada: reabrelo o crea un complementario."
                )
                return 1
            print(f"El lote {FOLIO_INICIAL} ya existe ({lote.estado}); se reutiliza.")
        else:
            lote = LoteModel(
                company_id=empresa_id,
                folio=FOLIO_INICIAL,
                nombre=NOMBRE_LOTE,
                ejercicio=EJERCICIO_INICIAL,
                mes=MES_INICIAL,
                tipo_periodo="mensual",
                fecha_inicio=FECHA_INICIO,
                fecha_fin=FECHA_FIN,
                estado=ESTADO_ABIERTO,
                total_esperado=None,
                total_escaneado=0,
                importe_total=Decimal("0.00"),
                creado_por=ACTOR,
            )
            session.add(lote)
            await session.flush()
            print(f"Lote creado: {FOLIO_INICIAL} ({NOMBRE_LOTE}).")

        archivos_asignados = 0
        archivos_ya = 0
        tickets_asignados = 0
        tickets_ya = 0
        extemporaneos: list[str] = []
        for fila in sorted(objetivo, key=lambda f: f.relative_path):
            if fila.lote_id is None:
                fila.lote_id = lote.id
                archivos_asignados += 1
            elif fila.lote_id != lote.id:
                # El archivo ya estaba en OTRO lote: se deja donde estaba y
                # se avisa, en vez de moverlo en silencio.
                print(
                    f"  Aviso: {fila.relative_path} ya estaba en otro lote; "
                    "no se mueve."
                )
                archivos_ya += 1
                continue
            else:
                archivos_ya += 1

            ticket = tickets_por_id.get(fila.ticket_id) if fila.ticket_id else None
            if ticket is None:
                continue
            if ticket.lote_id is None:
                ticket.lote_id = lote.id
                tickets_asignados += 1
            elif ticket.lote_id != lote.id:
                # El ticket ya esta en otro lote (por ejemplo, el original
                # de un duplicado que se asigno antes): el archivo sigue al
                # ticket y no al reves, asi que se deja constancia y no se
                # mueve el ticket.
                print(
                    f"  Aviso: el ticket de {fila.relative_path} ya estaba "
                    "en otro lote; no se mueve."
                )
                fila.lote_id = ticket.lote_id
                tickets_ya += 1
                continue
            else:
                tickets_ya += 1

            # La marca de extemporaneo, una vez por TICKET (no por archivo:
            # los duplicados comparten ticket). Sin fecha real en el papel
            # no hay marca: pertenece al periodo del lote. Va en
            # `auditoria` (modulo "lotes"): la CHECK de `scan_events` no
            # permite una accion nueva y el evento CREADO del archivo ya
            # existe desde el escaneo original — reescribirlo seria tocar
            # el historial.
            if (
                ticket.expense_date is not None
                and not ticket.fecha_provisional
                and es_extemporaneo(
                    ticket.expense_date, FECHA_ESCANEO, lote.tipo_periodo
                )
            ):
                ya_marcado = (
                    await session.execute(
                        select(func.count())
                        .select_from(Auditoria)
                        .where(
                            Auditoria.modulo == "lotes",
                            Auditoria.accion == "TICKET_EXTEMPORANEO",
                            Auditoria.registro_afectado == ticket.id,
                        )
                    )
                ).scalar_one()
                if ya_marcado == 0:
                    ejercicio_papel, mes_papel, _, _ = periodo_de_fecha(
                        ticket.expense_date, lote.tipo_periodo
                    )
                    session.add(
                        Auditoria(
                            accion="TICKET_EXTEMPORANEO",
                            modulo="lotes",
                            registro_afectado=ticket.id,
                            detalles=json.dumps(
                                {
                                    "archivo": fila.relative_path,
                                    "ticket_id": str(ticket.id),
                                    "fecha_papel": ticket.expense_date.isoformat(),
                                    "periodo_contable": (
                                        f"{ejercicio_papel}-{mes_papel:02d}"
                                    ),
                                    "lote_id": str(lote.id),
                                    "lote_folio": lote.folio,
                                }
                            ),
                            fecha=utcnow(),
                        )
                    )
                extemporaneos.append(
                    f"{fila.relative_path}: {ticket.provider_name} "
                    f"${ticket.total_amount} del {ticket.expense_date.isoformat()}"
                )

        # El cache se RECALCULA desde las filas, no se suma: es lo que hace
        # idempotente al script y lo que `GET /lotes/{id}` ya hace en vivo.
        # Un duplicado no suma: apunta al ticket original, que ya conto una
        # vez.
        filas_lote = (
            await session.execute(
                select(TicketModel).where(TicketModel.lote_id == lote.id)
            )
        ).scalars().all()
        lote.total_escaneado = len(filas_lote)
        lote.importe_total = sum(
            (t.total_amount for t in filas_lote if t.total_amount is not None),
            Decimal("0.00"),
        )

        if simular:
            await session.rollback()
            print("--simular: no se escribio nada.")
        else:
            await session.commit()

        print(f"Empresa: {empresa_id}")
        print(f"Lote: {lote.folio} ({lote.estado})")
        print(f"Archivos asignados: {archivos_asignados} (ya tenian: {archivos_ya})")
        print(f"Tickets asignados: {tickets_asignados} (ya tenian: {tickets_ya})")
        print(f"Cache del lote: {lote.total_escaneado} tickets, ${lote.importe_total}")
        if extemporaneos:
            print("Extemporaneos en este lote:")
            for linea in sorted(set(extemporaneos)):
                print(f"  - {linea}")
        else:
            print("Extemporaneos: ninguno.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Asigna LOT-2026-10-001 a los 7 HEIC ya escaneados."
    )
    parser.add_argument(
        "--simular",
        action="store_true",
        help="Dice que haria sin escribir nada.",
    )
    args = parser.parse_args()
    return asyncio.run(_asignar(simular=args.simular))


if __name__ == "__main__":
    sys.exit(main())
