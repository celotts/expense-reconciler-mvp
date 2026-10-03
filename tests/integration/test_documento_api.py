"""El endpoint que devuelve el comprobante: que lo sirva, y sobre todo que no lo regale.

Este archivo prueba el contrato HTTP. La escritura y la unicidad estan en
`test_documentos.py`; aqui lo que importa es que lo que sale por la red sea lo
correcto y lo seguro.

Lo que se protege, en orden de gravedad
---------------------------------------

1. **Que no se sirva un `Content-Type` del cliente.** Es la parte de seguridad y
   va primero porque es la que no falla nunca de forma visible: el PDF se abre
   bien, el test de "descargar el comprobante" pasa en verde, y lo que se tiene
   es XSS servido desde el propio dominio con la sesion de un empleado. Un test
   que solo comprueba que el PDF baja bien no ve nada de esto.

2. **Que un ticket sin documento no se confunda con un ticket que no existe.**
   Los dos son "no hay nada aqui", y con el mismo codigo no se puede
   Distinguished entre "te equivocaste de id" y "este ticket se tecleo a mano".

3. **Que el reemplazo no cree gastos.**
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.enums import SourceType
from app.models.company import CompanyModel
from app.services.capture import capture_ticket
from app.services.confidence_gate import compute_source_hash


TICKET_TEXTO = (
    b"Tiendas Ramirez SA de CV\n"
    b"RFC: TRAM910101XXX\n"
    b"FECHA EXPEDICION: 15/03/2025\n"
    b"SUBTOTAL 964.00\n"
    b"IVA (16%) 136.00\n"
    b"TOTAL 1,100.00\n"
)


@pytest.fixture
def con_ticket(db_session, test_company):
    """Un ticket creado por el camino real, con su documento guardado."""
    from app.api.tickets import _persist_extracted

    async def _crear(contenido=None, content_type="text/plain", nombre="ticket.txt"):
        contenido = contenido or TICKET_TEXTO
        extracted = await capture_ticket(contenido, "text")
        return await _persist_extracted(
            db_session, test_company.id, extracted, contenido,
            SourceType.PDF, nombre, content_type=content_type,
        )

    return _crear


class TestElEndpointDevuelveElComprobante:
    """El camino que hace posible el muestreo."""

    @pytest.mark.asyncio
    async def test_devuelve_los_bytes_exactos(self, async_client, con_ticket):
        ticket = await con_ticket()

        r = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")

        assert r.status_code == 200
        # Byte a byte. Una recompresion serviria para "se ve igual" y no para
        # auditar si el total leido es el que dice el papel.
        assert r.content == TICKET_TEXTO

    @pytest.mark.asyncio
    async def test_lo_sirve_para_que_se_pueda_VER_no_solo_descargar(
        self, async_client, con_ticket
    ):
        """`inline` y no `attachment`, y esto no es un detalle de UI.

        El muestreo pregunta si la lectura coincidio con el papel, y para
        responder hay que VER el papel. Con `attachment` habria que descargarlo,
        abrirlo en otro programa y volver a la pantalla; en un celular, en la
        cola de revision, ese es el camino que hace que la gente termine
        revisando a ciegas sin darse cuenta.
        """
        ticket = await con_ticket()

        r = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")

        assert "inline" in r.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_el_nombre_del_archivo_llega_al_navegador(
        self, async_client, con_ticket
    ):
        """El nombre es lo que el revisor reconoce del papel en su pantalla."""
        ticket = await con_ticket(nombre="oaxaca-marzo.pdf")

        r = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")

        assert "oaxaca-marzo.pdf" in r.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_un_png_se_sirve_como_imagen(self, async_client, con_ticket):
        """El camino feliz de una foto, que es la mitad de las capturas."""
        ticket = await con_ticket(
            contenido=b"\x89PNG\r\n\x1a\n" + b"foto" * 100,
            content_type="image/png",
            nombre="ticket.png",
        )

        r = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/png")


class TestElContentTypeNoLoDecideElCliente:
    """La parte de seguridad. Es lo mas importante de este archivo.

    El `Content-Type` de la subida lo declara el cliente, y servirlo tal cual
    convierte este endpoint en un lugar donde se puede ejecutar script: un
    archivo con `text/html` y bytes que el sistema no ha revisado se abre en el
    navegador, dentro del dominio de la app, con la sesion de un empleado
    iniciada. Quien lo suba no tiene que ser un atacante: basta con que una
    persona con acceso suba un archivo con esa extension.

    Y el sintoma es que todo lo demas funciona. El PDF se abre bien, el test de
    "descargar el comprobante" pasa en verde, y lo que hay es un XSS. Por eso
    estos tests miran el `Content-Type` de la respuesta y no solo que baja algo.
    """

    @pytest.mark.asyncio
    async def test_un_html_no_se_sirve_como_html(self, async_client, con_ticket):
        """El caso de XSS. Sale `octet-stream`, que descarga en vez de abrir."""
        ticket = await con_ticket(
            contenido=b"<script>alert(document.cookie)</script>",
            content_type="text/html",
            nombre="comprobante.html",
        )

        r = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")

        assert r.status_code == 200
        assert "text/html" not in r.headers["content-type"]
        assert r.headers["content-type"] == "application/octet-stream"
        # Y los bytes no se tocan: se sirve como descarga, no se modifica.
        assert r.content == b"<script>alert(document.cookie)</script>"

    @pytest.mark.asyncio
    async def test_un_svg_no_se_sirve_como_svg(self, async_client, con_ticket):
        """SVG es HTML con superpoderes de dibujo.

        Se ve como una imagen y abre en el navegador, asi que la lista de tipos
        servibles tiene que excluirla. Es el caso que mas se olvida, porque el
        filtro de "que sea una imagen" la deja pasar.
        """
        ticket = await con_ticket(
            contenido=b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
            content_type="image/svg+xml",
            nombre="ticket.svg",
        )

        r = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")

        assert "svg" not in r.headers["content-type"]
        assert r.headers["content-type"] == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_un_tipo_con_parametros_no_engana_la_lista(self, async_client, con_ticket):
        """`text/html; charset=utf-8` no es un tipo servible.

        El filtro compara contra la lista cerrada sin tomar en cuenta los
        parametros. Comparar el valor completo habria dejado pasar
        `text/html; charset=utf-8`, que es exactamente lo mismo que `text/html`
        para lo que importa, que es como lo interpreta el navegador.
        """
        ticket = await con_ticket(
            contenido=b"<html><body>hola</body></html>",
            content_type="text/html; charset=utf-8",
        )

        r = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")

        assert r.headers["content-type"] == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_mayusculas_y_espacios_no_enganan_la_lista(
        self, async_client, con_ticket
    ):
        """`TEXT/HTML` es `text/html`. El tipo HTTP no distingue mayusculas."""
        ticket = await con_ticket(
            contenido=b"<script/>", content_type="  TEXT/HTML  ",
        )

        r = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")

        assert r.headers["content-type"] == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_sin_content_type_sirve_como_descarga(
        self, async_client, con_ticket
    ):
        """Sin tipo declarado no se adivina uno.

        Adivinar por la extension seria volver al problema de trusting al
        cliente por otra puerta: el archivo se llama `.pdf` y el sistema lo
        sirve como PDF sin haberlo leido.
        """
        ticket = await con_ticket(contenido=b"contenido", content_type=None)

        r = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")

        assert r.headers["content-type"] == "application/octet-stream"


class TestLoQueNoExiste:
    """Dos "no hay nada" que tienen que ser distinguibles."""

    @pytest.mark.asyncio
    async def test_un_ticket_inexistente_dice_que_no_existe(
        self, async_client
    ):
        r = await async_client.get(f"/api/v1/tickets/{uuid4()}/documento")
        assert r.status_code == 404
        assert "Ticket not found" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_un_ticket_sin_documento_lo_dice_con_un_motivo(
        self, async_client, db_session, test_company
    ):
        """El mensaje distingue las dos causas, porque piden cosas distintas.

        Un ticket de captura manual no tiene documento y no deberia: nadie tiene
        que volver a subir nada. Uno de una captura antigua si deberia, y la
        solucion es volver a subirlo. Con un "no hay documento" seco, la primera
        persona que lo lea lo reporta como un fallo del sistema, y el problema
        real - que no se estan guardando los archivos - queda escondido debajo
        de un ticket que se tecleo a mano.
        """
        from app.api.tickets import create_ticket
        from app.schemas.ticket import TicketCreate

        ticket = await create_ticket(
            TicketCreate(
                company_id=test_company.id,
                provider_name="OXXO",
                total_amount=Decimal("150.00"),
                expense_date=date(2025, 3, 15),
            ),
            db=db_session,
        )

        r = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")

        assert r.status_code == 404
        detalle = r.json()["detail"]
        assert "manual" in detalle.lower()
        # Y no es un 204, que el cliente leeria como un exito.
        assert r.status_code != 204


class TestReemplazarElDocumento:
    """El reintento: lo que hace que un fallo de lectura sea recuperable."""

    @pytest.mark.asyncio
    async def test_se_puede_volver_a_subir_el_comprobante(
        self, async_client, con_ticket
    ):
        """Un PDF que no se leyo porque el extractor estaba apagado no queda
        ilegible para siempre. Con esto, el archivo vuelve al ticket que ya
        existe y el documento queda disponible para revisarlo.
        """
        ticket = await con_ticket()
        nuevo = b"%PDF-1.7 el mismo comprobante, mejor leido"

        r = await async_client.put(
            f"/api/v1/tickets/{ticket.id}/documento",
            files={"file": ("oaxaca.pdf", nuevo, "application/pdf")},
            data={"motivo": "la foto estaba cortada y no se leia el RFC"},
        )

        assert r.status_code == 200
        # Y el gasto NO se duplica ni se toca: lo que cambia es el papel, no el
        # ticket. Reextraer es otra operacion.
        assert r.json()["id"] == str(ticket.id)
        assert r.json()["total_amount"] == "1100.00"

        despues = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")
        assert despues.content == nuevo

    @pytest.mark.asyncio
    async def test_reemplazar_no_crea_un_gasto_nuevo(
        self, async_client, con_ticket
    ):
        """La tentacion de "crear el ticket del lado del servidor".

        Seria peor: un gasto que nadie reviso, con una fecha que eligio el
        sistema, entrando por la puerta de atras de una operacion que dice
        "reemplaza el documento de este ticket".
        """
        ticket = await con_ticket()

        await async_client.put(
            f"/api/v1/tickets/{ticket.id}/documento",
            files={"file": ("otro.pdf", b"%PDF-1.7 otro", "application/pdf")},
            data={"motivo": "prueba"},
        )

        lista = await async_client.get("/api/v1/tickets/")
        assert len(lista.json()) == 1

    @pytest.mark.asyncio
    async def test_un_archivo_grande_da_413_y_no_toca_nada(
        self, async_client, con_ticket
    ):
        """El mismo tope que la subida original, y el ticket queda como estaba.

        Si este endpoint aceptara mas de lo que acepta la subida, la diferencia
        seria un endpoint sin limite aparente, y el que fallaria seria el que
        no lo parece.
        """
        ticket = await con_ticket()
        enorme = b"x" * (11 * 1024 * 1024)

        r = await async_client.put(
            f"/api/v1/tickets/{ticket.id}/documento",
            files={"file": ("enorme.pdf", enorme, "application/pdf")},
            data={"motivo": "prueba"},
        )

        assert r.status_code == 413
        # El comprobante que ya estaba sigue ahi. Un intento que falla no borra
        # lo que habia.
        despues = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")
        assert despues.content == TICKET_TEXTO

    @pytest.mark.asyncio
    async def test_el_historial_muestra_el_cambio(
        self, async_client, con_ticket
    ):
        """El cambio tiene que ser VISIBLE, no solo estar escrito en la base.

        Antes, reemplazar un comprobante lo dejaba escrito en la tabla y
        invisible para quien opera el sistema. Eso es lo mismo que un cambio
        silencioso, con la diferencia de que ahora podria consultarse... si
        alguien supiera que existe este endpoint.
        """
        ticket = await con_ticket()

        await async_client.put(
            f"/api/v1/tickets/{ticket.id}/documento",
            files={"file": ("otro.pdf", b"%PDF-1.7 otro", "application/pdf")},
            data={"motivo": "el original estaba cortado"},
        )

        r = await async_client.get(f"/api/v1/tickets/{ticket.id}/documentos")
        assert r.status_code == 200
        historial = r.json()

        assert len(historial) == 2
        assert [d["version"] for d in historial] == [1, 2]
        # Solo la ultima es vigente: la primera es la que se conserva como
        # evidencia, no la que se descarga.
        assert [d["vigente"] for d in historial] == [False, True]
        assert historial[1]["motivo"] == "el original estaba cortado"
        assert historial[1]["actor"], "el reemplazo tiene que decir quien lo hizo"

    @pytest.mark.asyncio
    async def test_reemplazar_sin_motivo_da_422(
        self, async_client, con_ticket
    ):
        """Sin motivo no hay cambio de comprobante.

        Es lo que separa "cambie el papel" de "cambie el papel y por que". Un
        endpoint que lo dejara pasar tendria una cadena de cambios sin
        explicación, que es lo mismo que el silencio que se vino a cerrar.
        """
        ticket = await con_ticket()

        r = await async_client.put(
            f"/api/v1/tickets/{ticket.id}/documento",
            files={"file": ("otro.pdf", b"%PDF-1.7 otro", "application/pdf")},
        )

        assert r.status_code == 422
        vigente = await async_client.get(f"/api/v1/tickets/{ticket.id}/documento")
        assert vigente.content == TICKET_TEXTO, "cambio el papel sin que se le pidiera motivo"

    @pytest.mark.asyncio
    async def test_reemplazar_en_un_ticket_inexistente_es_404(
        self, async_client
    ):
        r = await async_client.put(
            f"/api/v1/tickets/{uuid4()}/documento",
            files={"file": ("x.pdf", b"%PDF-1.7", "application/pdf")},
            data={"motivo": "prueba"},
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_la_api_anuncia_el_documento_para_que_la_pantalla_lo_muestre(
        self, async_client, con_ticket
    ):
        """`tiene_documento` y `documento_url` tienen que salir en la respuesta.

        Sin esto, el frontend no tiene forma de distinguir un ticket de captura
        manual (que no tiene documento y no deberia) de uno al que se le perdio
        el archivo. Y una pantalla que no puede distinguir eso no muestra el
        enlace, o lo muestra siempre: en los dos casos el revisor acaba
        revisando a ciegas, y el sistema parece funciona.
        """
        ticket = await con_ticket()

        r = await async_client.get("/api/v1/tickets/")
        fila = next(t for t in r.json() if t["id"] == str(ticket.id))

        assert fila["tiene_documento"] is True
        assert fila["documento_url"] == f"/api/v1/tickets/{ticket.id}/documento"
        assert fila["documento_tamano"] == len(TICKET_TEXTO)

    @pytest.mark.asyncio
    async def test_un_ticket_manual_no_anuncia_un_documento_que_no_existe(
        self, async_client, db_session, test_company
    ):
        """El caso contrario, que es el que hace que el campo signifique algo.

        Un booleano que dice `true` siempre no informa de nada, y una URL que
        lleva a un 404 en toda captura manual hace que quien la ve deje de
        confiar en el resto de los campos.
        """
        from app.api.tickets import create_ticket
        from app.schemas.ticket import TicketCreate

        ticket = await create_ticket(
            TicketCreate(
                company_id=test_company.id,
                provider_name="OXXO",
                total_amount=Decimal("150.00"),
                expense_date=date(2025, 3, 15),
            ),
            db=db_session,
        )

        r = await async_client.get("/api/v1/tickets/")
        fila = next(t for t in r.json() if t["id"] == str(ticket.id))

        assert fila["tiene_documento"] is False
        assert fila["documento_url"] is None
        assert fila["documento_tamano"] is None

    @pytest.mark.asyncio
    async def test_la_url_que_anuncia_la_api_es_la_que_responde(
        self, async_client, con_ticket
    ):
        """La URL no es un adorno del contrato: tiene que funcionar.

        Se construye con el prefijo de la v1 hardcodeado, que es exactamente el
        tipo de detalle que se rompe en silencio: el campo sale bien formado en
        todas las pruebas de la API, y el unico sitio donde se nota es el
        navegador de la persona revisando, con un 404.
        """
        ticket = await con_ticket()

        r = await async_client.get("/api/v1/tickets/")
        url = next(
            t["documento_url"] for t in r.json() if t["id"] == str(ticket.id)
        )

        # La misma URL, tal cual la devolvio la API.
        documento = await async_client.get(url)
        assert documento.status_code == 200
        assert documento.content == TICKET_TEXTO

    @pytest.mark.asyncio
    async def test_sin_token_no_se_sirve_el_comprobante(
        self, async_client_sin_autenticar, con_ticket
    ):
        """Un comprobante es un documento fiscal de una empresa concreta.

        Este endpoint entrega el archivo completo de un gasto. Sin el token no
        entrega nada, igual que el resto de la v1, y por la misma razon: el
        sistema es multiempresa y el token es lo que dice a que empresa se
        pertenece la peticion.
        """
        ticket = await con_ticket()

        r = await async_client_sin_autenticar.get(
            f"/api/v1/tickets/{ticket.id}/documento"
        )

        assert r.status_code == 401
