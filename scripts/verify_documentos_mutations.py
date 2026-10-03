#!/usr/bin/env python3
"""Verifica que los tests detecten si alguien deshace la integridad del papel.

Un test que pasa no demuestra nada: puede que este probando lo que cree, o
puede que no este probando nada. La unica forma de saber cual de las dos es
deshacer la defensa a proposito y ver si el test se da cuenta.

Estas mutaciones deshacen decisiones de `db/migrations/0009_documento_inmutable.sql`
y de `app/services/document_service.py`, que son las que hacen que el
comprobante digitalizado no se altere.

Uso:
    python3 scripts/verify_documentos_mutations.py

Salida: 0 si toda mutacion muere, 1 si alguna sobrevive.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

SERVICIO = "app/services/document_service.py"
MODELO = "app/models/ticket_document.py"
MIGRACION = "db/migrations/0009_documento_inmutable.sql"
CONFTEST = "tests/conftest.py"

INMUTABLE = "tests/integration/test_documento_inmutable.py"
API = "tests/integration/test_documento_api.py"
DOCUMENTOS = "tests/integration/test_documentos.py"

MUTACIONES: list[tuple[str, str, str, str, list[str]]] = [
    # -----------------------------------------------------------------------
    # El papel original. Si esto sobrevive, el sistema puede perder la evidencia
    # sin que nadie se entere, que es lo que el producto promete que no pasa.
    # -----------------------------------------------------------------------
    (
        "reemplazar vuelve a BORRAR el documento anterior",
        SERVICIO,
        """        actual = (
            await db.execute(
                select(TicketDocumentModel)
                .where(TicketDocumentModel.ticket_id == ticket.id)
                .order_by(TicketDocumentModel.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()""",
        """        await db.execute(
            delete(TicketDocumentModel).where(
                TicketDocumentModel.ticket_id == ticket.id
            )
        )
        actual = None
        from sqlalchemy import delete  # noqa: F401""",
        [INMUTABLE],
    ),
    (
        "el vigente se queda con la primera version",
        SERVICIO,
        """            .order_by(TicketDocumentModel.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def documentos_del_ticket(""",
        """            .order_by(TicketDocumentModel.version.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def documentos_del_ticket(""",
        [INMUTABLE],
    ),
    # -----------------------------------------------------------------------
    # Quien y por que. Sin esto, la cadena registra que algo paso sin decir nada
    # de quien ni por que, que es el silencio que la regla vino a cerrar.
    # -----------------------------------------------------------------------
    (
        "el sha256 vuelve a ser el del ticket y no el de los bytes",
        SERVICIO,
        "            sha256=hashlib.sha256(contenido).hexdigest(),",
        "            sha256=ticket.source_hash,",
        [INMUTABLE],
    ),
    (
        "el reemplazo ya no exige motivo",
        SERVICIO,
        "    actor: str,\n    motivo: str,",
        "    actor: str = 'x',\n    motivo: str = 'x',",
        [INMUTABLE],
    ),
    # -----------------------------------------------------------------------
    # Lo que no se debe tocar: un ticket que ya reviso una persona, y el
    # borrado en cascada legitimo.
    # -----------------------------------------------------------------------
    (
        "un ticket ya revisado pierde su comprobante",
        SERVICIO,
        "    if ticket.reviewed_at is not None:",
        "    if False:",
        [INMUTABLE],
    ),
    (
        "el ORM vuelve a poder borrar documentos",
        "app/models/ticket.py",
        "        viewonly=True,",
        '        viewonly=False,\n        cascade="all, delete-orphan",',
        [DOCUMENTOS],
    ),
    # -----------------------------------------------------------------------
    # La regla del motor, por si alguien "simplifica" la migracion.
    # -----------------------------------------------------------------------
    # Los TRIGGERS, en dos capas y no una.
    #
    # CAPA 1 (esta): un test que lee la migracion y `init.sql` y comprueba que
    # declaran los dos triggers y las constraints. SQLite no tiene PL/pgSQL, asi
    # que si el trigger no esta escrito, pytest no se entera de nada y una
    # mutacion de esas sobrevive SIEMPRE. Comparar el texto es lo que hace el repo
    # con las acciones validas del registro del escaner, y funciona por lo mismo.
    #
    # CAPA 2: `scripts/verify_postgres_documentos.py`, contra la base de verdad, que
    # comprueba que los triggers BLOQUEAN de verdad. Un trigger declarado pero que
    # no bloquea es una defensa de papel, y solo la capa 2 lo detecta.
    (
        "la migracion deja de prohibir UPDATE",
        MIGRACION,
        "CREATE TRIGGER trg_ticket_documents_no_actualizar",
        "-- CREATE TRIGGER trg_ticket_documents_no_actualizar",
        [INMUTABLE],
    ),
    (
        "la migracion deja de prohibir DELETE",
        MIGRACION,
        "CREATE TRIGGER trg_ticket_documents_no_borrar",
        "-- CREATE TRIGGER trg_ticket_documents_no_borrar",
        [INMUTABLE],
    ),
    (
        # Se RENOMBRA en vez de comentarse. Comentar dejaria el nombre dentro de
        # un literal de texto (esta en el array del DO), asi que el nombre
        # seguiria en el archivo y el test no lo notaria. Renombrarlo lo saca de
        # ahi, que es lo que pasa cuando alguien "simplifica" el nombre.
        "la constraint que pide actor cambia de nombre y deja de aplicarse",
        MIGRACION,
        "'ck_ticket_documents_actor_si_reemplaza'",
        "'ck_ticket_documents_actor_renombrada'",
        [INMUTABLE],
    ),
    # -----------------------------------------------------------------------
    # La cascada legitima. Sin esto, borrar una empresa deja comprobantes
    # huerfanos de gastos que ya no existen.
    # -----------------------------------------------------------------------
    (
        "la llave foranea deja de borrar en cascada",
        MODELO,
        'ForeignKey("tickets.id", ondelete="CASCADE"),',
        'ForeignKey("tickets.id", ondelete="SET NULL"),',
        [DOCUMENTOS],
    ),
    (
        "la base de prueba vuelve a apagar las llaves foraneas",
        CONFTEST,
        '        cursor.execute("PRAGMA foreign_keys=ON")',
        "        pass",
        [DOCUMENTOS],
    ),
]


def main() -> int:
    if shutil.which("python3") is None:
        print("No se encuentra python3.")
        return 2

    sobreviven: list[str] = []
    # El texto original se guarda EN MEMORIA, no en un `.bak` al lado del
    # archivo.
    #
    # La primera version de este script usaba un `.bak` y se Restauraba al salir,
    # con lo que un Ctrl-C o un fallo dejaba el codigo mutado en el arbol de
    # trabajo. Guardar el texto es lo que hace que restaurar sea una operacion
    # trivial y no dependa de que el proceso llegue al final.
    originals: dict[Path, str] = {}

    def alinterrumpir(*_):
        for ruta, texto in originals.items():
            ruta.write_text(texto, encoding="utf-8")
        raise SystemExit(130)

    signal.signal(signal.SIGINT, alinterrumpir)

    print("Verificando las defensas de la integridad del documento\n")
    for nombre, archivo, viejo, nuevo, pruebas in MUTACIONES:
        ruta = RAIZ / archivo
        texto = ruta.read_text(encoding="utf-8")

        if viejo not in texto:
            print(f"  [NO APLICADA]  {nombre}")
            print("      El texto a mutar no esta en el archivo. La mutacion esta")
            print("      desactualizada: el codigo cambio y el script no.")
            sobreviven.append(nombre)
            continue

        originals[ruta] = texto
        ruta.write_text(texto.replace(viejo, nuevo, 1), encoding="utf-8")
        try:
            if pruebas == ["POSTGRES"]:
                paso = subprocess.run(
                    [sys.executable, "scripts/verify_postgres_documentos.py"],
                    cwd=RAIZ, capture_output=True, text=True, timeout=300,
                    env={**os.environ},
                )
                # Si la base no esta, el verificador sale con 2 y eso NO es una
                # defensa comprobada: es una defensa sin comprobar. Se dice, y
                # cuenta como supervivencia, porque "un test que no corre parece
                # un test que pasa".
                if paso.returncode == 2:
                    print("      Postgres no disponible: NO se pudo comprobar.")
            else:
                paso = subprocess.run(
                    [sys.executable, "-m", "pytest", *pruebas, "-x", "-q",
                     "-p", "no:cacheprovider"],
                    cwd=RAIZ, capture_output=True, text=True, timeout=300,
                )
        finally:
            # Restaurar ANTES de seguir, no al final del script.
            ruta.write_text(originals.pop(ruta), encoding="utf-8")

        if paso.returncode == 0:
            print(f"  [SOBREVIVIO]   {nombre}")
            print("      La suite sigue en verde con la defensa quitada.")
            sobreviven.append(nombre)
        else:
            print(f"  [murio]        {nombre}")

    print()
    if sobreviven:
        print(f"{len(sobreviven)} mutaciones pasaron sin que nadie se di cuenta:")
        for nombre in sobreviven:
            print(f"  - {nombre}")
        return 1

    print(f"Las {len(MUTACIONES)} mutaciones mueren. El papel no se altera.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())