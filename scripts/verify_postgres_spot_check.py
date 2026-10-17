"""El muestreo contra Postgres real, no contra el modelo.

Los tests del proyecto corren contra SQLite, y para el muestreo eso deja tres
cosas sin comprobar que son justo las que pueden fallar en produccion:

  1. Los indices son parciales y su predicado va en `postgresql_where`, que
     SQLite no lee. Un indice mal escrito pasa la suite y no existe en la base
     real. Y peor: un indice que existe pero que el planificador no usa es igual
     a no tenerlo, y eso solo se ve preguntandole al planificador.

  2. Las constraints del muestreo se replicaron en el modelo para que SQLite las
     ejecute. Eso cubre la LOGICA, pero no si el DDL de la migracion coincide
     con lo que el modelo replica. Si divergen, la base acepta lo que el modelo
     prohibe (o al reves), y el error aparece en produccion con un
     IntegrityError que no dice cual de los dos se quedo atras.

  3. `spot_checked_at` es TIMESTAMPTZ. Que la columna se declare con zona y que
     el valor que vuelve traiga zona son dos cosas, y solo la segunda se
     comprueba restando: en SQLite la lectura sale naive y el error salta ahi.

Este script usa la MISMA base migrada y el MISMO codigo de la API.

    python3 scripts/verify_postgres_spot_check.py

Limpia todo en `finally`: un verificador que acumula lo que verifica deja de
poder verificar, porque al cabo de un rato cualquier consulta incluye filas de
prueba y nadie sabe cuales son.
"""

from __future__ import annotations

import os
import asyncio
import sys
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.enums import ConfidenceSource, ExtractionStatus, SpotCheckStatus
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.services.accuracy_service import en_muestra
from app.services.confidence_gate import compute_source_hash

def _fatal(mensaje: str) -> str:
    """Sale con un mensaje, en vez de un KeyError sin pista."""
    print(f"ERROR: {mensaje}", file=sys.stderr)
    raise SystemExit(2)


# El DSN se lee del entorno, no se escribe aqui. Este archivo estuvo versionado
# en un repositorio publico con la password escrita en esta linea, y eso permitia
# a cualquiera entrar a la base de datos. Rotar la password no borra el archivo
# viejo del historial: ademas de rotarla, la password deja de estar en el codigo.
#     export POSTGRES_PASSWORD=...      (o ponla en .env.local)
DSN = "postgresql+asyncpg://postgres:{}@localhost:5434/expense_db".format(
    os.environ.get("POSTGRES_PASSWORD") or _fatal("POSTGRES_PASSWORD no esta definida.")
)

# Marcador de lo que es de este script. Sin el, un ticket de prueba se
# confunde con uno real, y `tax_id` es UNIQUE: la segunda corrida falla.
NOMBRE_VERIFICACION = "VERIFICACION-MUESTREO"
RFC_VERIFICACION = "VERMUE00001"

_fallos: list[str] = []
_comprobaciones = 0


def comprobar(descripcion: str, condicion: bool, detalle: str = "") -> None:
    global _comprobaciones
    _comprobaciones += 1
    if condicion:
        print(f"  ok   {descripcion}")
    else:
        print(f"  FALLA {descripcion}" + (f"\n         {detalle}" if detalle else ""))
        _fallos.append(descripcion)


