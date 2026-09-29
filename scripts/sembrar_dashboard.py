#!/usr/bin/env python3
"""Datos de ejemplo para ver el dashboard con contenido.

Por que este script y no el `seed_data.py` que ya existe: aquel siembra 13
tickets de los ultimos 15 dias, que es perfecto para probar que el motor de
conciliacion encuentraparejas y es insuficiente para ver un tablero. Con esos
datos, todo cae en "mes actual", la tendencia de doce meses es una barra y nueve
ceros, y no hay forma de distinguir un diseno bueno de uno malo.

Lo que siembra aqui, y por que:

  - **Doce meses**, no quince dias. Un tablero de gastos se juzga por la serie de
    tiempo, y una serie de un punto no dice nada.
  - **Varias empresas con perfiles distintos.** Un gasto se comporta de otra
    manera en una Constructora (pocos tickets, muy caros, concentrados en
    materiales) que en una Taqueria (muchos tickets, baratos, concentrados en
    alimentacion). Con una sola empresa no se ve si el tablero funciona, porque
    todos los datos se parecen.
  - **Categorias de la taxonomia de `app.core.categorias`**, para que el reparto
    por monto tenga sentido. Y algunos tickets SIN categoria a proposito: el
    "Sin clasificar" tambien es un dato, y un tablero que solo se ve con el
    100% clasificado no se ha probado en el estado en el que de verdad se usa.
  - **Los cinco estados de extraccion**, para que la cola de revision y la barra
    de estado tengan contenido en vez de ceros.
  - **Conciliaciones PERFECT / MANUAL / DISCREPANCY**, para que la salud de la
    conciliacion se vea en distintos tonos y no solo en verde.
  - **Movimientos bancarios conciliados y sin conciliar**, porque el total del
    banco y el porcentaje que se calcula por diferencia tienen que cuadrar.

Como se borra
-------------
`--limpiar` borra TODO lo que este script creo, y solo eso: las tres empresas
que nombra aqui, por nombre. Si alguien creo una empresa que se llama igual, se
borra tambien, asi que conviene correrlo antes de sembrar.

    docker compose exec -T expense-api python scripts/sembrar_dashboard.py
    docker compose exec -T expense-api python scripts/sembrar_dashboard.py --limpiar

No se siembra en una base de datos de verdad a proposito: se comprueba al
arrancar que este es el entorno de desarrollo. Un `--forzar` lo saltaria, y
preferimos que no exista.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal, engine
from app.core.enums import (
    AUTO_APPROVE_CONFIDENCE, ExtractionStatus, MatchStatus, REVIEW_CONFIDENCE,
    SPOT_CHECK_RATE, SpotCheckStatus,
)
from app.core.time import utcnow
from app.models.bank_transaction import BankTransactionModel
from app.models.company import CompanyModel
from app.models.reconciliation import ReconciliationModel
from app.models.ticket import TicketModel

# Las tres empresas de la demo. El nombre se usa para localizarlas al limpiar.
EMPRESAS = [
    {
        "nombre": "Taqueria La Esquina",
        "tax_id": "TLE240101AAA",
        # Ticket barato y frecuente, gasto pequeno y muy repartido.
        "perfil": "comercio",
    },
    {
        "nombre": "Nubestore SA de CV",
        "tax_id": "NUB240202BBB",
        # Pocos tickets, muy caros, casi todo servicios fijos.
        "perfil": "servicios",
    },
    {
        "nombre": "Constructora El Sol",
        "tax_id": "CES240303CCC",
        # Ticket carisimo, muy concentrado en materiales.
        "perfil": "obra",
    },
]

# Los proveedores de cada perfil, con su categoria y un rango de monto tipico.
# El rango es deliberadamente amplio dentro de cada familia: un tablero donde
# todos los tickets de una categoria cuestan lo mismo no tiene variacion y no
# dice nada de como se comporta el gasto.
CATALOGOS: dict[str, list[tuple[str, str, float, float, int]]] = {
    # (proveedor, rfc, minimo, maximo, peso relativo)
    "comercio": [
        ("BODEGA AURRERA", "BAU010101000", 180.00, 950.00, 8),
        ("OXXO", "OXX010101000", 45.00, 260.00, 10),
        ("COSTCO", "COS010101000", 600.00, 2400.00, 3),
        ("WALMART", "WAL010101000", 150.00, 800.00, 6),
        ("SAM'S CLUB", "SAM010101000", 700.00, 1800.00, 2),
        ("RESTAURANT LOS NARANJOS", "RLN010101000", 380.00, 1450.00, 4),
        ("CARNICERIA EL CHORIZO", "CEC010101000", 250.00, 900.00, 5),
        ("LA COMIDITA SA", "LCS010101000", 200.00, 700.00, 4),
        # Dos proveedores sin categoria, a proposito: son los que hacen que
        # exista la barra de "Sin clasificar".
        ("FERRETERIA LA LLAVE", "FLL010101000", 150.00, 600.00, 2),
        ("PAPELERIA CENTRO", "PAC010101000", 90.00, 420.00, 2),
    ],
    "servicios": [
        ("AMAZON WEB SERVICES", "AWS010101000", 2400.00, 9800.00, 6),
        ("GOOGLE CLOUD", "GCL010101000", 1800.00, 7200.00, 5),
        ("MICROSOFT AZURE", "AZU010101000", 2200.00, 8400.00, 4),
        ("DIGITALOCEAN", "DGO010101000", 600.00, 2600.00, 4),
        ("CLOUDFLARE", "CLO010101000", 400.00, 1400.00, 5),
        ("SLACK", "SLK010101000", 300.00, 900.00, 3),
        ("FIGMA", "FIG010101000", 250.00, 750.00, 2),
        ("GITHUB", "GTH010101000", 200.00, 600.00, 2),
        # Un gasto de renta: categoria fija, un solo pago al mes, y es el mas
        # grande. Sirve para ver que la barra de "Renta" no sigue la variacion
        # del resto, que es justo lo que distingue un gasto fijo de uno variable.
        ("INMOBILIARIA SANTA FE", "ISF010101000", 18000.00, 18000.00, 1),
    ],
    "obra": [
        ("CEMEX", "CMX010101000", 12000.00, 48000.00, 6),
        ("HOME DEPOT", "HDE010101000", 2200.00, 11000.00, 7),
        ("FERRETERIA EL TORNILLO", "FET010101000", 800.00, 4200.00, 6),
        ("PAINT DEPOT", "PNT010101000", 1500.00, 6800.00, 4),
        ("CONCRETOS DEL VALLE", "CDV010101000", 9000.00, 35000.00, 5),
        ("ACERO INDUSTRIAL", "ACI010101000", 14000.00, 52000.00, 4),
        ("PINTURAS BEREL", "PBE010101000", 1800.00, 7200.00, 3),
        ("CASETAS Y PEAJES SA", "CPS010101000", 400.00, 1600.00, 8),
        ("CASETAS Y PEAJES SA", "CPS010101000", 400.00, 1600.00, 6),
    ],
}

# Que proveedor va con que categoria. Vive aqui y no dentro del catalogo para
# que la misma familia de proveedor pueda aparecer con dos categorias distintas
# (un proveedor que vende de todo), que es lo que pasa en la vida real.
CATEGORIA_POR_PROVEEDOR: dict[str, str] = {
    "BODEGA AURRERA": "ALIMENTACION",
    "COSTCO": "ALIMENTACION",
    "SAM'S CLUB": "ALIMENTACION",
    "WALMART": "ALIMENTACION",
    "OXXO": "ALIMENTACION",
    "RESTAURANT LOS NARANJOS": "ALIMENTACION",
    "CARNICERIA EL CHORIZO": "ALIMENTACION",
    "LA COMIDITA SA": "ALIMENTACION",
    "AMAZON WEB SERVICES": "SERVICIOS",
    "GOOGLE CLOUD": "SERVICIOS",
    "MICROSOFT AZURE": "SERVICIOS",
    "DIGITALOCEAN": "SERVICIOS",
    "CLOUDFLARE": "SERVICIOS",
    "SLACK": "SOFTWARE",
    "FIGMA": "SOFTWARE",
    "GITHUB": "SOFTWARE",
    "INMOBILIARIA SANTA FE": "RENTA",
    "CEMEX": "MATERIALES",
    "CONCRETOS DEL VALLE": "MATERIALES",
    "ACERO INDUSTRIAL": "MATERIALES",
    "HOME DEPOT": "MATERIALES",
    "PAINT DEPOT": "MATERIALES",
    "PINTURAS BEREL": "MATERIALES",
    "FERRETERIA EL TORNILLO": "HERRAMIENTAS",
    "CASETAS Y PEAJES SA": "TRANSPORTE",
    # FERRETERIA LA LLAVE y PAPELERIA CENTRO se dejan sin categoria a proposito.
}

# El reparto de los estados ya NO se define aqui.
#
# Antes habia una tabla de pesos y el estado se sorteaba suelto. Eso era
# incoherente con el gate: salian tickets AUTO_APROBADO con confianza 0.63, que
# el sistema real jamas produciria, y hacia que el reporte de exactitud y la cola
# de revision no significaran nada. Ahora el estado sale de la confianza con las
# reglas de `enums.py`, en `_lectura`.
#
# Kept as a note to whoever reads this next: si quieres cambiar el reparto de la
# cola, cambia la distribucion de confianza en `_lectura`, no una tabla aqui.

MESES_A_SEMBRAR = 12


def _rng(semilla: int) -> random.Random:
    return random.Random(semilla)


async def limpiar() -> int:
    """Borra lo de la demo. Solo las empresas de aqui, por nombre."""
    async with AsyncSessionLocal() as db:
        nombres = [e["nombre"] for e in EMPRESAS]
        empresas = (
            await db.execute(select(CompanyModel).where(CompanyModel.name.in_(nombres)))
        ).scalars().all()

        if not empresas:
            print("No hay datos de la demo que borrar.")
            return 0

        ids = [e.id for e in empresas]
        # El orden importa por las claves foraneas: primero los hijos.
        await db.execute(
            delete(ReconciliationModel).where(ReconciliationModel.ticket_id.in_(
                select(TicketModel.id).where(TicketModel.company_id.in_(ids))
            ))
        )
        await db.execute(delete(TicketModel).where(TicketModel.company_id.in_(ids)))
        await db.execute(delete(BankTransactionModel).where(BankTransactionModel.company_id.in_(ids)))
        await db.execute(delete(CompanyModel).where(CompanyModel.id.in_(ids)))
        await db.commit()

        print(f"Borradas {len(empresas)} empresas de la demo y todo lo que dependia de ellas.")
        return 0


async def sembrar() -> int:
    async with AsyncSessionLocal() as db:
        ya_existen = (
            await db.execute(
                select(CompanyModel).where(CompanyModel.name.in_([e["nombre"] for e in EMPRESAS]))
            )
        ).scalars().first()
        if ya_existen is not None:
            print(f"Ya existe '{ya_existen.name}'. Corre con --limpiar primero.")
            return 1

        hoy = date.today()
        rng = _rng(20260928)  # La semilla es fija: dos siembras dan los mismos numeros.

        companies: list[CompanyModel] = []
        for emp in EMPRESAS:
            c = CompanyModel(id=uuid.uuid4(), name=emp["nombre"], tax_id=emp["tax_id"])
            companies.append(c)
            db.add(c)
        await db.commit()
        print(f"{len(companies)} empresas")

        total_tickets = 0
        total_banco = 0
        total_conc = 0

        for emp, company in zip(EMPRESAS, companies):
            catalogo = CATALOGOS[emp["perfil"]]
            pesos = [p[4] for p in catalogo]

            # Cuantos tickets por mes. Cada perfil tiene su ritmo: el comercio
            # genera muchos al mes, la constructora pocos y caros. Es la razon de
            # que la serie no sea plana y de que se vea algo en la tendencia.
            por_mes = {"comercio": (14, 26), "servicios": (5, 11), "obra": (7, 14)}[emp["perfil"]]

            # `range(MESES_A_SEMBRAR, -1, -1)`, con el -1 y no con un 0: el
            # bucle tiene que LLEGAR a cero, porque cero es "el mes en curso" y
            # es el unico que el tablero muestra en grande. Con `range(12, 0, -1)`
            # --que es lo que habia-- el bucle llegaba hasta 1 y el mes en curso
            # no se sembraba nunca, y el tablero abria con el reparto por
            # categoria vacio justo el dia que mas falta hacia falta: el primero
            # de cada mes.
            for meses_atras in range(MESES_A_SEMBRAR, -1, -1):
                # El mes se construye desde el dia 1 y se acota al hoy, para que
                # el mes en curso este a medias y no parezca completo. Un mes en
                # curso inflado es la forma mas facil de mentir con un tablero.
                primer_dia = _primer_dia_de_mes(hoy, meses_atras)
                ultimo_dia = min(
                    _primer_dia_siguiente(primer_dia) - timedelta(days=1),
                    hoy,
                )
                dias_disponibles = (ultimo_dia - primer_dia).days + 1
                if dias_disponibles <= 0:
                    continue

                n = rng.randint(*por_mes)
                for _ in range(n):
                    proveedor, rfc, minimo, maximo, _p = rng.choices(catalogo, weights=pesos, k=1)[0]
                    dia = primer_dia + timedelta(days=rng.randrange(dias_disponibles))

                    monto = _monto(rng, minimo, maximo)
                    subtotal = (monto / Decimal("1.16")).quantize(Decimal("0.01"))
                    iva = (monto - subtotal).quantize(Decimal("0.01"))

                    # **El estado se deduce de la confianza, no al reves.**
                    #
                    # La version anterior sorteaba los dos por separado, y eso
                    # dejaba 248 tickets AUTO_APROBADO con confianza 0.63: datos
                    # que contradicen el gate de `app/core/enums.py` (0.90 para
                    # autoaprobar). No es solo "irreal": hace que el reporte de
                    # exactitud y la cola de revision no significen nada, porque
                    # las dos se leen precisamente por esa coherencia. Un tablero
                    # de demostracion con datos incoherentes hace pensar que el
                    # sistema calcula mal.
                    estado, confianza, origen_confianza, lectura_ia = _lectura(rng)
                    (
                        spot_estado, spot_fecha, spot_notas, spot_campo, spot_por,
                    ) = _muestreo(rng, estado, confianza)

                    t = TicketModel(
                        id=uuid.uuid4(),
                        company_id=company.id,
                        provider_name=proveedor,
                        provider_tax_id=rfc,
                        total_amount=monto,
                        tax_amount=iva,
                        subtotal=subtotal,
                        expense_date=dia,
                        category=CATEGORIA_POR_PROVEEDOR.get(proveedor),
                        raw_text=f"{proveedor} Rfc {rfc} Total {monto}",
                        extraction_status=estado.value,
                        # La confianza va SOLO si hubo lectura automatica. En un
                        # ticket tecleado a mano no hay nada que medir, y un
                        # 0.000 ahi baja el promedio de la IA sin que nadie lo
                        # haya hecho mal.
                        confidence=confianza,
                        confidence_source=origen_confianza,
                        source_type="pdf" if lectura_ia else "manual",
                        source_file=f"{proveedor.lower().replace(' ', '_')}-{rng.randint(1000, 9999)}.pdf"
                        if lectura_ia else None,
                        # El muestreo es lo que permite AFIRMAR una exactitud. Sin
                        # estos campos, el reporte sale SIN_EVIDENCIA por diseño y
                        # el tablero no puede enseñar la parte que mide al sistema.
                        spot_check_status=spot_estado,
                        spot_checked_at=spot_fecha,
                        spot_check_notes=spot_notas,
                        spot_check_wrong_fields=spot_campo,
                        spot_checked_by=spot_por,
                    )
                    db.add(t)
                    total_tickets += 1

                    # El movimiento bancario acompana a una parte de los tickets,
                    # con tres desenlaces: quadrado, con diferencia de fecha, o
                    # con diferencia de monto. Los tres hacen falta para que los
                    # tres tonos de la conciliacion aparezcan.
                    dado = rng.random()
                    if dado < 0.62:
                        await _bancos(db, rng, company.id, proveedor, monto, dia, cuadra=True, conciliado=True)
                        total_banco += 1
                    elif dado < 0.78:
                        await _bancos(db, rng, company.id, proveedor, monto, dia + timedelta(days=2), cuadra=True, conciliado=True)
                        total_banco += 1
                    elif dado < 0.88:
                        # Discrepancia: el banco movio otra cantidad.
                        await _bancos(db, rng, company.id, proveedor, _redondea(monto * Decimal("1.08")), dia, cuadra=False, conciliado=True)
                        total_banco += 1
                    # El resto no tiene movimiento: el ticket esta sin conciliar.

                    # La conciliacion se crea aparte del movimiento, en una
                    # segunda pasada, porque necesita el id del movimiento.
            await db.commit()

        # Segunda pasada: conciliaciones, en bloque y no por mes.
        total_conc = await _conciliaciones(db, rng)
        await db.commit()

        print(f"{total_tickets} tickets, {total_banco} movimientos bancarios, {total_conc} conciliaciones")
        print("\nPara verlo: entra con tu cuenta y abre Inicio.")
        print("Las empresas de la demo son: " + ", ".join(e["nombre"] for e in EMPRESAS))
        return 0


async def _bancos(db, rng, company_id, proveedor, monto, dia, cuadra: bool, conciliado: bool) -> None:
    db.add(BankTransactionModel(
        id=uuid.uuid4(),
        company_id=company_id,
        transaction_date=dia,
        amount=monto,
        description=f"{proveedor} {rng.randint(1000, 9999)}",
        reference=f"REF{rng.randint(100000, 999999)}",
        is_reconciled=conciliado,
    ))


async def _conciliaciones(db, rng) -> int:
    """Concilia parte de los movimientos ya sembrados.

    Se hace en bloque, al final, y no mientras se siembran los tickets, por una
    razon de tiempos: la conciliacion necesita el id del movimiento, y si se
    hiciera fila por fila habria que hacer un `flush` por cada una.

    El emparejamiento es **por monto**, no por fecha. Es lo que hace el motor
    real, y tambien lo que decide si la demo es creible: si se empareja cada
    movimiento con el ticket mas reciente del proveedor, casi ninguno coincide en
    importe, y el tablero sale con 98% de discrepancias. Eso no es un tablero
    raro, es un tablero roto que hace creer que el sistema no funciona.
    """
    movimientos = (await db.execute(
        select(BankTransactionModel).where(BankTransactionModel.is_reconciled.is_(True))
    )).scalars().all()

    # Los tickets van indexados por empresa+proveedor una sola vez. Consultar la
    # tabla por cada movimiento son ~400 queries que en un entorno remoto se
    # notan; con el indice son una consulta y comparaciones en memoria.
    todos = (await db.execute(select(TicketModel))).scalars().all()
    por_proveedor: dict[tuple, list[TicketModel]] = {}
    for t in todos:
        por_proveedor.setdefault((t.company_id, t.provider_name), []).append(t)
    for lista in por_proveedor.values():
        lista.sort(key=lambda t: t.expense_date)

    usados: set = set()
    n = 0
    for mov in movimientos:
        proveedor = mov.description.rsplit(" ", 1)[0]
        candidatos = [
            t for t in por_proveedor.get((mov.company_id, proveedor), [])
            if t.id not in usados
        ]
        if not candidatos:
            continue

        # El que mas se acerca en monto, y a igualdad el mas cercano en fecha.
        ticket = min(
            candidatos,
            key=lambda t: (
                abs(mov.amount - t.total_amount),
                abs((mov.transaction_date - t.expense_date).days),
            ),
        )
        usados.add(ticket.id)

        # El estado sale de comparar el movimiento con ese ticket.
        diferencia_dia = abs((mov.transaction_date - ticket.expense_date).days)
        diferencia_monto = abs(mov.amount - ticket.total_amount)

        if diferencia_monto == 0 and diferencia_dia <= 2:
            estado = MatchStatus.PERFECT
        elif diferencia_monto == 0:
            estado = MatchStatus.MANUAL
        else:
            estado = MatchStatus.DISCREPANCY

        db.add(ReconciliationModel(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            bank_transaction_id=mov.id,
            match_status=estado.value,
        ))
        n += 1
    return n


def _lectura(rng: random.Random) -> tuple[ExtractionStatus, Decimal | None, str, bool]:
    """Simula una lectura y devuelve el estado que ESA lectura produce.

    El orden importa y es al reves del intuitivo: primero sale la confianza, y el
    estado sale de ella con las reglas de `app/core/enums.py`:

      - confianza >= 0.90  -> AUTO_APROBADO
      - 0.60 a 0.90        -> REQUIERE_REVISION
      - < 0.60             -> PENDIENTE

    Se sortea al reves (estado y luego confianza) porque se puede inventar el
    estado que se quiera, pero no la confianza que lo justifica. Un ticket
    AUTO_APROBADO con 0.63 no es un dato raro: es un dato imposible, y hacia
    falta el reporte de exactitud y la cola de revision funcionan.

    Los tres estados que el gate **no** produce se sortean aparte, y no por
    capricho:

      - `APROBADO` y `RECHAZADO` son decisiones de una persona, asi que no
        llevan confianza: la confianza mide la lectura, y ahi no hubo lectura que
        medir.
      - Una parte de los AUTO_APROBADO viene del parser por reglas sobre el texto
        del PDF, que no estima confianza en absoluto (por eso su `source` es
        `pdf_text` y su confianza va en `None`). Sin esos, el reporte de
        exactitud mediria un unico tipo de lectura, que es justo lo que la
       ConfidenceSource de `enums.py` dice que no se debe hacer.

    Devuelve `(estado, confianza, origen, hubo_lectura)`.
    """
    # Un reparto realista de lecturas, no uniforme.
    dado = rng.random()

    if dado < 0.08:
        # Parser por reglas sobre el texto del PDF: no estima confianza.
        return ExtractionStatus.AUTO_APROBADO, None, "pdf_text", True

    if dado < 0.13:
        # Una persona lo aprobo o lo rechazo: no hay lectura que medir.
        if rng.random() < 0.7:
            return ExtractionStatus.APROBADO, None, "manual", False
        return ExtractionStatus.RECHAZADO, None, "manual", False

    # El resto si lo leyo un modelo. La confianza se sortea primero.
    #
    # La distribucion NO es uniforme, y esa es la parte que importa. Sortear
    # `uniform(0.42, 0.995)` deja solo un 16% por encima del umbral de 0.90, y
    # con eso el tablero sale con 373 tickets en la cola por 97 aprobados: un 79%
    # de falla, que no describe a ningun sistema que funcione. Una lectura de
    # comprobante con IA sale alta casi siempre --un PDF de proveedor con el
    # total bien impreso se lee bien-- y los casos dudosos son la minoria. Por
    # eso se sortea por bandas con pesos, no de forma uniforme.
    #
    #   0.90 a 0.99  -> 62%   pasa sola
    #   0.75 a 0.90  -> 18%   a revision, pero con confianza alta
    #   0.60 a 0.75  -> 10%   a revision
    #   0.42 a 0.60  -> 10%   no se pudo leer con confianza
    dado = rng.random()
    if dado < 0.62:
        confianza = Decimal(str(round(rng.uniform(0.90, 0.995), 3)))
    elif dado < 0.80:
        confianza = Decimal(str(round(rng.uniform(0.75, 0.90), 3)))
    elif dado < 0.90:
        confianza = Decimal(str(round(rng.uniform(0.60, 0.75), 3)))
    else:
        confianza = Decimal(str(round(rng.uniform(0.42, 0.60), 3)))

    # **El umbral se compara como `Decimal`, no como float.**
    #
    # `AUTO_APPROVE_CONFIDENCE` en `enums.py` es un `float`, y comparar
    # `Decimal("0.9") >= 0.90` da `False`: Python compara Decimal contra float de
    # forma EXACTA, y 0.90 en binario es 0.9000000000000000222..., un pelo mas
    # grande que Decimal("0.9"). Un ticket con confianza justo en el umbral
    # caia a la cola en vez de pasar solo, y no se ve a simple vista: el numero
    # impreso dice 0.900 y parece que deberia haber pasado.
    #
    # Se envuelve en `Decimal(str(...))` para no cambiar `enums.py`: ahi el
    # float es correcto, porque el motor de confianza tambien trabaja con float y
    # el umbral no es dinero. Aqui si se toca el dato, y el dato es Decimal.
    if confianza >= Decimal(str(AUTO_APPROVE_CONFIDENCE)):
        estado = ExtractionStatus.AUTO_APROBADO
    elif confianza >= Decimal(str(REVIEW_CONFIDENCE)):
        estado = ExtractionStatus.REQUIERE_REVISION
    else:
        estado = ExtractionStatus.PENDIENTE

    return estado, confianza, "llm", True


def _muestreo(
    rng: random.Random,
    estado: ExtractionStatus,
    confianza: Decimal | None,
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Si el ticket entra al muestreo, y con que veredicto sale.

    El reporte de exactitud solo puede afirmar algo si hay revisados, y sin
    revisar nada sale `SIN_EVIDENCIA` en todos los origins. Es un estado
    legitimo y asi se vera, pero para ver el tablero completo hace falta que
    algunos tengan veredicto.

    La tasa es la de `enums.SPOT_CHECK_RATE`, y la mayoria queda PENDIENTE: es lo
    real. Un muestreo donde todo esta revisado no se parece a nada y hace creer
    que revisar es gratis.

    Cuando sale INCORRECTO se anota **que campo** fallo, que es lo que el
    reporte usa para decir cual es el mas fallido. Marcarlo sin decir cual es
    information a medias: dice que algo se leyo mal sin decir que, que es igual
    que no decir nada.
    """
    if estado != ExtractionStatus.AUTO_APROBADO:
        return None, None, None, None, None

    if rng.random() > SPOT_CHECK_RATE:
        return None, None, None, None, None

    dado = rng.random()
    if dado < 0.80:
        return SpotCheckStatus.PENDIENTE.value, None, None, None, None
    if dado < 0.93:
        return SpotCheckStatus.CORRECTO.value, utcnow(), None, None, None

    campo = rng.choice(["total_amount", "tax_amount", "expense_date", "provider_name"])
    return (
        SpotCheckStatus.INCORRECTO.value,
        utcnow(),
        f"Campo mal leido en el papel: {campo}",
        campo,
        "revisionado",
    )



