#!/usr/bin/env python3
"""Verifica el inventario contra Postgres de verdad.

POR QUE ESTE SCRIPT Y NO SOLO LOS TESTS
=======================================

Los tests de `tests/unit/test_inventario.py` corren sobre SQLite, y SQLite no
tiene triggers. Dos de las defensas del inventario viven en triggers de Postgres:

  trg_movimientos_no_reescribir   UPDATE y DELETE prohibidos en el kardex
  trg_movimientos_no_negativo     una entrada no puede dejar stock negativo

Las constraints del modelo SI se prueban en los tests (SQLite las ejecuta). Los
dos triggers no tienen equivalente, y `AGENTS.md` ya avisa de que un test verde
sobre SQLite no dice nada de lo que SQLite ignora: las FKs, los `postgresql_where`
y ahora los triggers.

Este script es la red que cubre ese hueco. Sale con 1 si una defensa no esta.

    python3 scripts/verify_postgres_inventario.py
    python3 scripts/verify_postgres_inventario.py --base-url postgresql+asyncpg://...

NO DEJA BASURA
==============

Cada comprobacion corre en su propia transaccion y se revierte al terminar, con
las filas que el propio script creo. La unica cosa que se escribe de verdad es la
migracion, y es idempotente (`CREATE TABLE IF NOT EXISTS`,
`CREATE OR REPLACE FUNCTION`).

POR QUE LA LOGICA ESTA INVERTIDA
================================

Cada comprobacion DEBE terminar en una excepcion. Si el ataque se bloquea,
Postgres lanza y la comprobacion pasa; si no se bloquea, la comprobacion levanta
una RuntimeError con el porque y falla.

Es contraintuitivo, y por eso esta aqui escrito: "un ataque que no fue bloqueado
es un fallo del script" es la forma de que el mensaje que se lee sea "la defensa
esta en pie" cuando todo va bien.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

RAIZ = Path(__file__).resolve().parent.parent
MIGRACION = RAIZ / "db" / "migrations" / "0010_inventario.sql"

BASE_POR_DEFECTO = "postgresql+asyncpg://postgres:postgres@localhost:5434/expense_db"

_ok = 0
_fallidas: list[str] = []


def comprobacion(nombre: str, resultado: str | None) -> None:
    """Registra el resultado.

    `resultado` es None cuando la defensa esta (el ataque fue bloqueado y el
    propio intento lanzo la excepcion que se capturo antes de llegar aqui), y un
    texto con el porque cuando NO esta.
    """
    global _ok
    if resultado is None:
        _ok += 1
        print(f"  ok     {nombre}")
    else:
        _fallidas.append(nombre)
        print(f"  FALLA  {nombre}")
        print(f"         {resultado}")


async def _aplicar_migracion(engine) -> None:
    """Aplica la migracion si falta algo. Idempotente."""
    async with engine.connect() as conn:
        ya_esta = await conn.scalar(
            text("SELECT to_regclass('public.movimientos_inventario') IS NOT NULL")
        )
    if ya_esta:
        return
    async with engine.begin() as conn:
        await conn.execute(text(MIGRACION.read_text(encoding="utf-8")))


async def _fixture(conn) -> dict[str, uuid.UUID]:
    """Filas propias de la comprobacion. Se van con el rollback."""
    ids = {
        "empresa": uuid.uuid4(),
        "ticket": uuid.uuid4(),
        "producto": uuid.uuid4(),
        "compra": uuid.uuid4(),
        "linea": uuid.uuid4(),
    }
    await conn.execute(
        text(
            "INSERT INTO companies (id, name, tax_id) "
            "VALUES (:id, 'Verify Inventario', :tax)"
        ),
        {"id": ids["empresa"], "tax": f"VF-{uuid.uuid4().hex[:10]}"},
    )
    await conn.execute(
        text(
            "INSERT INTO tickets (id, company_id, provider_name, total_amount, "
            "tax_amount, expense_date) "
            "VALUES (:id, :emp, 'PROVEEDOR', 100.00, 16.00, :fecha)"
        ),
        {"id": ids["ticket"], "emp": ids["empresa"], "fecha": date(2026, 9, 15)},
    )
    await conn.execute(
        text(
            "INSERT INTO productos (id, company_id, nombre) "
            "VALUES (:id, :emp, 'Producto Verify')"
        ),
        {"id": ids["producto"], "emp": ids["empresa"]},
    )
    await conn.execute(
        text(
            "INSERT INTO compras (id, company_id, ticket_id, estado, fecha, total) "
            "VALUES (:id, :emp, :tk, 'EN_REVISION', :fecha, 100.00)"
        ),
        {"id": ids["compra"], "emp": ids["empresa"], "tk": ids["ticket"],
         "fecha": date(2026, 9, 15)},
    )
    await conn.execute(
        text(
            "INSERT INTO compra_items (id, compra_id, descripcion, cantidad, "
            "orden) VALUES (:id, :compra, 'Caja', 10, 0)"
        ),
        {"id": ids["linea"], "compra": ids["compra"]},
    )
    return ids


async def _cada_comprobacion(
    engine, nombre: str, cuerpo, preparar=None, *, debe_pasar: bool = False
) -> None:
    """Corre una comprobacion en una transaccion propia y la revierte.

    `cuerpo(conn, ids)` deja que el ATAQUE se ejecute. Si la defensa funciona, la
    sentencia lanza y la excepcion sale propagada hasta aqui, donde se convierte
    en "ok". Si no funciono, `cuerpo` llega al final y devuelve un texto con el
    porque.

    `debe_pasar=True` INVIERTE eso, y hace falta porque no todas las defensas se
    comprueban atacandolas. El indice unico de `productos.codigo` es el ejemplo:
    lo que se quiere comprobar es que NO bloquee, porque el codigo de barras es
    del mundo y las dos empresas tienen que poder tenerlo. Ahi, "no lanzo" es el
    resultado correcto y "lanzo" es el fallo — al reves que en el resto, y sin
    este parametro el script reportaria como defensa rota justo la que esta
    bien puesta.
    """
    async with engine.connect() as conn:
        tx = await conn.begin()
        try:
            ids = await _fixture(conn)
            if preparar is not None:
                await preparar(conn, ids)
            motivo = await cuerpo(conn, ids)
            if debe_pasar:
                # No lanzo: correcto. Que el cuerpo devuelva un motivo aqui es un
                # error de como esta escrita la comprobacion, no una defensa rota.
                if motivo is not None:
                    comprobacion(nombre, f"comprobacion mal escrita: {motivo}")
                else:
                    comprobacion(nombre, None)
            else:
                comprobacion(nombre, motivo)
        except Exception as exc:  # noqa: BLE001
            if debe_pasar:
                comprobacion(
                    nombre,
                    f"una operacion LEGITIMA fue rechazada: "
                    f"{str(exc).strip().splitlines()[0][:140]}",
                )
            else:
                # El bloqueo ES el resultado.
                comprobacion(nombre, None)
        finally:
            await tx.rollback()


def _motivo(que_falto: str) -> str:
    return que_falto


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE_POR_DEFECTO)
    args = parser.parse_args()

    engine = create_async_engine(args.base_url, echo=False)
    try:
        await _aplicar_migracion(engine)
    except Exception as exc:
        print(f"No se pudo hablar con Postgres: {exc}", file=sys.stderr)
        print(
            "\nLa base esta en Docker. Para levantarla:  make up\n"
            "O pasale la URL:  --base-url postgresql+asyncpg://usuario:clave@host:puerto/db",
            file=sys.stderr,
        )
        return 2

    print("Inventario contra Postgres real")
    print("=" * 74)

    # --- 1. Una compra no se puede poner PROCESADO sin firma ---------------
    async def _procesado_sin_firma(conn, ids):
        await conn.execute(
            text("UPDATE compras SET estado = 'PROCESADO' WHERE id = :id"),
            {"id": ids["compra"]},
        )
        return _motivo(
            "ck_compras_confirmacion NO bloqueo un PROCESADO sin confirmada_por. "
            "Un solo UPDATE basta para que el inventario parezca autorizado "
            "sin que nadie lo haya hecho."
        )

    await _cada_comprobacion(
        engine, "una compra no se pone PROCESADO sin firma", _procesado_sin_firma
    )

    # --- 2. Una linea con producto necesita quien la asigno ---------------
    async def _linea_sin_actor(conn, ids):
        await conn.execute(
            text(
                "UPDATE compra_items SET producto_id = :p, "
                "producto_asignado_at = now() WHERE id = :id"
            ),
            {"p": ids["producto"], "id": ids["linea"]},
        )
        return _motivo(
            "ck_compra_items_asignacion NO bloqueo un producto sin "
            "producto_asignado_por. Una linea con producto sin saber quien lo "
            "eligio no se puede auditar."
        )

    await _cada_comprobacion(
        engine, "una linea con producto necesita quien la asigno", _linea_sin_actor
    )

    # --- 3. Una cantidad negativa no entra --------------------------------
    async def _cantidad_negativa(conn, ids):
        await conn.execute(
            text(
                "INSERT INTO movimientos_inventario (company_id, producto_id, "
                "tipo, cantidad, referencia_tipo, actor) VALUES "
                "(:emp, :prod, 'ENTRADA', -5, 'COMPRA', 'verify.py')"
            ),
            {"emp": ids["empresa"], "prod": ids["producto"]},
        )
        return _motivo(
            "ck_movimientos_cantidad_positiva NO bloqueo una cantidad negativa. "
            "El signo va en el tipo; una cantidad negativa es una devolucion, "
            "que es una SALIDA."
        )

    await _cada_comprobacion(
        engine, "una cantidad negativa no se guarda", _cantidad_negativa
    )

    # --- 4. Un ticket no genera dos compras -------------------------------
    async def _dos_compras(conn, ids):
        await conn.execute(
            text(
                "INSERT INTO compras (company_id, ticket_id, estado, fecha, "
                "total) VALUES (:emp, :tk, 'EN_REVISION', :fecha, 100.00)"
            ),
            {"emp": ids["empresa"], "tk": ids["ticket"], "fecha": date(2026, 9, 15)},
        )
        return _motivo(
            "compras.ticket_id UNIQUE NO bloqueo un segundo INSERT para el mismo "
            "ticket. Dos compras del mismo comprobante = el inventario sube el "
            "doble."
        )

    await _cada_comprobacion(engine, "un ticket no genera dos compras", _dos_compras)

    # --- 5. Un codigo no se repite en la misma empresa --------------------
    async def _codigo_repetido(conn, ids):
        for nombre in ("Uno", "Dos"):
            await conn.execute(
                text(
                    "INSERT INTO productos (company_id, nombre, codigo) "
                    "VALUES (:emp, :n, 'COD-REPETIDO')"
                ),
                {"emp": ids["empresa"], "n": nombre},
            )
        return _motivo(
            "ix_productos_codigo NO bloqueo dos productos con el mismo codigo en "
            "la misma empresa."
        )

    await _cada_comprobacion(
        engine, "un codigo no se repite en la misma empresa", _codigo_repetido
    )

    # --- 6. Los triggers del kardex ---------------------------------------
    #
    # Los tres necesitan una ENTRADA previa, asi que van con `preparar`.

    async def _entrada_inicial(conn, ids):
        await conn.execute(
            text(
                "INSERT INTO movimientos_inventario (company_id, producto_id, "
                "tipo, cantidad, referencia_tipo, actor) VALUES "
                "(:emp, :prod, 'ENTRADA', 10, 'COMPRA', 'verify.py')"
            ),
            {"emp": ids["empresa"], "prod": ids["producto"]},
        )

    async def _update_prohibido(conn, ids):
        await conn.execute(
            text(
                "UPDATE movimientos_inventario SET cantidad = 999 "
                "WHERE producto_id = :p"
            ),
            {"p": ids["producto"]},
        )
        return _motivo(
            "trg_movimientos_no_reescribir NO bloqueo el UPDATE. Editar un "
            "movimiento en sitio cambia el historial del inventario sin dejar "
            "rastro; la correccion es un AJUSTE."
        )

    await _cada_comprobacion(
        engine, "un movimiento no se edita (append-only)", _update_prohibido,
        preparar=_entrada_inicial,
    )

    async def _delete_prohibido(conn, ids):
        await conn.execute(
            text("DELETE FROM movimientos_inventario WHERE producto_id = :p"),
            {"p": ids["producto"]},
        )
        return _motivo(
            "trg_movimientos_no_reescribir NO bloqueo el DELETE. Si se puede "
            "borrar un movimiento, el SUM deja de describir el inventario."
        )

    await _cada_comprobacion(
        engine, "un movimiento no se borra (append-only)", _delete_prohibido,
        preparar=_entrada_inicial,
    )

    async def _negativo(conn, ids):
        await conn.execute(
            text(
                "INSERT INTO movimientos_inventario (company_id, producto_id, "
                "tipo, cantidad, referencia_tipo, actor) VALUES "
                "(:emp, :prod, 'SALIDA', 25, 'VENTA', 'verify.py')"
            ),
            {"emp": ids["empresa"], "prod": ids["producto"]},
        )
        return _motivo(
            "trg_movimientos_no_negativo NO bloqueo una SALIDA de 25 con stock "
            "de 10. El stock quedaria en -15 y nadie se enteraria hasta el "
            "conteo fisico."
        )

    await _cada_comprobacion(
        engine, "una salida no deja el stock en negativo", _negativo,
        preparar=_entrada_inicial,
    )

    # --- 7. Una ENTRADA valida SI se guarda -------------------------------
    #
    # Esta va al reves: no es un ataque, es la comprobacion de que las defensas
    # no son tan estrechas que rechacen lo legitimo. Una defensa que bloquea
    # tambien lo valido es un fallo, y no lo detecta ningun ataque.
    async def _entrada_valida(conn, ids):
        await conn.execute(
            text(
                "INSERT INTO movimientos_inventario (company_id, producto_id, "
                "tipo, cantidad, referencia_tipo, actor) VALUES "
                "(:emp, :prod, 'ENTRADA', 7.5, 'COMPRA', 'ana@empresa.mx')"
            ),
            {"emp": ids["empresa"], "prod": ids["producto"]},
        )
        total = await conn.scalar(
            text(
                "SELECT sum(cantidad) FROM movimientos_inventario "
                "WHERE producto_id = :p"
            ),
            {"p": ids["producto"]},
        )
        if Decimal(str(total)) != Decimal("7.500"):
            return _motivo(
                f"una ENTRADA valida de 7.5 no sumo 7.5 (sumo {total}). "
                "Las defensas estan rechazando entradas legitimas."
            )
        return None

    await _cada_comprobacion(
        engine, "una ENTRADA valida si se guarda (7.5 fraccionario)",
        _entrada_valida,
    )

    # --- 8. El mismo codigo SI puede existir en otra empresa --------------
    async def _codigo_otra_empresa(conn, ids):
        await conn.execute(
            text(
                "INSERT INTO productos (company_id, nombre, codigo) "
                "SELECT id, 'De otra empresa', 'COD-PARTIDO' FROM companies "
                "WHERE id <> :emp LIMIT 1"
            ),
            {"emp": ids["empresa"]},
        )
        await conn.execute(
            text(
                "INSERT INTO productos (company_id, nombre, codigo) "
                "VALUES (:emp, 'De la mia', 'COD-PARTIDO')"
            ),
            {"emp": ids["empresa"]},
        )
        # Si las dos insertions llegaron aqui, el indice NO las bloqueo y todo
        # esta bien. Se devuelve None, que con `debe_pasar=True` es "ok".
        #
        # Antes devolvia un motivo aqui, y eso hacia que el script reportara
        # como defensa rota justo la que esta bien puesta: el fallo estaba en
        # el verificador, no en el indice. Verificado a mano con dos INSERT
        # seguidos, que los dos pasaron.
        return None

    await _cada_comprobacion(
        engine, "el mismo codigo si puede existir en otra empresa",
        _codigo_otra_empresa,
        debe_pasar=True,
    )

    await engine.dispose()

    print("=" * 74)
    if _fallidas:
        print(f"\n{_ok} defensas en pie, {len(_fallidas)} SIN DEFENSA:")
        for f in _fallidas:
            print(f"  - {f}")
        return 1

    print(f"\n{_ok} defensas en pie.")
    print("El kardex es append-only, la firma es obligatoria y el stock no baja de cero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))