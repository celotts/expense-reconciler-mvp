"""Saca los comprobantes de la base a una carpeta del disco, y verifica cada uno.

POR QUE EXISTE, Y POR QUE ANTES DE BORRARLOS DEL DISCO
======================================================

El escaneo **borra** el comprobante de `Tickets_app` cuando ya esta respaldado en
`ticket_documents`. Eso es correcto y es lo pedido: la carpeta de entrada queda
con solo lo que falta por revisar.

Pero al hacerlo deja de haber dos copias del papel y pasa a haber UNA, y esa
una vive en el volumen de Postgres. Y hay un comando documentado en este mismo
repo que lo destruye:

    make clean   ->   docker compose down -v

Con ese comando, un comprobante que se borrara de la carpeta de entrada deja de
existir en cualquier lado. No hay recuperacion, porque el original estaba en el
disco y el respaldo estaba en lo que se borro.

Este script es la red: saca los bytes a una carpeta ORDINARY del disco, fuera de
Docker, con el mismo nombre y su `sha256` al lado. Sin el, "ya esta en la base"
es una promesa que un comando de seis letras puede romper.

LO QUE HACE
------------
1. Escribe cada `ticket_documents.contenido` en `<destino>/<nombre_archivo>`.
2. Escribe un `_manifiesto.json` con el `sha256` de cada uno y el `ticket_id`.
3. **Vuelve a leer lo que escribio y compara el hash.** Un archivo que se copio
   truncado o a medias es peor que no copiarlo: parece un respaldo y no lo es.

Como verificar es volver a leer del disco y no de la base, el manifiesto y el
contenido dicen la misma verdad aunque la base se caiga despues.

COMO SE USA
-----------

    # una vez, antes de la primera vez que el escaneo borre algo:
    python3 scripts/exportar_documentos.py /tickets_export

    # y de aqui en adelante, cada vez que se quiera tener el respaldo en disco:
    python3 scripts/exportar_documentos.py /tickets_export
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


async def _consultar(sesion) -> list[tuple]:
    """Las versiones VIGENTES de cada comprobante, una por ticket.

    Vigente = la de mayor `version`. Un ticket puede tener varias porque el
    papel se apila y nunca se altera (regla 18): exportar todas seria duplicar
    archivos en disco, y exportar una vieja seria respaldar el papel equivocado.
    """
    from sqlalchemy import select

    from app.models.ticket_document import TicketDocumentModel

    return list(
        (
            await sesion.execute(
                select(TicketDocumentModel).order_by(
                    TicketDocumentModel.ticket_id,
                    TicketDocumentModel.version.desc(),
                )
            )
        )
        .scalars()
        .all()
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "destino",
        type=Path,
        help="Carpeta ORDINARIA del disco donde se dejan los comprobantes. No la de entrada.",
    )
    ap.add_argument("--simular", action="store_true", help="Dice que haria sin escribir.")
    args = ap.parse_args()

    from app.core.database import AsyncSessionLocal
    from app.models.ticket_document import CONTENT_TYPE_POR_DEFECTO, content_type_servible

    destino: Path = args.destino
    entrada = destino.resolve()

    # Nunca se escribe en la carpeta de entrada: si el destino fuera la carpeta
    # que el escaner vacia, este script se estaria metiendo en su propio trabajo.
    from app.core.config import settings

    if entrada == Path(settings.TICKETS_INPUT_DIR).expanduser().resolve():
        print(
            "El destino es la carpeta de ENTRADA del escaner. No se puede: es la "
            "carpeta que este proceso ayuda a vaciar.\n"
            "Usa una carpeta aparte, por ejemplo /tickets_export."
        )
        return 2

    async with AsyncSessionLocal() as sesion:
        documentos = await _consultar(sesion)

    if not documentos:
        print("No hay comprobantes guardados. Nada que exportar.")
        return 0

    # Uno por ticket: la version vigente de cada uno.
    vigentes: dict = {}
    for doc in documentos:
        if doc.ticket_id not in vigentes:
            vigentes[doc.ticket_id] = doc

    print(f"{len(vigentes)} comprobante(s) a exportar en {destino}\n")
    if args.simular:
        for doc in vigentes.values():
            print(f"  {doc.nombre_archivo}  ({doc.tamano} bytes)")
        print("\n--simular: no se escribio nada.")
        return 0

    destino.mkdir(parents=True, exist_ok=True)

    manifiesto = {
        "_QUE_ES": (
            "Respaldo de los comprobantes que el escaner borro de la carpeta de "
            "entrada. Sin esto, la unica copia estaba en el volumen de la base, "
            "que 'make clean' destruye."
        ),
        "documentos": [],
    }

    escritos = 0
    fallidos: list[str] = []

    for doc in vigentes.values():
        nombre = doc.nombre_archivo or f"documento-{doc.ticket_id}.bin"
        ruta = destino / nombre
        contenido = doc.contenido

        # Se escribe y SE VERIFICA leyendo de vuelta. Confiar en que `write`
        # escribio bien es confiar sin comprobar, y un respaldo sin verificar no
        # es un respaldo.
        ruta.write_bytes(contenido)
        releido = ruta.read_bytes()
        digest = hashlib.sha256(releido).hexdigest()

        if digest != doc.sha256 or len(releido) != len(contenido):
            fallidos.append(nombre)
            print(f"  FALLO  {nombre}: el archivo copiado no coincide con la base")
            continue

        escritos += 1
        manifiesto["documentos"].append({
            "ticket_id": str(doc.ticket_id),
            "version": doc.version,
            "archivo": nombre,
            "sha256": digest,
            "tamano": len(releido),
            "content_type": content_type_servible(doc.content_type),
        })
        marca = "ok" if doc.sha256 else "sin hash en la base (verificado por tamano)"
        print(f"  ok     {nombre}  ({len(releido)} bytes, {marca})")

    (destino / "_manifiesto.json").write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n{escritos} escritos y verificados en {destino}")
    print(f"Manifiesto con los sha256: {destino / '_manifiesto.json'}")
    if fallidos:
        print(f"\n{len(fallidos)} NO se pudieron verificar: {', '.join(fallidos)}")
        print("No los tomes como respaldados. Revisa el espacio en disco.")
        return 1
    print("Cada uno se volvio a leer del disco y su hash coincide con el de la base.")
    return 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))