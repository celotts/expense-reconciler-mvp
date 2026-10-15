"""Que el comprobante se pueda VER, y no solo bajar.

El bug
------

`guardar_documento` guardaba el `content_type` que declaraba el cliente, y el
escaner de carpeta no declara ninguno. Resultado: 7 de 9 documentos guardados
tenian `content_type=NULL`, se servian como `application/octet-stream`, y el
navegador los DESCARGABA en vez de pintarlos. La foto existia, se podia bajar, y
el revisor veia un recuadro vacio — la misma pantalla que muestra un ticket sin
comprobante, que es justo la confusion que este endpoint existe para evitar.

Los otros 2 si lo traian porque entraron por una subida HTTP, que si lo manda. Por
eso el camino de la API se veia bien y el del escaner no.

Lo que NO se hace
-----------------

**No se keyea por la extension.** En este repo hay un documento guardado como
`IMG_4253 2.HEIC` que por sus bytes es un JPEG: la camara del telefono lo nombro
asi. Servirlo como HEIC daria un `Content-Type` que parece correcto y un
recuadro roto. La foto es la misma; lo que falla es la etiqueta.

**No se copia el `Content-Type` declarado.** Viene del cliente, y es la razon de
que exista `content_type_servible`. Aqui no hay nada que agregar: lo declarado se
acepta tal cual y lo que falta se deduce de los bytes.

Y lo que se deduce NO es lo que se sirve: `content_type_servible()` sigue
aplicando la lista cerrada al final. Por eso un HEIC deduce `image/heic`, cae
fuera de la lista y se sirve como octet-stream, que es lo honesto porque ningun
navegador de escritorio lo pinta.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.archivo_real import media_type_real
from app.models.ticket_document import content_type_servible


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
async def ticket_de_prueba(db_session, test_company):
    """Un ticket de los de teclear a mano: sin documento, que es lo que hay que probar."""
    from datetime import date

    from app.models.ticket import TicketModel

    ticket = TicketModel(
        company_id=test_company.id,
        provider_name="Tiendas Ramirez SA de CV",
        total_amount=Decimal("1100.00"),
        expense_date=date(2026, 3, 15),
        raw_text="x",
        source_type="manual",
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


class TestMediaTypeReal:
    """La deduccion por bytes."""

    @pytest.mark.parametrize(
        "contenido,esperado",
        [
            (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00", "image/jpeg"),
            (_png(), "image/png"),
            (b"GIF89a\x01\x00\x01\x00", "image/gif"),
            (b"GIF87a\x01\x00\x01\x00", "image/gif"),
            (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
            (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3", "application/pdf"),
        ],
    )
    def test_reconoce_cada_formato(self, contenido, esperado):
        assert media_type_real(contenido) == esperado

    def test_heic_deduce_heic_y_no_un_tipo_servible(self):
        """HEIC, HEIF y AVIF comparten la caja `ftyp`, y a los tres se les dice lo mismo.

        No hay `image/heic` en la lista cerrada a proposito: ningun navegador de
        escritorio lo pinta, asi que servirlo con ese tipo solo produce un recuadro
        roto CON un `Content-Type` que parece correcto. Lo que sale de aqui es
        `image/heic` y lo que se sirve es octet-stream.
        """
        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00"

        assert media_type_real(heic) == "image/heic"
        assert content_type_servible(media_type_real(heic)) == "application/octet-stream"

    def test_lo_desconocido_no_se_adivina(self):
        """Sin firma no hay tipo. `None` y no "text/plain" ni "octet-stream".

        Adivinar seria volver al problema de trusting al cliente por otra puerta, y
        el cliente ya no es quien decide: es el byte.
        """
        assert media_type_real(b"total: 100.00") is None
        assert media_type_real(b"") is None

    def test_el_pdf_no_se_acepta_con_preambulo(self):
        """`%PDF` tiene que estar en el byte 0.

        Es la regla de `archivo_real` y aqui se mantiene: un PDF con basura delante
        es un archivo manipulado, no un PDF. Aceptarlo con preambulo seria servir
        como `application/pdf` unos bytes que empiezan con otra cosa.
        """
        assert media_type_real(b"basura%PDF-1.7") is None


class TestElNombreNoDecide:
    """La extension es tan poca informacion como el campo del formulario."""

    def test_un_jpeg_que_se_llama_heic_se_deduce_como_jpeg(self):
        """El caso real de este repo: `IMG_4253 2.HEIC` es un JPEG.

        La camara del telefono guardo un JPEG con extension `.HEIC`. Keyear el tipo
        a la extension habria servido `image/heic` — que no esta en la lista y cae
        a octet-stream — y el revisor no veria la foto.
        """
        jpeg = b"\xff\xd8\xff\xe1\x11\x45\x78\x69\x66\x00\x00"

        assert media_type_real(jpeg) == "image/jpeg"
        assert content_type_servible(media_type_real(jpeg)) == "image/jpeg"


class TestGuardarDeduceElTipo:
    """El efecto: lo que entra por el escaner se sirve como lo que es."""

    @pytest.mark.asyncio
    async def test_un_documento_sin_tipo_declarado_se_guarda_con_el_suyo(
        self, db_session, ticket_de_prueba, monkeypatch
    ):
        """Sin `content_type` declarado, se guarda el deducido. Antes: `NULL`.

        Este es el test que muere con el bug. Lo que hacia falta para reproducirlo
        es un documento SIN tipo declarado —el escaner no declara ninguno— y con
        bytes de una firma conocida.
        """
        from app.services.document_service import guardar_documento

        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x00" + b"\x00" * 64
        guardado = await guardar_documento(
            db_session, ticket_de_prueba, jpeg,
            content_type=None,
            nombre_archivo="foto.jpg",
        )

        assert guardado is True
        await db_session.refresh(ticket_de_prueba)
        documento = ticket_de_prueba.documentos[0]
        assert documento.content_type == "image/jpeg"
        # Y lo que se SIRVE es la imagen, no una descarga.
        assert documento.content_type_servible == "image/jpeg"

    @pytest.mark.asyncio
    async def test_un_heic_se_guarda_como_heic_pero_se_sirve_como_descarga(
        self, db_session, ticket_de_prueba
    ):
        """Se guarda lo que ES y se sirve lo que se PUEDE pintar.

        Las dos mitades estan separadas a proposito: guardar el tipo real deja el
        dato disponible, y el allowlist de `content_type_servible` evita prometer al
        navegador algo que no sabe mostrar.
        """
        from app.services.document_service import guardar_documento

        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00" + b"\x00" * 64
        await guardar_documento(
            db_session, ticket_de_prueba, heic,
            content_type=None,
            nombre_archivo="IMG_4253 2.HEIC",
        )

        await db_session.refresh(ticket_de_prueba)
        documento = ticket_de_prueba.documentos[0]
        assert documento.content_type == "image/heic"
        assert documento.content_type_servible == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_un_tipo_declarado_no_se_pisa_con_el_deducido(
        self, db_session, ticket_de_prueba
    ):
        """Lo declarado manda en la columna; el allowlist sigue decidiendo el servicio.

        Cambiar esto seria inventar una regla que nadie pidio: el cliente declaro un
        tipo y se guarda ese. Lo que no se hace es confie en el para SERVIRLO, y eso
        lo sigue decidiendo `content_type_servible` —ver
        `TestElContentTypeNoLoDecideElCliente` en
        `tests/integration/test_documento_api.py`.
        """
        from app.services.document_service import guardar_documento

        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x00" + b"\x00" * 64
        await guardar_documento(
            db_session, ticket_de_prueba, jpeg,
            content_type="text/html",
            nombre_archivo="foto.jpg",
        )

        await db_session.refresh(ticket_de_prueba)
        documento = ticket_de_prueba.documentos[0]
        assert documento.content_type == "text/html"
        # Y al servirlo, `text/html` cae fuera de la lista: no es XSS servido desde
        # el propio dominio.
        assert documento.content_type_servible == "application/octet-stream"

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_las_fotos_ya_guardadas_sirven_como_imagen_sin_tocar_la_base(
        self, db_session, ticket_de_prueba
    ):
        """El caso real, y el que NO arregla escribir la columna.

        Un documento con `content_type=NULL` —como los 7 que dejo el escaner— tiene
        que servirse igual como `image/jpeg`. Y para comprobarlo hay que leer la
        fila YA GUARDADA, porque un backfill habria necesitado un `UPDATE` que
        `trg_ticket_documents_no_actualizar` rechaza: "un documento no se actualiza,
        se agrega una version nueva".

        Por eso la deduccion va en `content_type_servible` y no en la escritura. Un
        arreglo que solo escribiera la columna habria dejado las fotos viejas
        sirviendo como descarga para siempre, con el bug visible solo en las
        nuevas — que es como se esconden estos fallos.
        """
        from app.services.document_service import guardar_documento

        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x00" + b"\x00" * 64
        # Se escribe sin tipo declarado, que es lo que hacia el escaner.
        await guardar_documento(
            db_session, ticket_de_prueba, jpeg, content_type=None, nombre_archivo="f.jpg"
        )
        await db_session.commit()

        # Se vuelve a LEER de la base, no se usa el objeto que quedo en memoria: el
        # punto es que el byte guardado y el tipo que sale al servir son los mismos.
        from sqlalchemy import select

        from app.models.ticket_document import TicketDocumentModel

        fila = (
            await db_session.execute(select(TicketDocumentModel))
        ).scalar_one()

        # Con la columna ya deducida, el allowlist por si sola basta.
        assert fila.content_type_servible == "image/jpeg"

        # Y el caso del bug: si la columna esta en NULL —los documentos que YA
        # estaban en la base—, se deduce del contenido y se sirve igual.
        fila.content_type = None
        assert fila.content_type_servible == "image/jpeg"

    @pytest.mark.asyncio
    async def test_un_documento_ilegible_no_gana_un_tipo_inventado(
        self, db_session, ticket_de_prueba
    ):
        """Sin firma, `content_type` se queda en `NULL`.

        Inventar `application/octet-stream` en la columna pareceria una mejora y es
        una perdida: `NULL` significa "no se sabe que es", que es informacion, y
        `octet-stream` es una afirmacion sobre como hay que servirlo.
        """
        from app.services.document_service import guardar_documento

        await guardar_documento(
            db_session, ticket_de_prueba, b"esto no es un comprobante",
            content_type=None,
            nombre_archivo="notas.txt",
        )

        await db_session.refresh(ticket_de_prueba)
        documento = ticket_de_prueba.documentos[0]
        assert documento.content_type is None
        assert documento.content_type_servible == "application/octet-stream"