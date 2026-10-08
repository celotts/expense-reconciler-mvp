#!/usr/bin/env python3
"""Verifica el inventario contra Postgres de verdad.

POR QUE ESTE SCRIPT Y NO SOLO LOS TESTS
=======================================

Los tests de `tests/unit/test_inventario.py` corren sobre SQLite, y SQLite no
tiene triggers. Dos de las defensas del inventario viven en triggers de Postgres:

  trg_movimientos_no_reescribir   UPDATE y DELETE prohibidos en el kardex
  trg_movimientos_no_negativo     una entrada no puede dejar stock negativo

Y ADEMAS, DESDE 0013
--------------------

  el signo del trigger               un AJUSTE suma, y una SALIDA resta
  ck_compras_estado                  RECHAZADO existe; lo inventado no
  ck_reconciliations_revision        una revision lleva autor y fecha

Lo del signo tiene historia y conviene que quede escrita: el trigger hacia
`IF tipo = 'ENTRADA' THEN suma ELSE resta`, o sea que trataba `AJUSTE` como resta
mientras `stock_de` lo suma, y su `SUM` de filas previas ignoraba el signo de las
SALIDAS. Era inerte porque la unica via que escribia en el kardex era
`confirmar_compra`, y esa solo produce `ENTRADA`: el `ELSE` del trigger no se
ejecutaba nunca y ningun test de ataque alcanzaba la rama.

`POST /inventario/movimientos` es el camino que lo vuelve vivo, y estas
comprobaciones son las que lo habrian atrapado. La mitad de Python de la misma
regla esta en `TestElSignoDelKardex`; aca se comprueba que Postgres y Python
cuenten lo mismo, que es lo que hace que un movimiento que la API acepta no lo
rechace la base.

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
# La migracion del signo del trigger. Se aplica SIEMPRE, no solo si falta la
# tabla, porque su unico efecto es `CREATE OR REPLACE FUNCTION`: en una base que
# ya tiene `movimientos_inventario` —que es el caso normal— la version vieja del
# trigger es la que esta viva, y es la que tiene el bug de signo.
#
# Sin esto, el script pasaria sus comprobaciones de signo contra una base con el
# trigger viejo y no se enteraria de nada.
MIGRACION_SIGNO = RAIZ / "db" / "migrations" / "0013_compras_rechazables.sql"

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


def _sentencias(sql: str) -> list[str]:
    """Parte un archivo .sql en sentencias ejecutables de una en una.

    POR QUE NO `text(sql)` DE UNA VEZ
    ----------------------------------

    Porque asyncpg no acepta varias sentencias en una sentencia preparada:
    `conn.execute(texto_con_varios_punto_y_coma)` falla con "cannot insert multiple
    commands into a prepared statement". Se vio al intentar aplicar `0013` con el
    metodo que ya usaba el archivo para `0010`.

    Y el metodo de `0010` nunca se habia ejecutado en la practica: solo corria si
    la tabla no existia, y en una base creada con `db/init.sql` —que es lo normal—
    nunca hacia falta. O sea que el fallo no era nuevo: estaba ahi, en el camino
    que nadie tomaba.

    POR QUE NO SE PARTE CON `sql.split(";")`
    -----------------------------------------

    Porque el cuerpo de los triggers es plpgsql y tiene `;` dentro:

        CREATE FUNCTION ... AS $$
        BEGIN
            IF NEW.tipo = 'SALIDA' THEN   <-- punto y coma dentro del bloque
                actual := actual - NEW.cantidad;
        END;
        $$ LANGUAGE plpgsql;

    Partir ahi produce fragmentos que no compilan. `_sentencias` lleva la cuenta de
    los bloques `$$`, de las comillas simples y de los comentarios `--`, y solo
    corta en el `;` que esta fuera de las tres.

    Y los comentarios tambien importan por lo de las comillas: el SQL de estos
    archivos cita codigo en prosa (`-- IF NEW.tipo = 'ENTRADA' THEN ...`), y un
    apostrofo ahi abre una cadena que no se cierra nunca.
    """
    sentencias: list[str] = []
    actual: list[str] = []
    en_dinero = False       # dentro de un $$ ... $$
    en_cadena = False       # dentro de '...'
    en_comentario = False   # despues de --, hasta el fin de linea
    i = 0
    while i < len(sql):
        ch = sql[i]

        # Un comentario se salta entero. Sin esto, el apostrofo de una linea
        # comentada abre una cadena que nunca se cierra y TODO lo que viene
        # despues se pega a la misma sentencia.
        #
        # Y no es un detalle hipotetico: los comentarios de estas migraciones
        # citan codigo SQL, y hay cuatro que lo hacen (`-- IF NEW.tipo =
        # 'ENTRADA' THEN ...`). El primero abria la cadena y las cinco sentencias
        # del archivo acababan en una sola.
        #
        # Solo fuera de `$$`: dentro del cuerpo de un trigger un `--` puede ser un
        # operador de Postgres, y ahi el comentario no existe.
        if en_comentario:
            if ch == "\n":
                en_comentario = False
                actual.append(ch)
            i += 1
            continue

        if not en_dinero and not en_cadena and sql.startswith("--", i):
            en_comentario = True
            i += 2
            continue

        # El bloque tiene que poder ABRIR y CERRAR, asi que la condicion NO lleva
        # `not en_dinero`: con ese guardia el `$$` de apertura entraba y el de
        # cierre no se veia, `en_dinero` se quedaba en True y todo lo que venía
        # despues —las cinco sentencias del archivo— se ejecutaba como una sola.
        if sql.startswith("$$", i):
            en_dinero = not en_dinero
            actual.append("$$")
            i += 2
            continue

        if ch == "'" and not en_dinero:
            # El '' de SQL es una comilla escapada, no el cierre de la cadena.
            if en_cadena and sql.startswith("''", i):
                actual.append("''")
                i += 2
                continue
            en_cadena = not en_cadena

        if ch == ";" and not en_dinero and not en_cadena:
            trozo = "".join(actual).strip()
            if trozo:
                sentencias.append(trozo)
            actual = []
            i += 1
            continue

        actual.append(ch)
        i += 1

    resto = "".join(actual).strip()
    if resto:
        sentencias.append(resto)
    return sentencias


async def _ejecutar_sql(engine, sql: str) -> None:
    """Ejecuta un .sql sentencia por sentencia."""
    async with engine.begin() as conn:
        for sentencia in _sentencias(sql):
            await conn.execute(text(sentencia))


async def _aplicar_migracion(engine) -> None:
    """Aplica lo que falte. Idempotente.

    Son dos archivos y en orden, porque hacen cosas distintas:

    - `0010` crea las tablas. Solo si no existen.
    - `0013` reemplaza la funcion del trigger. SIEMPRE, incluso si las tablas ya
      estan, porque su efecto es `CREATE OR REPLACE FUNCTION` y lo que se quiere es
      corregir la version que hay puesta.

    Aplicarlos al reves daria una base cuyo trigger es el de `0010` (el del bug)
    con las columnas de `0013`, que es la combinacion que no existe en ningun sitio
    y es la que hace que estas comprobaciones no signifiquen nada.
    """
    async with engine.connect() as conn:
        ya_esta = await conn.scalar(
            text("SELECT to_regclass('public.movimientos_inventario') IS NOT NULL")
        )
    if not ya_esta:
        await _ejecutar_sql(engine, MIGRACION.read_text(encoding="utf-8"))

    await _ejecutar_sql(engine, MIGRACION_SIGNO.read_text(encoding="utf-8"))


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

    # --- 8. El signo del trigger, y el del stock de la API -----------------
    #
    # ESTAS SON LAS COMPROBACIONES QUE HABRIAN ATRAPADO EL BUG
    # ==========================================================
    #
    # El trigger hacia `IF NEW.tipo = 'ENTRADA' THEN suma ELSE resta`, o sea que
    # trataba AJUSTE como resta mientras `stock_de` lo suma; y su `SUM` de filas
    # previas ignoraba el signo, de modo que con ENTRADA 10 y SALIDA 4 decia 14 en
    # vez de 6.
    #
    # Las dos cosas eran INERTES: la unica via que escribia en
    # `movimientos_inventario` era `confirmar_compra`, y esa solo produce ENTRADA,
    # con lo que el `ELSE` del trigger no se ejecutaba nunca y ningun test de
    # ataque alcanzaba la rama. `POST /inventario/movimientos` es el camino que lo
    # vuelve vivo.
    #
    # `_stock_de_sql` replica la cuenta de `inventario_service.stock_de` en SQL. No
    # es una comprobacion de la API —eso es `TestElSignoDelKardex` en
    # `tests/unit/test_inventario.py`—: es la de que Postgres y Python cuentan lo
    # mismo, que es lo que hace que un movimiento que la API acepta no lo rechace
    # la base.
    #
    # El `CASE WHEN tipo = 'SALIDA'` es la MISMA pregunta que
    # `TipoMovimiento.suma_stock` en Python. Si uno cambia y el otro no, esto falla.

    async def _stock_de_sql(conn, producto_id):
        """La cuenta del stock, como la hace `stock_de`."""
        return await conn.scalar(
            text(
                "SELECT COALESCE(SUM(CASE WHEN tipo = 'SALIDA' "
                "THEN -cantidad ELSE cantidad END), 0) "
                "FROM movimientos_inventario WHERE producto_id = :p"
            ),
            {"p": producto_id},
        )

    async def _un_ajuste_suma_no_resta(conn, ids):
        """Un AJUSTE tiene que SUMAR en la base. Con `tipo='AJUSTE'`.

        ESTA COMPROBACION USA `tipo='AJUSTE'`, NO `tipo='ENTRADA'` CON
        `referencia_tipo='AJUSTE'`, Y HACE FALTA EXPLICAR POR QUE.
        =====================================================================

        `inventario_service.registrar_movimiento` nunca escribe `tipo='AJUSTE'`: lo
        escribe como ENTRADA o SALIDA segun el signo, con `referencia_tipo='AJUSTE'`
        para decir de donde viene. O sea que por la API esta fila no existe.

        Pero la constraint `ck_movimientos_tipo` la permite, `stock_de` la cuenta
        (`tipo != 'SALIDA'` suma) y un `psql` la puede escribir. El trigger tiene que
        estar de acuerdo con `stock_de` para TODO valor que la base acepte, no solo
        para los que la API produce: si no, hay una fila que la API lee como +5 y la
        baseAccounta como -5.

        Y la primera version de esta comprobacion usaba `ENTRADA` con
        `referencia_tipo='AJUSTE'`, que con el trigger viejo pasaba. Es decir: la
        comprobacion daba verde con el bug puesto. Se vio al revertir la migracion
        a proposito para ver que el script lo detectaba, y no lo detecto. Por eso
        aqui esta escrito.
        """
        await conn.execute(
            text(
                "INSERT INTO movimientos_inventario (company_id, producto_id, "
                "tipo, cantidad, referencia_tipo, actor) VALUES "
                "(:emp, :prod, 'AJUSTE', 5, 'AJUSTE', 'ana@empresa.mx')"
            ),
            {"emp": ids["empresa"], "prod": ids["producto"]},
        )
        total = await _stock_de_sql(conn, ids["producto"])
        if Decimal(str(total)) != Decimal("5"):
            return _motivo(
                f"un AJUSTE de 5 dejo el stock en {total} y deberia estar en 5. "
                "El trigger trata AJUSTE como resta y stock_de lo suma: la base y "
                "la API estan contando la misma fila de dos maneras."
            )
        return None

    await _cada_comprobacion(
        engine, "un AJUSTE suma (el signo del trigger)",
        _un_ajuste_suma_no_resta,
        debe_pasar=True,
    )

    async def _el_signo_de_las_salidas_cuenta(conn, ids):
        """El SUM del trigger tiene que aplicar el signo de las SALIDAS.

        Con ENTRADA 10 y SALIDA 4 el stock es 6. El `SUM(cantidad)` a secas decia
        14, y a partir de ahi toda comprobacion del trigger estaba corrida por 8.
        Este caso es el que mas daño hace y el mas facil de no ver: no bloquea
        donde deberia y no molesta donde no deberia molestar.
        """
        await conn.execute(
            text(
                "INSERT INTO movimientos_inventario (company_id, producto_id, "
                "tipo, cantidad, referencia_tipo, actor) VALUES "
                "(:emp, :prod, 'ENTRADA', 10, 'COMPRA', 'verify.py')"
            ),
            {"emp": ids["empresa"], "prod": ids["producto"]},
        )
        await conn.execute(
            text(
                "INSERT INTO movimientos_inventario (company_id, producto_id, "
                "tipo, cantidad, referencia_tipo, actor) VALUES "
                "(:emp, :prod, 'SALIDA', 4, 'VENTA', 'verify.py')"
            ),
            {"emp": ids["empresa"], "prod": ids["producto"]},
        )
        total = await _stock_de_sql(conn, ids["producto"])
        if Decimal(str(total)) != Decimal("6"):
            return _motivo(
                f"ENTRADA 10 y SALIDA 4 dieron un stock de {total}, y deberia "
                "ser 6. El SUM del trigger no esta aplicando el signo."
            )
        return None

    await _cada_comprobacion(
        engine, "el stock descuenta las SALIDAS (el signo del SUM)",
        _el_signo_de_las_salidas_cuenta,
        debe_pasar=True,
    )

    async def _una_salida_que_no_alcanza_sigue_bloqueada(conn, ids):
        """Y la defensa que el bug Debilita: no dejar el stock en negativo.

        Con ENTRADA 10 y SALIDA 4 quedan 6. Una SALIDA de 7 mas deja -1 y tiene que
        estar bloqueada. Con el `SUM` sin signo, el trigger creia que habia 14 y
        la dejaba pasar: la defensa contra stock negativo no defendia.
        """
        await conn.execute(
            text(
                "INSERT INTO movimientos_inventario (company_id, producto_id, "
                "tipo, cantidad, referencia_tipo, actor) VALUES "
                "(:emp, :prod, 'ENTRADA', 10, 'COMPRA', 'verify.py')"
            ),
            {"emp": ids["empresa"], "prod": ids["producto"]},
        )
        await conn.execute(
            text(
                "INSERT INTO movimientos_inventario (company_id, producto_id, "
                "tipo, cantidad, referencia_tipo, actor) VALUES "
                "(:emp, :prod, 'SALIDA', 4, 'VENTA', 'verify.py')"
            ),
            {"emp": ids["empresa"], "prod": ids["producto"]},
        )
        await conn.execute(
            text(
                "INSERT INTO movimientos_inventario (company_id, producto_id, "
                "tipo, cantidad, referencia_tipo, actor) VALUES "
                "(:emp, :prod, 'SALIDA', 7, 'VENTA', 'verify.py')"
            ),
            {"emp": ids["empresa"], "prod": ids["producto"]},
        )
        return _motivo(
            "trg_movimientos_no_negativo NO bloqueo una SALIDA de 7 con stock de 6 "
            "(ENTRADA 10, SALIDA 4). El stock quedaria en -1. Con el SUM sin signo "
            "el trigger creia que habia 14 y la defensa no defendia."
        )

    await _cada_comprobacion(
        engine, "la salida que no alcanza sigue bloqueada (tras salidas previas)",
        _una_salida_que_no_alcanza_sigue_bloqueada,
    )

    # --- 9. RECHAZADO existe en la constraint ------------------------------
    #
    # No es una defensa contra un ataque sino una comprobacion de que la
    # constraint que se aplica al arranque tiene el estado que el codigo usa. Si
    # `ck_compras_estado` no incluyera RECHAZADO, `POST /inventario/compras/{id}/
    # rechazar` devolveria 500 con un IntegrityError en produccion, y aqui no se
    # veria: los tests arman el esquema desde los modelos, no desde este DDL.

    async def _rechazado_se_puede_guardar(conn, ids):
        await conn.execute(
            text(
                "UPDATE compras SET estado = 'RECHAZADO' WHERE id = :id"
            ),
            {"id": ids["compra"]},
        )
        estado = await conn.scalar(
            text("SELECT estado FROM compras WHERE id = :id"), {"id": ids["compra"]}
        )
        if estado != "RECHAZADO":
            return _motivo(f"el estado quedo en {estado} y deberia ser RECHAZADO")
        return None

    await _cada_comprobacion(
        engine, "una compra se puede rechazar (ck_compras_estado)",
        _rechazado_se_puede_guardar,
        debe_pasar=True,
    )

    async def _un_estado_inventado_no_se_puede(conn, ids):
        await conn.execute(
            text("UPDATE compras SET estado = 'CUALQUIER_COSA' WHERE id = :id"),
            {"id": ids["compra"]},
        )
        return _motivo(
            "ck_compras_estado acepto un estado que no existe. Si acepta "
            "cualquier cadena, un UPDATE basta para poner una compra en un estado "
            "que ningun codigo sabe leer."
        )

    await _cada_comprobacion(
        engine, "un estado de compra inventado no se puede guardar",
        _un_estado_inventado_no_se_puede,
    )

    # --- 10. Una conciliacion revisada lleva autor y fecha -----------------

    async def _revision_sin_autor_no_se_puede(conn, ids):
        await conn.execute(
            text(
                "INSERT INTO reconciliations (id, ticket_id, bank_transaction_id, "
                "match_status, revisado_at) VALUES "
                "(:id, :tk, :bank, 'PERFECT', CURRENT_TIMESTAMP)"
            ),
            {
                "id": uuid.uuid4(),
                "tk": ids["ticket"],
                "bank": None,
            },
        )
        return _motivo(
            "ck_reconciliations_revision NO bloqueo una revision con fecha y sin "
            "autor. Una decision sin quien la tomo no se puede auditar, que es "
            "justo lo que la columna existe para evitar."
        )

    await _cada_comprobacion(
        engine, "una conciliacion revisada necesita autor y fecha",
        _revision_sin_autor_no_se_puede,
    )

    async def _una_revision_completa_si_se_puede(conn, ids):
        await conn.execute(
            text(
                "INSERT INTO reconciliations (id, ticket_id, bank_transaction_id, "
                "match_status, revisado_por, revisado_at) VALUES "
                "(:id, :tk, :bank, 'PERFECT', 'ana@empresa.mx', "
                "CURRENT_TIMESTAMP)"
            ),
            {"id": uuid.uuid4(), "tk": ids["ticket"], "bank": None},
        )
        return None

    await _cada_comprobacion(
        engine, "una conciliacion revisada con autor y fecha si se guarda",
        _una_revision_completa_si_se_puede,
        debe_pasar=True,
    )

    # --- 11. El mismo codigo SI puede existir en otra empresa --------------
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
    print(
        "El kardex es append-only, la firma es obligatoria, el stock no baja de "
        "cero y Postgres cuenta el signo igual que la API."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))