async def _limpiar(motor) -> None:
    """Borra lo de esta corrida, aunque `main` haya reventado.

    Se hace con `asyncio` y no con `asyncio.run()` dentro de un sync: esta
    funcion se llama desde el `finally` de una corrutina que YA corre en un
    event loop, y `asyncio.run()` ahi lanza
    `RuntimeError: asyncio.run() cannot be called from a running event loop`.
    Es decir: la version anterior no limpiaba nada justo cuando hacia falta,
    que es cuando algo fallo.

    El borrado es por MARCADOR (nombre de la empresa) y no solo por id: si la
    corrida fallo antes de borrar y dejo la empresa puesta, el id de esta
    corrida es distinto y la anterior se quedaria para siempre. Con
    `tax_id` UNIQUE, ademas, esa empresa haria fallar la corrida siguiente.
    """
    try:
        async with motor.begin() as conn:
            await conn.execute(
                text("DELETE FROM companies WHERE name = :n"),
                {"n": NOMBRE_VERIFICACION},
            )
            # Las cuentas de verificacion no cuelgan de ninguna empresa, asi que
            # la limpieza de arriba no las alcanza. Se borran por marcador de
            # dominio (`@verify.local`) y no por correo exacto: el correo lleva
            # un uuid, asi que borrarlo por igualdad solo sacaria el de esta
            # corrida y los de las anteriores se quedarian para siempre.
            await conn.execute(
                text("DELETE FROM users WHERE email LIKE '%@verify.local'"),
            )
        print(f"\n  limpieza: borrado todo lo de {NOMBRE_VERIFICACION!r}")
    except Exception as exc:  # noqa: BLE001
        print(
            f"\n  ATENCION: no se pudo limpiar. Borra a mano:\n"
            f"    DELETE FROM companies WHERE name = '{NOMBRE_VERIFICACION}';\n"
            f"    DELETE FROM users WHERE email LIKE '%@verify.local';"
        )
        print(f"    (el error fue: {exc})")


# ---------------------------------------------------------------------------
# Contenido que SI entra a la muestra
# ---------------------------------------------------------------------------
#
# Se busca de verdad en vez de parchear `en_muestra`. Lo que hay que comprobar
# es que la marca llega a la fila desde la funcion real, no que la funcion
# devuelve True cuando alguien le dice que devuelva True.


def _contenido_en_la_muestra(etiqueta: str) -> bytes:
    import hashlib

    for i in range(2000):
        candidato = f"{etiqueta}-{i}".encode()
        if en_muestra(hashlib.sha256(candidato).hexdigest()):
            return candidato
    raise AssertionError(f"no se encontro contenido en la muestra para {etiqueta}")


async def _crear_ticket(session, company_id, contenido: bytes, **kw):
    from app.api.tickets import _persist_extracted
    from app.core.enums import SourceType
    from app.services.parser_service import TicketExtractionResult

    en_cola = kw.get("en_cola", False)
    extracted = TicketExtractionResult(
        provider_name="SIN PROVEEDOR" if en_cola else "Tiendas Ramirez SA de CV",
        provider_tax_id=None if en_cola else "TRAM910101XXX",
        total_amount=Decimal("0.00") if en_cola else Decimal("1100.00"),
        tax_amount=Decimal("0.00") if en_cola else Decimal("136.00"),
        subtotal=None if en_cola else Decimal("964.00"),
        expense_date=date(2025, 3, 15),
        category="alimentos",
        confidence=0.2 if en_cola else 0.95,
        confidence_source=kw.get("origen", ConfidenceSource.RULES.value),
        raw_text=contenido.decode("utf-8", "ignore"),
    )
    return await _persist_extracted(
        session, company_id, extracted, contenido, SourceType.IMAGE,
        source_file="comprobante.txt",
    )


