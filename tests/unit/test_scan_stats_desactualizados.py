"""`archivos_desactualizados` tiene que significar DESACTUALIZADO, y no "tiene un
aviso en `last_error`".

EL DEFECTO, MEDIDO
==================

La condicion del contador en `GET /scan/stats` era:

    ScanFileModel.status == PROCESADO and ScanFileModel.last_error.is_not(None)

Y en esta maquina, con 7 archivos escaneados con **"Escanear (solo
inventariar)"** — que por diseño NO crea tickets—, la pantalla decia:

    6 archivos cambiaron desde que se leyeron, pero su ticket ya fue
    corregido a mano y no se sobreescribió

**Es falso en las tres partes:**

| Lo que dice | Lo que paso |
|---|---|
| "cambiaron desde que se leyeron" | no cambiaron nada |
| "su ticket ya fue corregido a mano" | **no hay ticket**: 0 en la base |
| "no se sobreescribió" | no habia nada que sobreescribir |

La causa: el camino de "sin empresa" escribe
`last_error = "leido correctamente pero sin empresa: no se creo ticket"`, que es
un **mensaje informativo**, y el contador lo contaba como si fuera un problema.

POR QUE EL CONTADOR EXISTE
==========================

Porque el caso real no cabe en ningun estado del archivo: se leyo bien, su
ticket existe, pero el papel cambio y **alguien ya habia corregido el ticket a
mano**. Ese archivo queda `PROCESADO` y sin nada que lo delate. Sin este conteo
aparece como un `PROCESADO` mas, igual que uno al dia.

Lo que cuenta el arreglo es exactamente eso: un `OMITIDO` **con** ticket. Sin
ticket no hay correccion humana que preservar; hay una lectura que todavia no se
ha atribuido, y esa se cuenta en otro sitio (`archivos_sin_ticket`) y se resuelve
con el boton de "crear tickets", no con un aviso de este panel.

LO QUE ESTE ARCHIVO PROTEGE
===========================

Que la cifra no vuelva a significar dos cosas. Un contador que se dispara sin que
pase nada es peor que uno que no existe: entrena al operador a ignorar el numero,
y cuando importe de verdad no le va a creer.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.company import CompanyModel
from app.models.scan_file import ScanFileModel
from app.models.ticket import TicketModel

# El texto que escribe el camino de "sin empresa". Es INFORMATIVO: el archivo se
# leyo bien, lo que falta es la decision de a que empresa pertenece.
SIN_EMPRESA = (
    "leido correctamente pero sin empresa: no se creo ticket. "
    "Corre el escaneo con company_id para crear el ticket."
)

# El texto del caso que SI es un desactualizado real: el archivo cambio y el
# ticket ya lo habia corregido una persona, asi que no se sobreescribe.
SOBREESCRITO = (
    "el archivo cambio desde la ultima lectura, pero el ticket ya fue revisado "
    "por una persona y no se sobreescribe. Usa "
    "POST /scan/files/{id}/reprocess con forzar=true si de verdad quieres "
    "reemplazar la correccion."
)


_contador = {"n": 0}


async def _archivo(db_session, ticket_id, *, last_error):
    """Un archivo en el registro.

    `relative_path` es UNIQUE (`scan_files_relative_path_key`), asi que el nombre
    lleva un contador y no un `hash` del texto: dos archivos con el mismo
    `last_error` —que es justo lo que hace este test— producirian el mismo nombre
    y el segundo INSERT reventaria con `IntegrityError`, que es un fallo del
    helper y no del contador que se quiere probar.
    """
    _contador["n"] += 1
    fila = ScanFileModel(
        relative_path=f"a{_contador['n']}.pdf",
        content_hash=f"{_contador['n']:064d}"[-64:],
        status="PROCESADO",
        read_by="ocr",
        last_error=last_error,
        ticket_id=ticket_id,
    )
    db_session.add(fila)
    await db_session.commit()
    return fila


@pytest.fixture
async def con_ticket(db_session):
    empresa = CompanyModel(name="Emp", tax_id=f"T{abs(hash('desact'))%999999999:09d}")
    db_session.add(empresa)
    await db_session.commit()
    ticket = TicketModel(
        company_id=empresa.id,
        provider_name="P",
        total_amount=Decimal("10.00"),
        tax_amount=Decimal("0.00"),
        expense_date=date(2026, 10, 1),
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


class TestElContadorNoMiente:
    async def test_leido_sin_empresa_NO_cuenta_como_desactualizado(
        self, async_client, db_session
    ):
        """El caso medido: 7 archivos sin empresa deben dar CERO.

        Antes daba 6 (o 7), y la pantalla acusaba a alguien de haber corregido
        tickets a mano que no existen.
        """
        for _ in range(3):
            await _archivo(db_session, None, last_error=SIN_EMPRESA)

        r = await async_client.get("/api/v1/scan/stats")
        assert r.status_code == 200
        stats = r.json()

        assert stats["archivos_desactualizados"] == 0
        # Y los archivos se siguen contando como leidos. Lo que no se cuenta como
        # problema no puede desaparecer del total.
        assert stats["total_archivos"] == 3
        assert stats["archivos_sin_ticket"] == 3

    async def test_un_ticket_corregido_a_mano_SI_cuenta(
        self, async_client, db_session, con_ticket
    ):
        """La contra-prueba: el caso real tiene que seguir contando.

        Si este test pasara con `== 0` en todos, el "arreglo" seria borrar el
        contador en vez de corregirlo, y el operador volveria a no ver el trabajo
        que si tiene.
        """
        await _archivo(db_session, con_ticket.id, last_error=SOBREESCRITO)

        stats = (await async_client.get("/api/v1/scan/stats")).json()
        assert stats["archivos_desactualizados"] == 1

    async def test_con_ticket_but_sin_el_aviso_no_cuenta(
        self, async_client, db_session, con_ticket
    ):
        """`last_error` nulo es un archivo leido sin novedad. No es desactualizado.

        El contador es para los que tienen un PROBLEMA, no para todos los que
        tienen ticket.
        """
        await _archivo(db_session, con_ticket.id, last_error=None)

        stats = (await async_client.get("/api/v1/scan/stats")).json()
        assert stats["archivos_desactualizados"] == 0
        assert stats["tickets_vinculados"] == 1

    async def test_ambos_juntos(self, async_client, db_session, con_ticket):
        """Los dos casos juntos: el numero es el de los de verdad, no la suma.

        Es el caso que se va a ver todos los dias: la carpeta tiene archivos ya
        atribuidos y otros que todavia no.
        """
        await _archivo(db_session, None, last_error=SIN_EMPRESA)
        await _archivo(db_session, None, last_error=SIN_EMPRESA)
        await _archivo(db_session, con_ticket.id, last_error=SOBREESCRITO)

        stats = (await async_client.get("/api/v1/scan/stats")).json()
        assert stats["archivos_desactualizados"] == 1
        assert stats["total_archivos"] == 3
        assert stats["archivos_sin_ticket"] == 2