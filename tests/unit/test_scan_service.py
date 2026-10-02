"""El escaner de carpeta: las defensas y las decisiones de idempotencia.

Tres bloques, y el orden importa porque son las tres cosas que pueden salir mal
de verdad:

1. Contencion de rutas. Un escaner de carpeta es el unico modulo del proyecto
   que lee el disco, y por eso es el unico donde un `..` es una lectura
   arbitraria. Estas pruebas mueren si alguien afloja la comprobacion.

2. Idempotencia. Es la funcion del escaneo: correrlo dos veces no puede crear
   dos tickets, y un archivo que no cambio no puede costar OCR otra vez.

3. La regla que protege el trabajo humano. Un ticket que una persona ya
   corrigio a mano no se sobreescribe porque el archivo haya cambiado.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.enums import ScanStatus
from app.models.scan_file import ScanFileModel
from app.models.ticket import TicketModel
from app.services import scan_service
from app.services.scan_service import ArchivoFueraDeLaCarpeta


# ---------------------------------------------------------------------------
# Ayudas
# ---------------------------------------------------------------------------

# Un comprobante con toda la evidencia: RFC, subtotal, IVA, total y fecha. Es el
# caso que el gate auto-aprueba, y por eso sirve para probar tanto el camino
# feliz como el de la proteccion al trabajo humano.
COMPROBANTE = """Tiendas Ramirez SA de CV
RFC: TRAM910101XXX
Fecha: 2025/03/15
Subtotal: 948.28
IVA (16%): 151.72
TOTAL: 1100.00
"""


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    """Una carpeta de tickets de verdad, apuntada por `TICKETS_INPUT_DIR`.

    Se ajusta `settings.TICKETS_INPUT_DIR` y no una variable de modulo, porque
    el servicio la lee de `settings` en cada llamada. Fijar el atributo del
    modulo dejaria las pruebas dependientes del orden de ejecucion.
    """
    from app.core.config import settings

    destino = tmp_path / "tickets"
    destino.mkdir()
    monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(destino))
    return destino


@pytest.fixture
def sin_modelo(monkeypatch):
    """Apaga OCR y modelo: ninguna prueba de esta suite toca la red.

    Son dos apagones y ninguno es opcional.

    El OCR se apaga por lo de siempre: una suite que necesita un binario de
    Tesseract instalado es una suite que el que no lo tiene ve roja.

    El modelo se apaga porque `ai_extractor` es un cliente HTTP de verdad. Sin
    este apagon, una prueba que llega a vision intenta una llamada a la API y
    falla con un error de DNS en vez de con el aserto que quiere comprobar. Y es
    peor que ruido: una prueba que depende de la red es una prueba que pasa en
    casa de uno y falla en la de otro, sin que nadie haya tocado el codigo.
    """
    from app.services import capture
    from app.services.ai_extractor import ai_extractor

    def _no_disponible(datos: bytes):
        raise capture.OCRNoDisponible("OCR apagado para esta prueba")

    async def _sin_modelo(*_args, **_kwargs):
        raise RuntimeError("el modelo esta apagado en esta prueba")

    monkeypatch.setattr(capture, "_ocr_por_defecto", _no_disponible)
    monkeypatch.setattr(ai_extractor, "extract_from_image", _sin_modelo)
    monkeypatch.setattr(ai_extractor, "extract_from_text", _sin_modelo)


def _pdf_con_texto(monkeypatch, texto: str):
    """Fuerza la rama de PDF con texto, sin modelo y sin OCR."""
    from app.services import capture

    monkeypatch.setattr(capture, "extract_pdf_text", lambda _bytes: texto)
    monkeypatch.setattr(
        capture, "_vision_pdf", _no_deberia_llamarse("un PDF con texto no se renderiza")
    )


def _no_deberia_llamarse(que):
    async def _explota(*_args, **_kwargs):
        raise AssertionError(que)

    return _explota


# ---------------------------------------------------------------------------
# 1. Contencion de rutas
# ---------------------------------------------------------------------------


class TestLaRutaNoSePideSeConfigura:

    def test_una_ruta_relativa_se_reconstruye_dentro_de_la_carpeta(self, carpeta):
        (carpeta / "dentro.pdf").write_bytes(b"%PDF-1.7 x")
        fila = ScanFileModel(relative_path="dentro.pdf", content_hash="a" * 64)

        assert scan_service.ruta_de_relativo("dentro.pdf") == (carpeta / "dentro.pdf")

    @pytest.mark.parametrize(
        "malicioso",
        [
            "../../../etc/passwd",
            "..",
            "sub/../../fuera.pdf",
            "/etc/passwd",
        ],
    )
    def test_un_parent_de_directorio_no_sale_de_la_carpeta(self, carpeta, malicioso):
        """`..` en el registro no puede sacar la lectura de la carpeta.

        Este es el test que hace que el escaner no sea lectura arbitraria del
        disco. Sin el, un `relative_path` con `..` lee lo que hay arriba de la
        carpeta y lo sube como si fuera un comprobante.
        """
        with pytest.raises(ArchivoFueraDeLaCarpeta):
            scan_service.ruta_de_relativo(malicioso)

    def test_una_carpeta_hermana_no_cuenta_como_dentro(self, carpeta, tmp_path):
        """`/tickets_secreto` no esta dentro de `/tickets`.

        Este es el error clasico de comparar rutas con `startswith`, y aqui se
        ve por que importa: como cadenas, `str("/x/tickets_secreto")` empieza con
        `str("/x/tickets")`. Con `is_relative_to` no cuela.
        """
        hermana = tmp_path / "tickets_secreto"
        hermana.mkdir()
        secreto = hermana / "privado.pdf"
        secreto.write_bytes(b"%PDF-1.7 secreto")

        with pytest.raises(ArchivoFueraDeLaCarpeta):
            scan_service.ruta_de_relativo("../tickets_secreto/privado.pdf")

    def test_un_symlink_que_sale_de_la_carpeta_no_se_sigue(self, carpeta, tmp_path):
        """El comprobacion va DESPUES de resolver, no antes.

        Un symlink dentro de la carpeta que apunta a `/etc/passwd` no tiene un
        solo `..` en el texto: el `..` no existe. Lo unico que lo revela es
        `resolve()`, y por eso el orden de las dos operaciones no es
        intercambiable. Este test falla si alguien comprueba la cadena antes de
        resolver, que es el orden "natural" y el equivocado.
        """
        fuera = tmp_path / "fuera.pdf"
        fuera.write_bytes(b"%PDF-1.7 fuera de la carpeta")

        enlace = carpeta / "dentro.pdf"
        enlace.symlink_to(fuera)

        # El recorrido ni lo lista.
        assert scan_service.listar_archivos() == []

        # Y si se le pide la ruta explicitamente, se rechaza.
        with pytest.raises(ArchivoFueraDeLaCarpeta):
            scan_service.ruta_de_relativo("dentro.pdf")

    def test_la_ruta_relativa_va_en_posix(self, carpeta):
        r"""La clave del registro no depende del sistema operativo.

        `a\b` y `a/b` no son la misma cadena. Guardar la ruta con la barra del
        sistema hace que la misma carpeta tenga dos nombres distintos segun quien
        escribiera la fila, y el UNIQUE de `relative_path` deja de proteger
        porque `sub\x.jpg` y `sub/x.jpg` son dos claves.
        """
        (carpeta / "viaje").mkdir()
        (carpeta / "viaje" / "marzo.pdf").write_bytes(b"%PDF-1.7 x")

        relativas = [v.relative_path for v in scan_service.listar_archivos()]
        assert relativas == ["viaje/marzo.pdf"]
        assert "\\" not in relativas[0]


# ---------------------------------------------------------------------------
# 2. El recorrido
# ---------------------------------------------------------------------------


class TestElRecorrido:

    def test_no_baja_a_subcarpetas_cuando_esta_apagado(self, carpeta, monkeypatch):
        """`TICKETS_SCAN_RECURSIVO=false` de verdad se respeta.

        Es una opcion expuesta en `GET /scan/config`. Si el recorrido la
        ignorara, alguien la apagaria creyendo que ya no mira subcarpetas y
        seguiria mirando.
        """
        from app.core.config import settings

        (carpeta / "viaje").mkdir()
        (carpeta / "arriba.pdf").write_bytes(b"%PDF-1.7 a")
        (carpeta / "viaje" / "abajo.pdf").write_bytes(b"%PDF-1.7 b")

        monkeypatch.setattr(settings, "TICKETS_SCAN_RECURSIVO", True)
        con_sub = [v.relative_path for v in scan_service.listar_archivos()]
        assert con_sub == ["arriba.pdf", "viaje/abajo.pdf"]

        monkeypatch.setattr(settings, "TICKETS_SCAN_RECURSIVO", False)
        sin_sub = [v.relative_path for v in scan_service.listar_archivos()]
        assert sin_sub == ["arriba.pdf"]

    def test_ignora_el_ruido_del_sistema(self, carpeta):
        """`.DS_Store` y los temporales no son comprobantes fallidos.

        Se ignoran en vez de registrarse como `NO_SOPORTADO` porque el conteo de
        "no soportados" tiene que significar "hay archivos aqui que no puedo
        leer", no "Finder estuvo en esta carpeta".
        """
        (carpeta / ".DS_Store").write_bytes(b"\x00\x01\x02")
        (carpeta / "~$ticket.xlsx").write_bytes(b"temporal")
        (carpeta / "bueno.pdf").write_bytes(b"%PDF-1.7 x")

        assert [v.relative_path for v in scan_service.listar_archivos()] == ["bueno.pdf"]

    def test_se_salta_lo_que_pasa_del_tope_de_bytes(self, carpeta, monkeypatch):
        """Un video en la carpeta no se lee entero para hashearlo.

        Leer los bytes es lo que decide si el archivo cambio, asi que un video
        de 2 GB se leeria entero en memoria. Con el tope se salta, y no se
        registra: no es un comprobante que fallo, es algo que no tiene sentido
        leer.
        """
        from app.core.config import settings

        (carpeta / "video.mp4").write_bytes(b"\x00" * 5000)
        (carpeta / "ticket.pdf").write_bytes(b"%PDF-1.7 x")

        monkeypatch.setattr(settings, "TICKETS_SCAN_MAX_BYTES", 1000)
        assert [v.relative_path for v in scan_service.listar_archivos()] == ["ticket.pdf"]

    def test_el_orden_es_estable_entre_corridas(self, carpeta):
        """Dos recorridos de la misma carpeta dan la misma lista y en el mismo orden.

        El orden importa por el tope por corrida. Si dos corridas sobre la misma
        carpeta tuvieran distinto orden, el tope cortaria en archivos distintos y
        los ultimos de la lista no se verian nunca.
        """
        for nombre in ("c.pdf", "a.pdf", "b.pdf"):
            (carpeta / nombre).write_bytes(b"%PDF-1.7 x")

        primera = [v.relative_path for v in scan_service.listar_archivos()]
        segunda = [v.relative_path for v in scan_service.listar_archivos()]
        assert primera == segunda == ["a.pdf", "b.pdf", "c.pdf"]


# ---------------------------------------------------------------------------
# 3. Idempotencia: la funcion del escaneo
# ---------------------------------------------------------------------------


class TestEscanearEsIdempotente:

    async def test_un_archivo_nuevo_crea_un_ticket(self, db_session, test_company, carpeta, sin_modelo, monkeypatch):
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "ocho.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        assert resumen.nuevos == 1
        assert resumen.leidos == 1

        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert len(tickets) == 1
        assert tickets[0].provider_name == "Tiendas Ramirez SA de CV"
        assert tickets[0].total_amount == Decimal("1100.00")
        assert tickets[0].source_type == "directory"

    async def test_escanear_dos_veces_no_crea_dos_tickets(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        """La segunda pasada no vuelve a leer ni vuelve a crear.

        Este es el motivo de que exista `scan_files`. Con solo
        `tickets.source_hash` se sabe que el CONTENIDO esta repetido, pero no que
        este ARCHIVO ya se miro; sin el registro, cada corrida paga OCR de nuevo
        sobre los mismos 400 tickets y el operador ve el escaneo taking minutes
        sin que nada nuevo ocurra.
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "ocho.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        primera = await scan_service.escanear(db_session, test_company.id, "ana")
        segunda = await scan_service.escanear(db_session, test_company.id, "ana")

        assert primera.nuevos == 1
        assert segunda.nuevos == 0
        assert segunda.sin_cambios == 1

        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert len(tickets) == 1, "dos corridas no pueden crear dos tickets"

    async def test_inventariar_y_despues_atribuir_crea_el_ticket(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        """Un archivo inventariado despues SI se puede atribuir a una empresa.

        El caso se vio escaneando la carpeta de verdad: el primer `POST /scan`
        sin `company_id` dejo los 3 archivos registrados con su hash, y el
        siguiente con empresa los reporto "sin cambios" y no creo ningun
        ticket. Esos archivos ya no se atribuian nunca por la via normal y
        habia que reprocesarlos uno por uno a mano.

        La causa: el atajo de "sin cambios" miraba solo el hash, y el hash ya
        estaba escrito por el escaneo de inventario. Pero `PROCESADO` quiere
        decir "se leyo", NO "esta contabilizado": un archivo leido en el
        inventario esta `PROCESADO` y sin ticket, y son esos dos hechos juntos
        los que hay que mirar.
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "ocho.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        # 1. Inventario: sin empresa, no crea tickets pero registra el archivo.
        inventario = await scan_service.escanear(db_session, None, "ana")
        assert inventario.archivos_vistos == 1
        assert inventario.nuevos == 0
        tickets_despues_del_inventario = (
            await db_session.execute(select(TicketModel))
        ).scalars().all()
        assert tickets_despues_del_inventario == []

        fila = (await db_session.execute(select(ScanFileModel))).scalar_one()
        assert fila.ticket_id is None, "el inventario no debe crear ticket"
        assert fila.content_hash, "pero si guarda el hash, para no releerlo"

        # 2. Con empresa: el mismo archivo, sin cambios de bytes, debe crear el
        #    ticket. Es el paso que se rompia.
        con_empresa = await scan_service.escanear(db_session, test_company.id, "ana")

        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert len(tickets) == 1, (
            "un archivo inventariado se debe poder atribuir despues a una empresa"
        )
        assert con_empresa.nuevos == 1
        assert tickets[0].total_amount == Decimal("1100.00")

    async def test_despues_de_atribuir_ya_si_es_idempotente(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        """Y una vez atribuido, volver a pasar no crea un segundo ticket.

        Es la otra mitad de la prueba de arriba: si el arreglo hiciera que
        CUALQUIER pasada releyera, el escaneo volveria a gastar OCR en cada
        corrida y habria perdido lo que resolvia.
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "ocho.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        await scan_service.escanear(db_session, None, "ana")
        await scan_service.escanear(db_session, test_company.id, "ana")
        tercera = await scan_service.escanear(db_session, test_company.id, "ana")

        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert len(tickets) == 1
        assert tercera.sin_cambios == 1, "ya tiene ticket: ya no hay nada que hacer"
        assert tercera.nuevos == 0

    async def test_la_segunda_pasada_no_incrementa_los_intentos(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        """`attempts` cuenta intentos de LECTURA, no visitas.

        Un archivo que ya se leyo y no cambio no se intento leer: se comparo su
        hash. Si `attempts` sumara en cada pasada, un archivo sano llegaria a
        400 intentos en una semana y "muchos intentos" dejaria de significar
        "muchas veces fallo".
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "ocho.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        await scan_service.escanear(db_session, test_company.id, "ana")
        await scan_service.escanear(db_session, test_company.id, "ana")

        fila = (await db_session.execute(select(ScanFileModel))).scalar_one()
        assert fila.attempts == 1, "la segunda pasada no es un intento de lectura"

    async def test_mismos_bytes_en_dos_rutas_es_un_duplicado(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        """Una copia del mismo comprobante no es un gasto nuevo.

        Es el caso de "el mismo ticket fotografiado dos veces", o de una copia
        de seguridad en la carpeta. El indice unico de `tickets.source_hash`
        impide el segundo ticket, pero el ARCHIVO tiene que quedar registrado
        como `DUPLICADO` y apuntando al original: si se reportara como "CREADO",
        el operador veria dos archivos nuevos para un solo ticket.

        Que archivo sea el "original" lo decide el orden de recorrido, que es
        alfabetico, y no el nombre del archivo. Aqui se comprueba el invariante
        y no cual de los dos gano, porque un escaner no puede saber que
        `original.pdf` es mas original que `copia.pdf` solo por como se llama.
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        contenido = b"%PDF-1.7 " + COMPROBANTE.encode()
        (carpeta / "original.pdf").write_bytes(contenido)
        (carpeta / "copia.pdf").write_bytes(contenido)

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert len(tickets) == 1, "los bytes iguales no pueden ser dos tickets"
        assert resumen.duplicados == 1
        assert resumen.nuevos == 1

        filas = {
            f.relative_path: f
            for f in (await db_session.execute(select(ScanFileModel))).scalars().all()
        }
        estados = {f.status for f in filas.values()}
        assert estados == {ScanStatus.PROCESADO.value, ScanStatus.DUPLICADO.value}

        # Ambos apuntan al mismo ticket: la pregunta "este ticket salio de que
        # archivo?" tiene que poder contestarse con los dos.
        assert filas["original.pdf"].ticket_id == filas["copia.pdf"].ticket_id
        assert filas["original.pdf"].ticket_id is not None


# ---------------------------------------------------------------------------
# 4. La regla que protege el trabajo humano
# ---------------------------------------------------------------------------


class TestNoSePisaLaCorreccionDeUnaPersona:

    async def test_un_ticket_revisado_no_se_sobreescribe(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        """Si alguien corrigio el ticket a mano, reprocesar no lo deshace.

        El operador corrigio el proveedor porque la lectura estaba mal. Volver a
        correr OCR sobre el mismo archivo va a volver a leerlo mal y a borrar la
        correccion, en silencio: nadie se entera hasta que el cierre no cuadra.

        Por eso el escaneo automatico nunca sobreescribe un ticket con
        `reviewed_at`, ni siquiera con `reprocesar=True`. El unico camino que puede hacerlo es el endpoint de reproceso, que es
        explicito.
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "ocho.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        await scan_service.escanear(db_session, test_company.id, "ana")
        ticket = (await db_session.execute(select(TicketModel))).scalar_one()

        # Alguien lo revisa y corrige el proveedor a mano.
        from app.core.time import utcnow

        ticket.provider_name = "OXXO SA de CV"
        ticket.reviewed_at = utcnow()
        await db_session.commit()

        # El archivo cambia y se pide reprocesar.
        _pdf_con_texto(monkeypatch, COMPROBANTE.replace("Tiendas Ramirez", "LEAL"))
        (carpeta / "ocho.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.replace("Tiendas Ramirez", "LEAL").encode())

        await scan_service.escanear(db_session, test_company.id, "ana", reprocesar=True)

        await db_session.refresh(ticket)
        assert ticket.provider_name == "OXXO SA de CV", (
            "la correccion humana no puede sobrescribirse con una relectura automatica"
        )

    async def test_avisa_que_hay_un_ticket_desactualizado(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        """Cuando no se sobreescribe, se dice. No se hace en silencio.

        El archivo cambio y el ticket ya no corresponde al papel. El estado
        `PROCESADO` no alcanza a expresar eso, asi que se deja el aviso en
        `last_error` y el endpoint de estadisticas cuenta los "desactualizados".
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "ocho.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())
        await scan_service.escanear(db_session, test_company.id, "ana")

        ticket = (await db_session.execute(select(TicketModel))).scalar_one()
        from app.core.time import utcnow

        ticket.reviewed_at = utcnow()
        await db_session.commit()

        nuevo = COMPROBANTE.replace("Tiendas Ramirez", "LEAL")
        _pdf_con_texto(monkeypatch, nuevo)
        (carpeta / "ocho.pdf").write_bytes(b"%PDF-1.7 " + nuevo.encode())

        await scan_service.escanear(db_session, test_company.id, "ana", reprocesar=True)

        fila = (await db_session.execute(select(ScanFileModel))).scalar_one()
        assert fila.status == ScanStatus.PROCESADO.value
        assert fila.last_error is not None
        assert "revisado por una persona" in fila.last_error


# ---------------------------------------------------------------------------
# 5. Un archivo roto no tumba la corrida
# ---------------------------------------------------------------------------


class TestUnArchivoMaloNoTumbaLaCorrida:

    async def test_un_binario_no_soportado_no_es_un_error(
        self, db_session, test_company, carpeta, sin_modelo
    ):
        """Un `.zip` en la carpeta se marca, no se cuenta como lectura fallida.

        La diferencia importa para el operador: "no soportado" significa "hay
        aqui algo que no es un comprobante" y "error" significa "no supe leer un
        comprobante". Lo primero se resuelve borrando el archivo; lo segundo se
        resuelve instalando Tesseract.
        """
        (carpeta / "respaldo.zip").write_bytes(b"PK\x03\x04\x00\x00\x00\x00\x00")

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        assert resumen.no_soportados == 1
        assert resumen.con_error == 0

    async def test_una_foto_ilegible_no_detiene_a_los_demas(
        self, db_session, test_company, carpeta, sin_modelo, monkeypatch
    ):
        """Un archivo que no se puede leer no deja sin procesar a los que si.

        Si un TIFF corrupto parara la corrida, los otros archivos de la carpeta
        se quedarian sin procesar porque en la carpeta habia uno malo, y el
        operador no tendria forma de saber cual era: el error estaria en la cara
        de la peticion, no en la fila del archivo.

        Y el ilegible no es un ERROR: es un ticket en la cola. Esa es la
        diferencia de contrato entre los dos fallos, y es deliberada. Un archivo
        que NI SE PUEDE ABRIR es `ERROR` (no hay nada que revisar). Un archivo
        que se abre pero no se entiende es un papel que alguien tiene que
        mirar, y perderlo es el unico resultado inaceptable.
        """
        from app.services import capture

        (carpeta / "roto.pdf").write_bytes(b"%PDF-1.7 roto a proposito")
        (carpeta / "bueno.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        def _explota(_bytes: bytes):
            raise capture.ExtractionUnavailable("no se pudo abrir")

        # El PDF "roto" no se puede ni parsear ni renderizar: cae a la cola.
        # El "bueno" se lee con reglas.
        llamadas = {"n": 0}

        def _a_veces_roto(bytes_pdf: bytes) -> str:
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                raise ValueError("pdfplumber no abre este")
            return COMPROBANTE

        monkeypatch.setattr(capture, "extract_pdf_text", _a_veces_roto)
        monkeypatch.setattr(
            capture, "render_pdf_pages",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no renderizable")),
        )

        resumen = await scan_service.escanear(db_session, test_company.id, "ana")

        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert len(tickets) == 2, (
            "los dos archivos produjeron un ticket: el ilegible va a la cola, "
            "no se pierde"
        )
        # Y el que se leyo bien esta completo.
        bueno = [t for t in tickets if t.provider_name != "Unknown Provider"]
        assert len(bueno) == 1
        assert bueno[0].total_amount == Decimal("1100.00")
        assert resumen.leidos == 2


# ---------------------------------------------------------------------------
# 6. Sin empresa no hay ticket
# ---------------------------------------------------------------------------


class TestEscanearSinEmpresa:

    async def test_inventaria_sin_crear_tickets(
        self, db_session, carpeta, sin_modelo, monkeypatch
    ):
        """Correr el escaneo sin `company_id` lista los archivos y no crea nada.

        Es el primer paso que conviene: ver que hay en la carpeta antes de
        atribuir los gastos a una empresa. El archivo queda `PROCESADO` con un
        aviso, porque SI se leyo bien: lo que falta es una decision, no una
        lectura.
        """
        _pdf_con_texto(monkeypatch, COMPROBANTE)
        (carpeta / "ocho.pdf").write_bytes(b"%PDF-1.7 " + COMPROBANTE.encode())

        resumen = await scan_service.escanear(db_session, None, "ana")

        tickets = (await db_session.execute(select(TicketModel))).scalars().all()
        assert tickets == []

        fila = (await db_session.execute(select(ScanFileModel))).scalar_one()
        assert fila.status == ScanStatus.PROCESADO.value
        assert fila.ticket_id is None
        assert "sin empresa" in fila.last_error