async def main() -> int:
    motor = create_async_engine(DSN)
    session_factory = async_sessionmaker(motor, expire_on_commit=False)

    try:
        # =====================================================================
        print("\n1. Las columnas existen con el tipo que el codigo espera")
        # =====================================================================
        async with motor.begin() as conn:
            filas = (await conn.execute(text("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'tickets'
                  AND column_name IN (
                    'spot_check_status', 'spot_checked_at',
                    'spot_check_notes', 'spot_check_wrong_fields', 'subtotal')
                ORDER BY column_name
            """))).mappings().all()

        por_columna = {f["column_name"]: f for f in filas}
        esperadas = {
            "spot_check_status": "character varying",
            "spot_checked_at": "timestamp with time zone",
            "spot_check_notes": "text",
            "spot_check_wrong_fields": "text",
            # El subtotal se guardaba con 2 decimales porque es dinero. Con mas
            # decimales, `964.0000` y `964.00` son el mismo numero y Postgres
            # no los distingue, asi que el riesgo real es al reves: una escala
            # de mas guarda precision que el papel no tiene.
            "subtotal": "numeric",
        }
        for nombre, tipo in esperadas.items():
            real = por_columna.get(nombre)
            comprobar(
                f"columna {nombre} existe como {tipo}",
                real is not None and real["data_type"] == tipo,
                f"encontrada: {real['data_type'] if real else 'NO EXISTE'}",
            )
        comprobar(
            "subtotal admite NULL (no se sabe el subtotal de un papel sin subtotal)",
            por_columna["subtotal"]["is_nullable"] == "YES",
            f"is_nullable={por_columna['subtotal']['is_nullable']}",
        )
        async with motor.begin() as conn:
            escala = (await conn.execute(text("""
                SELECT numeric_scale, numeric_precision
                FROM information_schema.columns
                WHERE table_name = 'tickets' AND column_name = 'subtotal'
            """))).one()
        comprobar(
            "subtotal guarda 2 decimales, como el dinero",
            escala[0] == 2,
            f"numeric_scale={escala[0]}",
        )

        # =====================================================================
        print("\n2. Las tres constraints existen Y muerden")
        # =====================================================================
        async with motor.begin() as conn:
            constraints = (await conn.execute(text("""
                SELECT conname, pg_get_constraintdef(oid) AS def
                FROM pg_constraint
                WHERE conrelid = 'tickets'::regclass
                  AND conname LIKE 'ck_tickets_spot_check%'
                ORDER BY conname
            """))).mappings().all()

        por_nombre = {c["conname"]: c["def"] for c in constraints}
        for nombre in (
            "ck_tickets_spot_check_values",
            "ck_tickets_spot_check_verdict_has_date",
            "ck_tickets_spot_check_only_auto",
        ):
            comprobar(f"constraint {nombre} existe", nombre in por_nombre)

        # Y que la LOGICA coincide con la del modelo. Si divergen, la base
        # acepta filas que el modelo prohibe y el error sale en produccion con
        # un IntegrityError que no dice cual de los dos se quedo atras.
        def_valores = por_nombre.get("ck_tickets_spot_check_values", "")
        del_enum = {e.value for e in SpotCheckStatus}
        en_sql = {v for v in ("PENDIENTE", "CORRECTO", "INCORRECTO") if v in def_valores}
        comprobar(
            "la constraint de valores lista los mismos estados que el enum",
            en_sql == del_enum,
            f"DDL: {en_sql or 'ninguno'}, enum: {del_enum}",
        )

        # =====================================================================
        print("\n3. Los indices parciales existen con predicado real")
        # =====================================================================
        async with motor.begin() as conn:
            indices = (await conn.execute(text("""
                SELECT indexname, indexdef FROM pg_indexes
                WHERE tablename = 'tickets'
                  AND indexname LIKE 'ix_tickets_spot_check%'
                ORDER BY indexname
            """))).mappings().all()

        por_indice = {i["indexname"]: i["indexdef"] for i in indices}
        for nombre in ("ix_tickets_spot_check_queue", "ix_tickets_spot_check_report"):
            defi = por_indice.get(nombre, "")
            comprobar(f"indice {nombre} existe", bool(defi))
            comprobar(
                f"{nombre} es PARCIAL (WHERE ...), no un indice completo",
                " WHERE " in defi,
                f"definicion: {defi[:160]}",
            )
        # Un indice sin predicado sobre una columna con 95% de NULL es lo
        # contrario de lo que se quiere: en vez del 5% de la tabla, el 100%.
        #
        # Postgres normaliza el predicado al guardarlo: una columna VARCHAR
        # aparece como `(spot_check_status)::text = 'PENDIENTE'::text`. Por eso
        # se compara sobre una version normalizada y no sobre el texto tal
        # cual. Una comparacion literal seria un test que vigila la redaccion
        # de Postgres y no el indice.
        def _normalizada(definicion: str) -> str:
            for sobra in ("::text", "::character varying", " ", "(", ")"):
                definicion = definicion.replace(sobra, "")
            return definicion

        comprobar(
            "el indice de la cola se limita a los PENDIENTE, no a toda la tabla",
            "spot_check_status='PENDIENTE'" in _normalizada(
                por_indice.get("ix_tickets_spot_check_queue", "")
            ),
            f"definicion: {por_indice.get('ix_tickets_spot_check_queue', '')[:200]}",
        )
        comprobar(
            "el indice del reporte se limita a lo que esta en la muestra",
            "spot_check_statusISNOTNULL" in _normalizada(
                por_indice.get("ix_tickets_spot_check_report", "")
            ),
            f"definicion: {por_indice.get('ix_tickets_spot_check_report', '')[:200]}",
        )

        # =====================================================================
        print("\n4. El planificador USA los indices (un indice que no se usa no existe)")
        # =====================================================================
        # Esta es la comprobacion que SQLite no puede hacer y la que mas
        # importa: un indice parcial mal escrito existe, aparece en
        # pg_indexes, y la consulta de la cola sigue haciendo seq scan sobre
        # toda la tabla. Pasa todas las pruebas estructurales y no acelera nada.
        async with session_factory() as session:
            # Una corrida anterior que haya reventado sin llegar al `finally`
            # deja la empresa puesta, y como `tax_id` es UNIQUE la segunda
            # corrida falla con un IntegrityError que no dice de donde viene.
            # Esto no es cosmetico: un verificador que no se puede re-correr
            # deja de usarse, y un verificador sin usar no verifica nada.
            # El borrado se hace aqui y no solo en el `finally` por eso.
            await session.execute(
                text("DELETE FROM companies WHERE name = :n"),
                {"n": NOMBRE_VERIFICACION},
            )
            await session.commit()

            empresa = CompanyModel(
                name=NOMBRE_VERIFICACION, tax_id=RFC_VERIFICACION,
            )
            session.add(empresa)
            await session.commit()
            await session.refresh(empresa)

            # A partir de aqui se usa el id como valor plano y no
            # `empresa.id`. Motivo concreto: uno de los casos de constraint hace
            # `rollback`, y un rollback expira TODOS los objetos de la sesion.
            # Al volver a tocar `empresa.id` el ORM intenta recargarlo con una
            # consulta perezosa, y en async eso se ejecuta fuera del greenlet y
            # revienta con MissingGreenlet. El sintoma (greenlet) no dice nada
            # de que el problema sea un rollback de tres lineas mas arriba.
            empresa_id = empresa.id

            for etiqueta in ("cola", "fuera"):
                contenido = (
                    _contenido_en_la_muestra(etiqueta) if etiqueta == "cola"
                    else b"fuera-" + etiqueta.encode()
                )
                await _crear_ticket(session, empresa_id, contenido)

            # Volumen para que el planificador tenga algo que elegir. Con pocas
            # filas Postgres prefiere seq scan y con razon: un indice sobre 30
            # filas es mas lento que leerlas todas. Por eso `enable_seqscan` se
            # apaga para FORZAR la decision, en vez de esperar a que el
            # optimizador decida por su cuenta con datos de juguete: asi la
            # comprobacion dice "el indice es utilizable para esta consulta",
            # que es lo que importa, y no "con estos datos salio seq scan".
            fila = TicketModel(
                company_id=empresa_id,
                provider_name="Comercio de Volumen SA de CV",
                provider_tax_id="VOL000101XXX",
                total_amount=Decimal("10.00"),
                tax_amount=Decimal("1.00"),
                subtotal=Decimal("9.00"),
                expense_date=date(2025, 3, 15),
                category="alimentos",
                confidence=Decimal("0.950"),
                confidence_source=ConfidenceSource.RULES.value,
                extraction_status=ExtractionStatus.AUTO_APROBADO.value,
                source_type="image",
                source_file="lote.pdf",
            )
            session.add(fila)
            await session.commit()
            await session.refresh(fila)

            # La cola: solo PENDIENTE de esta empresa.
            #
            # Va por una conexion CRUDA y no por la sesion del ORM, a proposito.
            # En la sesion, cualquier `session.execute` dispara un autoflush
            # que intenta materializar los atributos pendientes de los
            # objetos del ORM, y en async eso salta con MissingGreenlet: el
            # fallo aparece como "greenlet_spawn no fue invocado", que no dice
            # nada de indices. Aqui no hay objetos en juego, solo SQL.
            async def _plan(sql: str) -> str:
                async with motor.connect() as conn:
                    async with conn.begin():
                        await conn.execute(text("SET LOCAL enable_seqscan = off"))
                        filas = (await conn.execute(
                            text(sql), {"c": empresa_id}
                        )).scalars().all()
                return " ".join(filas)

            texto_cola = await _plan("""
                EXPLAIN (COSTS OFF)
                SELECT id FROM tickets
                WHERE company_id = :c AND spot_check_status = 'PENDIENTE'
                ORDER BY created_at
            """)
            comprobar(
                "la consulta de la cola usa ix_tickets_spot_check_queue",
                "ix_tickets_spot_check_queue" in texto_cola,
                f"plan: {texto_cola[:200]}",
            )

            # El reporte: agrupa por origen sobre lo revisado.
            texto_reporte = await _plan("""
                EXPLAIN (COSTS OFF)
                SELECT confidence_source, COUNT(*)
                FROM tickets
                WHERE company_id = :c AND spot_check_status IS NOT NULL
                GROUP BY confidence_source
            """)
            comprobar(
                "la consulta del reporte usa ix_tickets_spot_check_report",
                "ix_tickets_spot_check_report" in texto_reporte,
                f"plan: {texto_reporte[:200]}",
            )

            # =================================================================
            print("\n5. La marca llega a la fila desde la ruta real")
            # =================================================================
            contenido_muestra = _contenido_en_la_muestra("verif-marca")
            t1 = await _crear_ticket(session, empresa_id, contenido_muestra)
            comprobar(
                "un AUTO_APROBADO cuyo hash cae en el 5% queda PENDIENTE",
                t1.spot_check_status == SpotCheckStatus.PENDIENTE.value,
                f"valor: {t1.spot_check_status!r}",
            )
            t1_id = t1.id
            comprobar(
                "la fila guardada tiene el mismo hash que se uso para decidir",
                t1.source_hash == compute_source_hash(contenido_muestra),
                f"guardado: {t1.source_hash!r}",
            )

            contenido_fuera = b"verif-fuera-de-muestra"
            if not en_muestra(compute_source_hash(contenido_fuera)):
                contenido_fuera += b"-1"
                while en_muestra(compute_source_hash(contenido_fuera)):
                    contenido_fuera += b"x"
            t2 = await _crear_ticket(session, empresa_id, contenido_fuera)
            t2_id = t2.id
            comprobar(
                "un AUTO_APROBADO cuyo hash NO cae en el 5% queda sin marcar",
                t2.spot_check_status is None,
                f"valor: {t2.spot_check_status!r}",
            )

            t3 = await _crear_ticket(
                session, empresa_id, _contenido_en_la_muestra("verif-cola"),
                en_cola=True,
            )
            t3_id = t3.id
            comprobar(
                "un ticket que el gate dejo en la cola NO se muestrea",
                t3.spot_check_status is None,
                f"valor: {t3.spot_check_status!r}",
            )
            comprobar(
                "y sigue en la cola, no aprobado por la sombra",
                t3.extraction_status != ExtractionStatus.AUTO_APROBADO.value,
                f"valor: {t3.extraction_status!r}",
            )

            # =================================================================
            # El reporte con la muestra marcada pero SIN ningun veredicto. Va
            # aqui y no al final a proposito: mas abajo este script registra
            # veredictos, y un "reporte vacio" medido despues de eso ya no esta
            # vacio. La comprobacion decia "INCONCLUYENTE != SIN_EVIDENCIA" y
            # el motivo era que se estaba mirando tarde, no que el codigo
            # mintiera: hay un PENDIENTE y ningun veredicto, y eso ya es
            # evidencia (insuficiente), no ausencia de evidencia.
            # =================================================================
            print("\n5b. El reporte con la muestra abierta y sin veredictos")
            from app.api.tickets import get_reporte_exactitud

            vacio = await get_reporte_exactitud(db=session, company_id=empresa_id)
            comprobar(
                "los tres origenes aparecen aunque no tengan datos",
                {m.origen for m in vacio.por_origen} >= {"llm", "pdf_text", "rules"},
                f"origenes: {[m.origen for m in vacio.por_origen]}",
            )
            comprobar(
                "sin veredictos, ningun origen se inventa una exactitud",
                all(m.revisados == 0 for m in vacio.por_origen),
                f"revisados: {[m.revisados for m in vacio.por_origen]}",
            )
            reglas_abierto = next(
                m for m in vacio.por_origen if m.origen == "rules"
            )
            comprobar(
                "pero el que tiene PENDIENTE los cuenta, no los ignora",
                reglas_abierto.pendientes >= 1,
                f"pendientes: {reglas_abierto.pendientes}",
            )
            comprobar(
                "y su exactitud sigue siendo None: pendiente no es acierto",
                reglas_abierto.exactitud is None,
                f"valor: {reglas_abierto.exactitud!r}",
            )
            comprobar(
                "sin veredictos el veredicto global no puede ser CUMPLE",
                vacio.veredicto_global != "CUMPLE",
                f"valor: {vacio.veredicto_global}",
            )
            comprobar(
                "y ningun origen se inventa una exactitud que no midio",
                all(m.exactitud is None for m in vacio.por_origen),
                f"exactitudes: {[m.exactitud for m in vacio.por_origen]}",
            )
            from app.api.tickets import get_spot_check_queue

            cola_inicial = await get_spot_check_queue(
                db=session, company_id=empresa_id, status_filter=None, limit=50,
            )
            comprobar(
                "la cola abierta tiene pendientes y ningun revisado",
                cola_inicial.total_pendientes >= 1 and cola_inicial.total_revisados == 0,
                f"pendientes={cola_inicial.total_pendientes}, "
                f"revisados={cola_inicial.total_revisados}",
            )

            # =================================================================
            print("\n6. El subtotal se guarda y se puede volver a leer")
            # =================================================================
            await session.refresh(t1)
            comprobar(
                "el subtotal del ticket AUTO_APROBADO se guardo",
                t1.subtotal == Decimal("964.00"),
                f"valor: {t1.subtotal!r}",
            )
            comprobar(
                "vuelve como Decimal exacto, no como float",
                isinstance(t1.subtotal, Decimal) and t1.subtotal.as_tuple().exponent == -2,
                f"tipo: {type(t1.subtotal).__name__}, valor: {t1.subtotal!r}",
            )
            await session.refresh(t3)
            comprobar(
                "un ticket sin subtotal guardable lo deja en NULL, no en 0.00",
                t3.subtotal is None,
                f"valor: {t3.subtotal!r}",
            )

            # Un NULL de verdad: un ticket manual sin subtotal.
            manual = TicketModel(
                company_id=empresa_id,
                provider_name="Captura Manual",
                total_amount=Decimal("50.00"),
                tax_amount=Decimal("0.00"),
                subtotal=None,
                expense_date=date(2025, 4, 1),
                extraction_status=ExtractionStatus.APROBADO.value,
                source_type="manual",
            )
            session.add(manual)
            await session.commit()
            await session.refresh(manual)
            manual_id = manual.id
            comprobar(
                "un subtotal desconocido queda NULL, no 0.00",
                manual.subtotal is None,
                f"valor: {manual.subtotal!r}",
            )

            # =================================================================
            print("\n7. Las constraints de Postgres rechazan lo que deben")
            # =================================================================
            # Cada una se prueba con una fila REAL, no leyendo el DDL: un DDL puede
            # estar escrito bien y no estar aplicado.
            casos = [
                (
                    "estado inventado",
                    lambda f: setattr(f, "spot_check_status", "REVISADO"),
                    "ck_tickets_spot_check_values",
                ),
                (
                    "veredicto sin fecha",
                    lambda f: (
                        setattr(f, "spot_check_status", SpotCheckStatus.CORRECTO.value),
                        setattr(f, "spot_checked_at", None),
                    ),
                    "ck_tickets_spot_check_verdict_has_date",
                ),
                (
                    "muestrear lo que no fue automatico",
                    lambda f: setattr(f, "extraction_status", "APROBADO"),
                    "ck_tickets_spot_check_only_auto",
                ),
            ]
            for etiqueta, mutar, constraint_esperada in casos:
                fila = TicketModel(
                    company_id=empresa_id,
                    provider_name="Prueba de constraint",
                    total_amount=Decimal("10.00"),
                    tax_amount=Decimal("1.00"),
                    expense_date=date(2025, 3, 15),
                    extraction_status=ExtractionStatus.AUTO_APROBADO.value,
                    source_type="image",
                    spot_check_status=SpotCheckStatus.PENDIENTE.value,
                )
                session.add(fila)
                await session.flush()
                mutar(fila)
                try:
                    await session.commit()
                    comprobar(f"Postgres rechaza: {etiqueta}", False, "LO ACEPTO")
                except IntegrityError as exc:
                    await session.rollback()
                    comprobar(f"Postgres rechaza: {etiqueta}", True)
                    comprobar(
                        f"  y la culpa es de {constraint_esperada}",
                        constraint_esperada in str(exc.orig),
                        f"el error nombra: {str(exc.orig)[:200]}",
                    )

            # =================================================================
            print("\n8. La fecha de revision vuelve CON zona horaria")
            # =================================================================
            # No se comprueba la columna, que ya se comprobo arriba: se
            # comprueba el VALOR. Son dos cosas distintas, y solo la segunda
            # importa: restar un naive de un aware revienta con TypeError, y
            # el sintoma aparece en la pantalla, no en el almacenamiento.
            fila = TicketModel(
                company_id=empresa_id,
                provider_name="Prueba de zona",
                total_amount=Decimal("10.00"),
                tax_amount=Decimal("1.00"),
                expense_date=date(2025, 3, 15),
                extraction_status=ExtractionStatus.AUTO_APROBADO.value,
                source_type="image",
                spot_check_status=SpotCheckStatus.CORRECTO.value,
            )
            # Se marca CORRECTO sin fecha, que la constraint prohibe, y luego se deja
            # que lo ponga la propia BD con un UPDATE. Asi lo que se comprueba es
            # el camino de verdad de la base, no una asignacion del codigo.
            fila.spot_checked_at = None
            fila.spot_check_status = SpotCheckStatus.PENDIENTE.value
            session.add(fila)
            await session.commit()
            await session.refresh(fila)
            fila.spot_check_status = SpotCheckStatus.CORRECTO.value
            await session.execute(
                text("UPDATE tickets SET spot_checked_at = NOW() WHERE id = :i"),
                {"i": fila.id},
            )
            await session.commit()
            await session.refresh(fila)
            comprobar(
                "un NOW() de Postgres vuelve como instante con zona",
                fila.spot_checked_at is not None
                and fila.spot_checked_at.tzinfo is not None,
                f"valor: {fila.spot_checked_at!r}, tzinfo: "
                f"{getattr(fila.spot_checked_at, 'tzinfo', None)!r}",
            )

            from app.core.time import dias_desde

            try:
                dias = dias_desde(fila.spot_checked_at)
                comprobar(
                    "y se puede restar sin reventar",
                    dias is not None,
                )
            except TypeError as exc:
                comprobar("y se puede restar sin reventar", False, str(exc))

            # =================================================================
            print("\n9. El reporte se arma desde los datos de Postgres, no de memoria")
            # =================================================================
            from app.api.tickets import registrar_veredicto
            from app.schemas.ticket import SpotCheckRequest
            from app.core.security import hashear_contrasena
            from app.models.user import UserModel

            # El estado de partida se mide AQUI, no antes. La seccion 8
            # registro un veredicto con SQL crudo, asi que el conteo de la 5b
            # ya no sirve de referencia: comparar contra el daria un desfase de
            # uno que no tiene nada que ver con la logica que se comprueba.
            antes = await get_spot_check_queue(
                db=session, company_id=empresa_id, status_filter=None, limit=50,
            )
            comprobar(
                "el ticket de la muestra SI aparece en la cola antes de revisarlo",
                t1_id in {t.ticket.id for t in antes.tickets},
                "no estaba en la cola, y deberia",
            )

            # Se registra un veredicto de verdad, por la misma funcion que
            # llama el endpoint. El `usuario` va porque `registrar_veredicto`
            # guarda en `spot_checked_by` el correo del token, y esa columna
            # tiene que quedar con el correo de verdad y no con una constante.
            verificador = UserModel(
                email=f"muestreo{uuid4().hex[:10]}@verify.local",
                nombre="Verificación Muestreo",
                password_hash=hashear_contrasena("contrasena-de-verificacion"),
            )
            session.add(verificador)
            await session.commit()
            await session.refresh(verificador)

            await registrar_veredicto(
                ticket_id=t1_id,
                payload=SpotCheckRequest(correct=True),
                usuario=verificador,
                db=session,
            )

            verificado = (
                await session.execute(
                    text("SELECT spot_checked_by FROM tickets WHERE id = :tid"),
                    {"tid": t1_id},
                )
            ).scalar_one()
            comprobar(
                "el veredicto quedó firmado por el correo del token",
                verificado == verificador.email,
                f"spot_checked_by={verificado!r}, esperado={verificador.email!r}",
            )

            con_datos = await get_reporte_exactitud(db=session, company_id=empresa_id)
            reglas = next(m for m in con_datos.por_origen if m.origen == "rules")
            comprobar(
                "tras un veredicto, ese origen ya tiene exactitud",
                reglas.revisados == 1 and reglas.aciertos == 1,
                f"revisados={reglas.revisados}, aciertos={reglas.aciertos}",
            )
            comprobar(
                "1 de 1 no alcanza para certificar: es INCONCLUYENTE",
                reglas.veredicto == "INCONCLUYENTE",
                f"valor: {reglas.veredicto}",
            )
            comprobar(
                "y por eso el global tampoco dice CUMPLE",
                con_datos.veredicto_global != "CUMPLE",
                f"valor: {con_datos.veredicto_global}",
            )
            comprobar(
                "un origen con confidence_source NULL se reporta como "
                "'desconocido', no se descarta en silencio",
                any(m.origen == "desconocido" for m in con_datos.por_origen),
                f"origenes: {[m.origen for m in con_datos.por_origen]}",
            )

            # Y la cola cuenta lo que quedo, no lo que se pidio.
            # Se pasan `limit` y `status_filter` explicitos: son `Query(...)` de
            # FastAPI, valores pensados para que el los resuelva el inyector de
            # dependencias. Al llamar la funcion como una funcion normal hay que
            # darlos, y sin darlos revienta con
            # `TypeError: int() argument must be ... not 'Query'`, que no
            # dice nada de la cola.
            cola = await get_spot_check_queue(
                db=session, company_id=empresa_id, status_filter=None, limit=50,
            )
            # En comparacion, no en valor absoluto: la seccion 8 ya registro
            # otro veredicto con SQL directo, asi que "revisados == 1" seria
            # falso por construccion y el test estaria midiendo el orden de las
            # secciones. Lo que importa es que registrar un veredicto mueve UNO
            # de la cola a los revisados: ni se queda en los dos lados, ni
            # desaparece.
            comprobar(
                "registrar un veredicto saca su ticket de la cola",
                cola.total_pendientes == antes.total_pendientes - 1,
                f"pendientes antes={antes.total_pendientes}, "
                f"ahora={cola.total_pendientes}",
            )
            comprobar(
                "y lo suma a los revisados, sin doble conteo",
                cola.total_revisados == antes.total_revisados + 1,
                f"revisados antes={antes.total_revisados}, "
                f"ahora={cola.total_revisados}",
            )
            comprobar(
                "y el ticket revisado ya no esta en la cola",
                t1_id not in {t.ticket.id for t in cola.tickets},
                "sigue apareciendo como pendiente",
            )

            # El punto por el que se corrigio el endpoint: sin el filtro de
            # "esta en la muestra", la cola devolvia TODOS los tickets de la
            # empresa, con el 95% que no tiene nada que verificar. Con 10 000
            # tickets serian 10 000 filas en pantalla y 500 con trabajo.
            # Se comprueba por id: el ticket que quedo PENDIENTE entra, y el
            # que nunca fue muestreado no.
            ids_en_cola = {t.ticket.id for t in cola.tickets}
            comprobar(
                "la cola NO trae el ticket que no fue muestreado",
                t2_id not in ids_en_cola,
                "aparecio en la cola sin estar en la muestra",
            )
            comprobar(
                "la cola NO trae el ticket que el gate dejo esperando",
                t3_id not in ids_en_cola,
                "aparecio en la cola sin estar en la muestra",
            )
            comprobar(
                "la cola NO trae la captura manual",
                manual_id not in ids_en_cola,
                "aparecio en la cola sin estar en la muestra",
            )

    finally:
        # Limpiar ANTES de cerrar el motor: hace falta una conexion viva, y
        # `await motor.dispose()` las cierra todas. Al reves, la limpieza no
        # se puede ejecutar y quedan tickets de prueba en la base.
        await _limpiar(motor)
        await motor.dispose()

    print(f"\n{'=' * 70}")
    if _fallos:
        print(f"FALLARON {_comprobaciones - len(_fallos)} de {_comprobaciones} comprobaciones:")
        for f in _fallos:
            print(f"  - {f}")
        return 1
    print(f"{_comprobaciones} comprobaciones contra Postgres real: todas ok")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