def _monto(rng: random.Random, minimo: float, maximo: float) -> Decimal:
    """Un importe con centavos, y no uno plano.

    Se redondea a centimos porque todos los importes reales los tienen, y porque
    una barra que acaba en .00 en todas partes no se parece a ninguna factura.
    """
    valor = rng.uniform(minimo, maximo)
    return _redondea(Decimal(str(valor)))


def _redondea(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("0.01"))


def _primer_dia_de_mes(hoy: date, meses_atras: int) -> date:
    mes = hoy.month - meses_atras
    anio = hoy.year
    while mes <= 0:
        mes += 12
        anio -= 1
    return date(anio, mes, 1)


def _primer_dia_siguiente(dia: date) -> date:
    if dia.month == 12:
        return date(dia.year + 1, 1, 1)
    return date(dia.year, dia.month + 1, 1)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Datos de ejemplo del dashboard")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--limpiar", action="store_true", help="Borra lo que seembro")
    args = parser.parse_args()

    if args.limpiar:
        return await limpiar()
    return await sembrar()


async def _corriendo() -> int:
    """El main y el cierre del pool en el MISMO event loop.

    `asyncio.run(main())` seguido de `asyncio.run(engine.dispose())` --el patron
    que usan los otros scripts-- abre dos loops distintos. El pool se creo en el
    primero y se intenta cerrar en el segundo, y asyncpg lanza
    "attached to a different loop" al final. No rompe la siembra, pero ensucia
    la salida con una traza de veinte lineas que hace pensar que algo fallo
    cuando todo fue bien. Para un script que alguien va a correr a mano, una
    salida limpia no es un detalle.
    """
    try:
        return await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_corriendo()))
