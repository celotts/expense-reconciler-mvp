"""El comprobante digitalizado no se altera: se apila.

La defensa que estas pruebas cubren
----------------------------------
`PUT /tickets/{id}/documento` hacia `DELETE` + `INSERT`: los bytes originales
desaparecian y no quedaba ni el hash anterior, ni quien lo cambio, ni cuando,
ni por que. El endpoint ni siquiera tomaba el usuario autenticado.

Eso hace falsa la promesa central del producto: que el comprobante es la
evidencia contra la que se contrasta cualquier lectura. Si el papel se puede
borrar en silencio, el muestreo puede estar midiendo algo que nadie reviso y no
hay forma de demostrarlo.

Lo que ahora
------------
- `ticket_documents` es una cadena: cada version apunta a la que reemplaza con
  `reemplaza_a`, y el vigente es el de mayor `version`.
- `actor` y `motivo` son obligatorios a partir de la segunda version, en el
  servicio y en la base.
- Un trigger de Postgres prohibe `UPDATE` y `DELETE` mientras el ticket exista.

DONDE ESTA QUE NO
-----------------
El trigger es de Postgres y los tests corren sobre SQLite, asi que aqui se
comprueba el SERVICIO (que toda alta pase por el camino correcto) y el motor se
comprueba en `scripts/verify_postgres_documentos.py`. Un test en SQLite que
dijera "el trigger existe" estaria mintiendo.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.ticket_document import TicketDocumentModel
from app.services.document_service import (
    documento_de_ticket,
    documentos_del_ticket,
    guardar_documento,
    reemplazar_documento,
)

COMPROBANTE = b"%PDF-1.7 Tiendas Ramirez SA de CV RFC: TRAM910101XXX TOTAL: 1100.00"


@pytest.fixture
def con_ticket_para_documentos(db_session, test_company):
    """Un ticket con su documento, creado por el camino real.

    Se usa el servicio de verdad y no un INSERT: la regla que se prueba es de la
    cadena, y una fila metida a mano probaria que el INSERT funciona.
    """
    from app.models.ticket import TicketModel
    from app.services.document_service import guardar_documento

    async def _crear():
        ticket = TicketModel(
            company_id=test_company.id,
            provider_name="TIENDAS RAMIREZ SA DE CV",
            total_amount=Decimal("1100.00"),
            tax_amount=Decimal("160.00"),
            expense_date=date(2025, 3, 15),
            source_type="pdf",
            source_file="origen.pdf",
            source_hash=hashlib.sha256(COMPROBANTE).hexdigest(),
        )
        db_session.add(ticket)
        await db_session.commit()
        await db_session.refresh(ticket)
        await guardar_documento(
            db_session, ticket, COMPROBANTE,
            content_type="application/pdf", nombre_archivo="origen.pdf",
        )
        await db_session.commit()
        await db_session.refresh(ticket)
        return ticket

    return _crear


class TestElDocumentoNoSeBorra:
    """La propiedad central: el original sobrevive al reemplazo."""

    async def test_reemplazar_conserva_el_original(
        self, db_session, test_company, con_ticket_para_documentos
    ):
        """Lo que se sube al FINAL tiene que quedar, y lo anterior tambien.

        Es el test que muere si alguien vuelve al `DELETE` + `INSERT`. Antes
        despues del PUT solo existia una fila, la nueva, y los bytes del papel
        original se habian ido con el `DELETE`.
        """
        ticket = await con_ticket_para_documentos()
        antes = await documento_de_ticket(db_session, ticket.id)
        assert antes is not None
        assert antes.contenido == COMPROBANTE

        nuevo = b"%PDF-1.7 otro papel, mejor leido"
        ok = await reemplazar_documento(
            db_session, ticket.id, nuevo,
            actor="ana@test.mx", motivo="la foto estaba cortada",
        )
        assert ok is True

        cadena = await documentos_del_ticket(db_session, ticket.id)
        assert len(cadena) == 2, "el documento anterior se borro en vez de apilarse"
        assert cadena[0].contenido == COMPROBANTE, "los bytes originales se perdieron"
        assert cadena[1].contenido == nuevo

    async def test_la_cadena_va_por_version(
        self, db_session, test_company, con_ticket_para_documentos
    ):
        ticket = await con_ticket_para_documentos()
        for i in range(2):
            await reemplazar_documento(
                db_session, ticket.id, f"%PDF-1.7 version {i}".encode(),
                actor="ana@test.mx", motivo=f"cambio {i}",
            )

        cadena = await documentos_del_ticket(db_session, ticket.id)
        assert [d.version for d in cadena] == [1, 2, 3]
        # Y cada una apunta a la anterior: la cadena se puede recorrer hacia
        # atras y ver exactamente que se fue reemplazando.
        assert cadena[0].reemplaza_a is None
        assert cadena[1].reemplaza_a == cadena[0].id
        assert cadena[2].reemplaza_a == cadena[1].id

    async def test_el_descarga_sigue_dando_el_vigente(
        self, db_session, test_company, con_ticket_para_documentos
    ):
        """Apilar no puede cambiar que se descargue el papel NUEVO.

        Es el riesgo obvio de una cadena: que `documento_de_ticket` se quede con
        la primera por costumbre y sirva un comprobante que ya no es el que tiene
        el ticket.
        """
        ticket = await con_ticket_para_documentos()
        nuevo = b"%PDF-1.7 el vigente"
        await reemplazar_documento(
            db_session, ticket.id, nuevo, actor="ana@test.mx", motivo="releido",
        )

        vigente = await documento_de_ticket(db_session, ticket.id)
        assert vigente.contenido == nuevo
        assert vigente.version == 2

    async def test_el_sha256_es_de_los_bytes_guardados(
        self, db_session, test_company, con_ticket_para_documentos
    ):
        """El hash de la fila tiene que ser el hash DEL CONTENIDO.

        Antes se sellaba con `tickets.source_hash`, que es el hash de lo que el
        ESCANER leyo. En la ruta del escaner coinciden por casualidad, asi que el
        bug dormia; al reemplazar un documento, los bytes nuevos quedaban
        sellados con el hash de los VIEJOS y la columna dejaba de describir lo
        que estaba guardada. Y su unico trabajo es describirlo.
        """
        import hashlib

        ticket = await con_ticket_para_documentos()
        nuevo = b"%PDF-1.7 otro papel"
        await reemplazar_documento(
            db_session, ticket.id, nuevo, actor="ana@test.mx", motivo="prueba",
        )

        vigente = await documento_de_ticket(db_session, ticket.id)
        assert vigente.sha256 == hashlib.sha256(nuevo).hexdigest()
        # Y sigue siendo distinto del hash de lo que el escaner leyo, que es
        # justo la diferencia entre "que leyo" y "que papel hay guardado".
        assert vigente.sha256 != ticket.source_hash


class TestQuienYPorQue:
    """Un cambio de comprobante tiene que tener autor y motivo."""

    async def test_el_reemplazo_guarda_actor_y_motivo(
        self, db_session, test_company, con_ticket_para_documentos
    ):
        ticket = await con_ticket_para_documentos()
        await reemplazar_documento(
            db_session, ticket.id, b"%PDF-1.7 nuevo",
            actor="carlos@empresa.mx", motivo="el original estaba ilegible",
        )

        vigente = await documento_de_ticket(db_session, ticket.id)
        assert vigente.actor == "carlos@empresa.mx"
        assert vigente.motivo == "el original estaba ilegible"

    async def test_la_version_inicial_no_exige_actor(
        self, db_session, test_company, con_ticket_para_documentos
    ):
        """La sube el escaner y no hay nadie detras.

        La regla es "actor y motivo desde la SEGUNDA version", no "siempre": el
        escaner no es una persona y exigirle un nombre seria inventar una
        firma. Ver la constraint `ck_ticket_documents_actor_si_reemplaza`.
        """
        ticket = await con_ticket_para_documentos()
        primera = await documento_de_ticket(db_session, ticket.id)

        assert primera.version == 1
        assert primera.reemplaza_a is None
        # El escaner no firma, y por eso actor queda en NULL, no en "".
        assert primera.actor is None

    async def test_el_servicio_exige_motivo(
        self, db_session, test_company, con_ticket_para_documentos
    ):
        """`reemplazar_documento` no tiene valor por omision para `motivo`.

        Es lo que hace que el compilador lo exija en el punto de llamada, en vez
        de que se pueda olvidar y quede un cambio de comprobante sin explicar.
        """
        import inspect

        parametros = inspect.signature(reemplazar_documento).parameters
        for nombre in ("actor", "motivo"):
            assert parametros[nombre].default is inspect.Parameter.empty, (
                f"{nombre} no puede tener valor por omision en reemplazar_documento"
            )


class TestLoQueNoSeToca:
    async def test_un_ticket_revisado_no_pierde_su_papel(
        self, db_session, test_company, con_ticket_para_documentos
    ):
        """Cambiar el papel invalidaria la revision de una persona.

        Es la regla 7 de `AGENTS.md` aplicada al documento: si alguien ya
        contrasto sus datos contra ese comprobante, sustituirlo por otro deja su
        revision apuntando a un papel que ya no esta. El camino para rehacerlo es
        el endpoint de reproceso, que es explicito.
        """
        from app.core.time import utcnow

        ticket = await con_ticket_para_documentos()
        ticket.reviewed_at = utcnow()
        await db_session.commit()

        ok = await reemplazar_documento(
            db_session, ticket.id, b"%PDF-1.7 otro papel",
            actor="ana@test.mx", motivo="prueba",
        )

        assert ok is False
        vigente = await documento_de_ticket(db_session, ticket.id)
        assert vigente.contenido == COMPROBANTE, "le cambiaron el papel al que ya se reviso"

    async def test_un_ticket_inexistente_no_crea_nada(
        self, db_session, test_company
    ):
        ok = await reemplazar_documento(
            db_session, uuid4(), b"%PDF-1.7",
            actor="ana@test.mx", motivo="prueba",
        )
        assert ok is False


class TestLosTicketsSinPapel:
    """Un ticket tecleado a mano no tiene documento, y no se le inventa uno."""

    async def test_un_ticket_manual_no_tiene_documento(
        self, db_session, test_company
    ):
        from app.models.ticket import TicketModel

        ticket = TicketModel(
            company_id=test_company.id,
            provider_name="TECLEADO A MANO",
            total_amount=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            expense_date=date(2025, 1, 1),
        )
        db_session.add(ticket)
        await db_session.commit()
        await db_session.refresh(ticket)

        assert await documento_de_ticket(db_session, ticket.id) is None
        assert await documentos_del_ticket(db_session, ticket.id) == []
        assert ticket.tiene_documento is False

    async def test_un_documento_vacio_no_se_guarda(
        self, db_session, test_company, con_ticket_para_documentos
    ):
        """Guardar un comprobante de cero bytes seria guardar la prueba de que
        no hay prueba."""
        ticket = await con_ticket_para_documentos()
        antes = len(await documentos_del_ticket(db_session, ticket.id))

        assert await guardar_documento(db_session, ticket, b"") is False
        assert len(await documentos_del_ticket(db_session, ticket.id)) == antes

class TestLaMigracionDeclaraLasReglas:
    """El trigger se comprueba en dos capas, y esta es la primera.

    Un test en SQLite que comprobara que el trigger BLOQUEA no probaria nada:
    SQLite no tiene PL/pgSQL, y un trigger que no esta escrito es
    indistinguible de uno que no hace nada. Asi que aqui se comprueba lo
    DECLARATIVO (que la migracion y `init.sql` digan lo que tienen que decir), y
    el bloqueo de verdad se comprueba en `scripts/verify_postgres_documentos.py`,
    contra la base real.

    Comparar el texto de la migracion no es una prueba tibia: es lo mismo que
    hace `verify_scan_mutations.py` con las acciones validas del registro, y
    funciona por una razon concreta. Si alguien quita el trigger del archivo, el
    texto deja de coincidir y el test muere.
    """

    def _archivo(self, nombre: str) -> str:
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[2]
        return (raiz / nombre).read_text(encoding="utf-8")

    @staticmethod
    def _sentencias(texto: str) -> str:
        """El archivo SIN las lineas comentadas.

        Sin esto, comprobar que "CREATE TRIGGER x" esta en el archivo no dice
        nada: comentar la linea con `--` deja el texto intacto y el test pasaria
        con la defensa quitada. Es lo que hacia que dos mutaciones sobrevivieran
        a la primera version de este archivo.

        Se quitan las lineas cuyo primer caracter no vacio es `--`, que es como
        se comenta en los dos archivos (SQL y Python). Lo que queda son las
        sentencias que Postgres va a ejecutar de verdad.
        """
        return "\n".join(
            linea for linea in texto.splitlines()
            if not linea.strip().startswith("--")
        )

    @pytest.mark.parametrize("archivo", [
        "db/migrations/0009_documento_inmutable.sql",
        "db/init.sql",
    ])
    def test_declara_los_dos_triggers(self, archivo):
        texto = self._sentencias(self._archivo(archivo))
        assert "CREATE TRIGGER trg_ticket_documents_no_actualizar" in texto, archivo
        assert "CREATE TRIGGER trg_ticket_documents_no_borrar" in texto, archivo

    @pytest.mark.parametrize("archivo", [
        "db/migrations/0009_documento_inmutable.sql",
        "db/init.sql",
    ])
    def test_el_trigger_de_delete_permite_la_cascada(self, archivo):
        """El trigger tiene que distinguir "me robe el papel" de "borre el gasto".

        Es la unica parte de la regla que parece una excepcion: sin ella, borrar
        una empresa (que `DELETE /companies/{id}` hace) dejaria todos sus
        comprobantes huerfanos. La excepcion esta en que se comprueba si el
        ticket padre SIGUE existiendo.
        """
        texto = self._sentencias(self._archivo(archivo))
        assert "ticket_documents_no_borrar" in texto, archivo
        assert "EXISTS (SELECT 1 FROM tickets" in texto, (
            f"{archivo}: el trigger de DELETE tiene que dejar pasar la cascada"
        )

    @pytest.mark.parametrize("archivo", [
        "db/migrations/0009_documento_inmutable.sql",
        "db/init.sql",
    ])
    def test_declara_las_constraints_de_actor_y_motivo(self, archivo):
        texto = self._sentencias(self._archivo(archivo))
        assert "ck_ticket_documents_actor_si_reemplaza" in texto, archivo
        assert "ck_ticket_documents_motivo_si_reemplaza" in texto, archivo
