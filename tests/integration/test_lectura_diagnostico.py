"""El diagnostico de lectura: que se lee, por que, y que haria el gate.

POR QUE ESTE ARCHIVO
===================

Porque hasta ahora no habia forma de *ver* como se leyo un comprobante. Se sabia que
un ticket caia a `REQUIERE_REVISION`, y `POST /tickets/extract` decia que no se pudo
leer el proveedor — pero no si fue porque el OCR no vio texto, porque el parser no
encontro el folio, o porque el extractor de vision estaba apagado. Esas tres piden
arreglos distintos y eran indistinguibles.

El diagnostico es lo que las distingue, y **sin escribir nada**: repetir el mismo
archivo muchas veces tiene que dejar la base igual, o el endpoint no serviria para
probar.

LO QUE ESTA FIJADO AQUI
=======================

  1. **No guarda.** Ni ticket, ni documento, ni compra, ni movimiento. Y no basta con
     que la respuesta lo diga: se comprueba que la base quedo como estaba.
  2. **Dice POR QUE, no "que no pudo".** Un paso que no dio resultado tiene motivo, y
     el motivo distingue "miro y no encontro" de "no pudo mirar".
  3. **El veredicto es el del gate de verdad.** Se corre `gate_ticket` con la lectura,
     no una copia de sus reglas. Un diagnostico que dijera "pasaria" con checks
     distintos a los del gate seria el fallo de "el sistema afirma mas de lo que
     sostiene", en el sitio donde mas dano hace.
  4. **Contraste con lo guardado.** Es la respuesta a "esta bien leido y el problema es
     otro": si releer daria el mismo estado, el problema no es el lector.
  5. **El `file_type` del cliente no elige la ruta.** Los bytes mandan, y la correccion
     se reporta. Regla 3 de `AGENTS.md`, comprobada desde la API.

LO QUE NO SE COMPRUEBA AQUI
===========================

El OCR de verdad (Tesseract) y el modelo de vision. Los tests inyectan extractores y un
OCR falso, como el resto del repo: una suite que dependiera del binario instalado seria
roja en la maquina de quien no lo tiene, y aprenderia a saltarsela. El camino real se
prueba con `GET /scan/ocr`, que reporta si el motor esta disponible.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.enums import ConfidenceSource, ExtractionStatus, ScanStatus
from app.models.scan_file import ScanFileModel
from app.models.ticket import TicketModel

# --- Un comprobante de texto que el parser SI entiende ----------------------
#
# Formato: `etiqueta: valor` en lineas separadas, que es lo que los regex del parser
# tienen anclado. Si este texto no lo lee el parser, el test no mide el diagnostico sino
# el parser, y seria un fallo dificil de interpretar.
TICKET_BONITO = """TIENDAS RAMIREZ SA DE CV
RFC: TRAM910101XXX
FOLIO: 000123
FECHA: 2026-09-15
SUBTOTAL: 1000.00
IVA: 160.00
TOTAL: 1160.00
"""


def _pdf_falso() -> bytes:
    """Un PDF con cabecera valida y sin capa de texto.

    No es un PDF que pdfplumber pueda abrir: la idea es exactamente esa, que caiga en
    la rama de "escaneado" y el diagnostico tenga que decirlo. Los bytes tienen la
    firma `%PDF-` porque `sniff_tipo` la mira.
    """
    return b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


def _pdf_con_texto() -> bytes:
    """Un PDF de verdad, con una capa de texto, generado con pymupdf si esta."""
    try:
        import fitz
    except ImportError:  # pragma: no cover - depende del entorno
        pytest.skip("pymupdf no esta instalado")

    doc = fitz.open()
    pagina = doc.new_page()
    pagina.insert_text((72, 100), TICKET_BONITO, fontsize=9)
    datos = doc.tobytes()
    doc.close()
    return datos


# ---------------------------------------------------------------------------
# 1. No guarda nada
# ---------------------------------------------------------------------------


class TestNoGuardaNada:
    async def test_una_lectura_de_prueba_no_deja_ticket(
        self, async_client, db_session, test_company
    ):
        """LA DEFENSA DEL ENDPOINT. Sin esto, "probar" llena la base de duplicados.

        `POST /tickets/extract-and-create` si persiste, y por eso no sirve para ver
        como lee el sistema: repetir el mismo archivo 20 veces deja 20 tickets.
        """
        antes = len((await db_session.execute(_todos_los_tickets())).all())

        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("ticket.txt", TICKET_BONITO.encode(), "text/plain")},
            data={"file_type": "text"},
        )
        assert r.status_code == 200, r.text

        despues = len((await db_session.execute(_todos_los_tickets())).all())
        assert despues == antes, (
            "el diagnostico creo un ticket. Es un endpoint de simulacion: si escribe, "
            "probar el mismo comprobante llena la base de duplicados y el "
            "diagnostico deja de ser usable."
        )

    async def test_la_respuesta_lo_declara(self, async_client):
        """`guardado: false` esta siempre, para que el que consume el JSON no suponga.

        Un endpoint que "a veces guarda" es un endpoint del que nadie sabe que hace.
        """
        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("ticket.txt", TICKET_BONITO.encode(), "text/plain")},
            data={"file_type": "text"},
        )
        assert r.json()["guardado"] is False

    async def test_un_ticket_existente_no_se_toca(self, async_client, db_session, test_company):
        """Contrastear con un ticket guardado no lo modifica.

        Es lo que hace el parametro `ticket_id`: comparar, no reescribir. Si esto
        escribiera, el diagnostico seria un `PATCH` disfrazado y peor: escribiría
        encima de correcciones humanas.
        """
        ticket = await _ticket_guardado(db_session, test_company)

        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("ticket.txt", TICKET_BONITO.encode(), "text/plain")},
            data={"file_type": "text", "ticket_id": str(ticket.id)},
        )
        assert r.status_code == 200, r.text

        await db_session.refresh(ticket)
        assert ticket.reviewed_at is None
        assert ticket.extraction_status == ExtractionStatus.AUTO_APROBADO.value


# ---------------------------------------------------------------------------
# 2. Dice por que, no "que no pudo"
# ---------------------------------------------------------------------------


class TestDicePorQue:
    async def test_un_pdf_sin_texto_dice_que_es_escaneado(self, async_client):
        """El caso que mas se confunde: "no se pudo leer" en un PDF que SI tiene info.

        pdfplumber abre el PDF y devuelve una cadena vacia o casi vacia. Sin el piso de
        caracteres, un regex sobre cadena vacia devuelve ceros y el ticket sale con
        total 0.00 — un ticket falso. El diagnostico tiene que decir "es un escaneado",
        que es una conclusion accionable, y no "ilegible".
        """
        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("escaneado.pdf", _pdf_falso(), "application/pdf")},
            data={"file_type": "pdf"},
        )
        assert r.status_code == 200, r.text
        cuerpo = r.json()

        paso_pdf = next(p for p in cuerpo["pasos"] if p["escalon"] == "pdf_texto")
        assert paso_pdf["aceptado"] is False
        # El motivo puede ser cualquiera de los dos caminos de "esto no es un PDF de
        # texto": que no se pudiera abrir (pdfplumber fallo) o que abriera pero
        # devolviera muy poco. Los dos son escaneados para quien lo lee, y el mensaje
        # tiene que permitir distinguir "no se pudo abrir" de "no habia texto", porque
        # el primero es un archivo corrupto y el segundo una foto.
        assert (
            "no se pudo abrir" in paso_pdf["motivo"]
            or "piso" in paso_pdf["motivo"]
            or "escaneado" in paso_pdf["motivo"]
        ), f"motivo poco util: {paso_pdf['motivo']!r}"

    async def test_el_motivo_de_los_pasos_inyectados_tambien_se_prueba(self, db_session, test_company):
        """El OCR con un motor de verdad, inyectado. Sin el binario de Tesseract.

        La rama del OCR es la que mas se confunde y la que este endpoint vino a
        distinguir, y no se puede probar contra el OCR real: Tesseract es un binario
        del sistema, y una suite que lo necesita se pone roja en la maquina de quien
        no lo tiene. Por eso `diagnosticar_lectura` recibe `ocr_reader`, igual que
        `capture_ticket`.
        """
        from app.services.lectura_diagnostico import diagnosticar_lectura

        # El OCR lee, pero el texto no dice nada util: es el caso "miro y no
        # encontrou", que es distinto de "no pudo mirar".
        corto = await diagnosticar_lectura(
            db_session,
            _png_minimo(),
            declarado="image",
            ocr_reader=lambda datos: _lectura("poco"),
        )
        # `corto.pasos` son dataclasses del SERVICIO, no el schema de la API: por
        # atributo, y no con corchetes. El schema se prueba en las clases de arriba,
        # por HTTP; aqui se prueba la rama con un OCR inyectado, que por HTTP habria
        # que simular.
        paso = next(p for p in corto.pasos if p.escalon == "ocr")
        assert paso.aceptado is False
        assert "piso" in paso.motivo, (
            f"un OCR que leyo poco tiene que decir que leyo poco, no fallar en "
            f"silencio. Hubo: {paso.motivo!r}"
        )

        # El OCR leio suficiente pero el parser no encontro proveedor: el motivo tiene
        # que ser el del parser, no el del piso de caracteres.
        sin_etiquetas = await diagnosticar_lectura(
            db_session,
            _png_minimo(),
            declarado="image",
            ocr_reader=lambda datos: _lectura("basura " * 40),
        )
        paso = next(p for p in sin_etiquetas.pasos if p.escalon == "ocr")
        assert paso.aceptado is False
        assert "piso" not in paso.motivo
        assert "proveedor" in paso.motivo.lower()

    async def test_ocr_no_disponible_dice_eso_y_no_que_no_vio_texto(
        self, db_session, test_company
    ):
        """LA DISTINCION QUE MAS SE CONFUNDE.

        "el OCR no esta instalado" y "el OCR no vio texto" producen el mismo resultado
        final —se va a vision— y piden **arreglos opuestos**: instalar Tesseract, o
        mejorar la foto. Confundirlos hace que alguien reinstale un binario que ya
        estaba, o que remplie fotos buenas creyendo que el problema es el motor.
        """
        from app.services.ocr import OCRNoDisponible
        from app.services.lectura_diagnostico import diagnosticar_lectura

        def _no_disponible(datos: bytes):
            raise OCRNoDisponible("tesseract-ocr no esta instalado")

        resultado = await diagnosticar_lectura(
            db_session,
            _png_minimo(),
            declarado="image",
            ocr_reader=_no_disponible,
        )
        paso = next(p for p in resultado.pasos if p.escalon == "ocr")
        assert paso.aceptado is False
        assert "no esta disponible" in paso.motivo
        assert "piso" not in paso.motivo, (
            "un OCR ausente no leyo poco: no leyo nada porque no existe. Decir "
            "'leyo menos del piso' manda a buscar una foto mejor cuando lo que falta "
            "es el motor."
        )

    async def test_un_texto_ilegible_dice_que_no_hay_proveedor(self, async_client):
        """Sin proveedor ni total, el motivo tiene que ser concreto, no generico.

        "No se pudo leer el comprobante" obliga a mirar el papel. "No se encontro un
        proveedor" dice que el parser recibio texto y no encontro la etiqueta.
        """
        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("vacio.txt", b"basura sin etiquetas\n" * 20, "text/plain")},
            data={"file_type": "text"},
        )
        cuerpo = r.json()

        paso_reglas = next(p for p in cuerpo["pasos"] if p["escalon"] == "reglas")
        assert paso_reglas["aceptado"] is False
        assert "proveedor" in paso_reglas["motivo"].lower()

    async def test_cada_paso_que_no_sirvio_dice_por_que(self, async_client):
        """LA REGLA. Un paso sin motivo es el mismo "no se pudo leer" que vino a arreglar.

        Se comprueba en los dos caminos que se pueden dar en un test sin Tesseract: PDF
        sin capa de texto y texto que no es un comprobante.
        """
        for contenido, tipo, nombre in (
            (_pdf_falso(), "pdf", "escaneado.pdf"),
            (b"basura sin etiquetas\n" * 20, "text", "basura.txt"),
        ):
            r = await async_client.post(
                "/api/v1/tickets/extract-diagnostico",
                files={"file": (nombre, contenido, "application/octet-stream")},
                data={"file_type": tipo},
            )
            cuerpo = r.json()
            no_aceptados = [p for p in cuerpo["pasos"] if not p["aceptado"]]
            assert no_aceptados, f"se esperaba al menos un paso fallido en {nombre}"
            for paso in no_aceptados:
                assert paso["motivo"], (
                    f"el paso '{paso['escalon']}' de {nombre} no fue aceptado y no "
                    "dice por que. Un motivo vacio es indistinguible de que el "
                    "diagnostico no se esta ejecutando."
                )


# ---------------------------------------------------------------------------
# 3. El veredicto es el del gate
# ---------------------------------------------------------------------------


class TestElVeredictoEsElDelGate:
    async def test_un_comprobante_bien_leido_daria_auto_aprobado(self, async_client):
        """El estado que el ticket HABRIA quedado, con los checks de verdad.

        `confidence_source` es `rules` y la confianza sale del conteo de evidencia:
        RFC + subtotal + fecha. Si el gate real no lo aprueba, el diagnostico esta
        mintiendo sobre lo que hace el sistema.
        """
        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("ticket.txt", TICKET_BONITO.encode(), "text/plain")},
            data={"file_type": "text"},
        )
        cuerpo = r.json()

        assert cuerpo["datos"] is not None
        assert cuerpo["datos"]["proveedor"] == "TIENDAS RAMIREZ SA DE CV"
        assert Decimal(cuerpo["datos"]["total"]) == Decimal("1160.00")

        veredicto = cuerpo["veredicto"]
        assert veredicto["status"] == ExtractionStatus.AUTO_APROBADO.value
        assert veredicto["confidence_source"] == ConfidenceSource.RULES.value
        assert veredicto["checks_fallidos"] == []

    async def test_una_aritmetica_imposible_no_pasa_aunque_la_confianza_alta(
        self, async_client
    ):
        """LA DEFENSA DEL GATE, vista desde el diagnostico.

        La regla 1 de `AGENTS.md`: "la confianza alta nunca compensa un check roto". Un
        total que no cuadra con subtotal + IVA tiene que salir con
        `subtotal_plus_tax_mismatch` aunque la lectura venga perfecta.
        """
        texto = TICKET_BONITO.replace("TOTAL: 1160.00", "TOTAL: 9999.00")
        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("ticket.txt", texto.encode(), "text/plain")},
            data={"file_type": "text"},
        )
        veredicto = r.json()["veredicto"]

        # El check llega con los numeros pegados —`subtotal_plus_tax_mismatch
        # (leido=9999.00,esperado=1160.00)`— y por eso se compara por prefijo y no
        # con `in` sobre la cadena entera. Los numeros son lo que hace accionable el
        # diagnostico: dicen que el problema es de aritmetica del papel, no del lector.
        fallidos = veredicto["checks_fallidos"]
        assert any(
            f.startswith("subtotal_plus_tax_mismatch") for f in fallidos
        ), f"esperaba el check de aritmetica y hubo: {fallidos}"
        assert any("9999.00" in f and "1160.00" in f for f in fallidos), (
            "el motivo del check tiene que traer los dos numeros, no solo el nombre: "
            f"sin ellos no se puede saber si el total o el subtotal estan mal. Hubo: "
            f"{fallidos}"
        )

        # Y el estado NO puede ser AUTO_APROBADO: es el punto de la regla.
        assert veredicto["status"] != ExtractionStatus.AUTO_APROBADO.value

    async def test_sin_fecha_el_veredicto_lo_dice(self, async_client):
        """Un comprobante sin fecha cae a PENDIENTE, y el motivo esta a la vista.

        `date_missing` es de los checks bloqueantes: sin fecha el ticket no puede
        existir todavia. Un diagnostico que solo dijera "REQUIERE_REVISION" obligaria
        a mirar la respuesta a secas para saber cual de los tres checks fallo.
        """
        texto = "\n".join(
            linea for linea in TICKET_BONITO.splitlines() if "FECHA" not in linea
        )
        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("ticket.txt", texto.encode(), "text/plain")},
            data={"file_type": "text"},
        )
        veredicto = r.json()["veredicto"]

        assert "date_missing" in veredicto["checks_fallidos"]
        assert veredicto["status"] == ExtractionStatus.PENDIENTE.value


# ---------------------------------------------------------------------------
# 4. El contraste con lo guardado
# ---------------------------------------------------------------------------


class TestElContraste:
    async def test_releer_el_mismo_papel_daria_el_mismo_estado(
        self, async_client, db_session, test_company
    ):
        """LA RESPUESTA A "esta bien leido y el problema es otro".

        Si la lectura de ahora produce el mismo estado que el guardado, entonces
        releer no arregla nada: el problema esta en el gate o en los datos, no en el
        lector. Sin este campo, quien esta depurando un ticket malo se queda probando
        el OCR otra vez.
        """
        ticket = await _ticket_guardado(db_session, test_company)

        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("ticket.txt", TICKET_BONITO.encode(), "text/plain")},
            data={"file_type": "text", "ticket_id": str(ticket.id)},
        )
        cuerpo = r.json()

        assert cuerpo["ticket_id"] == str(ticket.id)
        assert cuerpo["ticket_status_actual"] == ExtractionStatus.AUTO_APROBADO.value
        assert cuerpo["veredicto"]["status"] == ExtractionStatus.AUTO_APROBADO.value
        assert cuerpo["coincide_con_guardado"] is True

    async def test_un_ticket_que_cae_a_revision_dice_que_releer_no_ayuda(
        self, async_client, db_session, test_company
    ):
        """El caso interesante: la lectura es buena, pero el ticket esta en la cola.

        Aqui `coincide_con_guardado` es `False` y por eso hay que mirar los
        `checks_fallidos` del veredicto: el problema no es el lector.
        """
        ticket = await _ticket_guardado(db_session, test_company)
        ticket.extraction_status = ExtractionStatus.REQUIERE_REVISION.value
        await db_session.commit()

        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("ticket.txt", TICKET_BONITO.encode(), "text/plain")},
            data={"file_type": "text", "ticket_id": str(ticket.id)},
        )
        cuerpo = r.json()

        assert cuerpo["ticket_status_actual"] == ExtractionStatus.REQUIERE_REVISION.value
        assert cuerpo["veredicto"]["status"] == ExtractionStatus.AUTO_APROBADO.value
        assert cuerpo["coincide_con_guardado"] is False


# ---------------------------------------------------------------------------
# 5. El file_type del cliente no elige la ruta
# ---------------------------------------------------------------------------


class TestElFileTypeNoDecide:
    async def test_un_pdf_declarado_como_imagen_se_reporta_como_corregido(
        self, async_client
    ):
        """LA REGLA 3 DE AGENTS.MD, desde la API.

        Con `file_type=image`, un PDF entra por vision en vez de por la capa de texto, y
        sale con otra confianza —que es el origen con el que agrupa el reporte de
        exactitud—. El sniffing gana, y el motivo de la correccion sale en la
        respuesta para que quien lo llama sepa por que su etiqueta no se respeto.
        """
        pdf = _pdf_con_texto()

        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("ticket.pdf", pdf, "application/pdf")},
            data={"file_type": "image"},
        )
        assert r.status_code == 200, r.text
        cuerpo = r.json()

        assert cuerpo["formato_detectado"] == "pdf"
        assert cuerpo["formato_declarado"] == "image"
        assert cuerpo["formato_corregido"], (
            "los bytes son PDF y el cliente declaro image. La correccion tiene que "
            "decirlo: sin ella, quien llama se lleva un resultado de otra ruta de "
            "lectura sin saber que lo pidio mal."
        )

    async def test_una_imagen_declarada_como_pdf_tambien_se_corrige(self, async_client):
        """El otro sentido. Manda el sniffing, no la etiqueta."""
        # Un PNG minimo: la firma son los primeros 8 bytes.
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        )
        r = await async_client.post(
            "/api/v1/tickets/extract-diagnostico",
            files={"file": ("foto.png", png, "image/png")},
            data={"file_type": "pdf"},
        )
        cuerpo = r.json()
        assert cuerpo["formato_detectado"] == "image"
        assert cuerpo["formato_corregido"]


# ---------------------------------------------------------------------------
# 6. El archivo que ya escaneó el sistema
# ---------------------------------------------------------------------------


class TestDiagnosticoDeUnArchivoEscaneado:
    """El camino que se usa desde Insomnia: sin elegir archivo, sin subirlo."""

    async def test_diagnostica_un_archivo_registrado(
        self, async_client, db_session, test_company, tmp_path, monkeypatch
    ):
        """Un archivo del registro se relee sin tocar la base.

        Es el camino que hace util el endpoint desde Insomnia: `{{ file_id }}` ya esta
        autocompletado por `GET /scan/files`, y no hay que ir al disco a buscar nada.
        """
        from app.core.config import settings

        (tmp_path / "ticket.txt").write_bytes(TICKET_BONITO.encode())
        monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(tmp_path))

        fila = ScanFileModel(
            relative_path="ticket.txt",
            content_hash="a" * 64,
            status=ScanStatus.PROCESADO.value,
            detected_format="text",
            company_id=test_company.id,
        )
        db_session.add(fila)
        await db_session.commit()
        await db_session.refresh(fila)

        r = await async_client.post(f"/api/v1/scan/files/{fila.id}/diagnostico")
        assert r.status_code == 200, r.text
        cuerpo = r.json()

        assert cuerpo["datos"]["proveedor"] == "TIENDAS RAMIREZ SA DE CV"
        assert cuerpo["veredicto"]["status"] == ExtractionStatus.AUTO_APROBADO.value
        assert cuerpo["guardado"] is False

    async def test_no_cambia_el_registro_del_archivo(
        self, async_client, db_session, test_company, tmp_path, monkeypatch
    ):
        """LA DEFENSA. Diagnosticar no es reprocesar.

        `POST /files/{id}/reprocess` si cambia el estado del archivo y cuenta un
        intento. Este no. Confundirlos seria hacer que "ver por que fallo" contara como
        "volver a intentarlo", y un diagnostico que cambia lo que diagnostica es peor
        que no tenerlo.
        """
        from app.core.config import settings

        (tmp_path / "ticket.txt").write_bytes(TICKET_BONITO.encode())
        monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(tmp_path))

        fila = ScanFileModel(
            relative_path="ticket.txt",
            content_hash="b" * 64,
            status=ScanStatus.ERROR.value,
            attempts=3,
            last_error="no se pudo leer antes",
            company_id=test_company.id,
        )
        db_session.add(fila)
        await db_session.commit()
        await db_session.refresh(fila)

        await async_client.post(f"/api/v1/scan/files/{fila.id}/diagnostico")

        await db_session.refresh(fila)
        assert fila.attempts == 3
        assert fila.status == ScanStatus.ERROR.value
        assert fila.last_error == "no se pudo leer antes"

    async def test_un_archivo_archivado_da_409_y_dice_la_otra_via(
        self, async_client, db_session, test_company, tmp_path, monkeypatch
    ):
        """El caso que va a pasar siempre: el comprobante ya esta en `Tickets_Scan`.

        Con `TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR` en `true` —la omision— `POST /scan`
        mueve cada comprobante al digitalizarlo. El 409 tiene que decir donde esta y
        que hay otro camino, porque si no la conclusion que se saca es "el archivo se
        perdio".
        """
        from app.core.config import settings

        (tmp_path / "vacio").mkdir()
        monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(tmp_path))

        fila = ScanFileModel(
            relative_path="archivado.pdf",
            content_hash="c" * 64,
            status=ScanStatus.PROCESADO.value,
            company_id=test_company.id,
        )
        db_session.add(fila)
        await db_session.commit()
        await db_session.refresh(fila)

        r = await async_client.post(f"/api/v1/scan/files/{fila.id}/diagnostico")
        assert r.status_code == 409
        assert "extract-diagnostico" in r.json()["detail"]

    async def test_un_id_que_no_existe_da_404(self, async_client):
        """404 y no una lista vacia: "no hay nada" y "no existe" piden acciones distintas."""
        r = await async_client.post(f"/api/v1/scan/files/{uuid4()}/diagnostico")
        assert r.status_code == 404

    async def test_una_ruta_fuera_de_la_carpeta_no_se_lee(
        self, async_client, db_session, test_company, tmp_path, monkeypatch
    ):
        """LA DEFENSA DE CONTENCION.

        `relative_path` con `..` sale del registro (un `psql` lo puede escribir, y el
        UNIQUE no lo impide), y leerlo seria lectura arbitraria del disco. La
        comprobacion va DESPUES de resolver, con `is_relative_to`: un symlink a `/etc`
        no tiene un solo `..` en el texto.
        """
        from app.core.config import settings

        (tmp_path / "entrantes").mkdir()
        secreto = tmp_path / "secreto.txt"
        secreto.write_bytes(b"esto no me deberias leer")
        monkeypatch.setattr(settings, "TICKETS_INPUT_DIR", str(tmp_path / "entrantes"))

        fila = ScanFileModel(
            relative_path="../secreto.txt",
            content_hash="d" * 64,
            status=ScanStatus.PROCESADO.value,
            company_id=test_company.id,
        )
        db_session.add(fila)
        await db_session.commit()
        await db_session.refresh(fila)

        r = await async_client.post(f"/api/v1/scan/files/{fila.id}/diagnostico")
        assert r.status_code == 409
        assert "fuera de la carpeta" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Los helpers
# ---------------------------------------------------------------------------


def _png_minimo() -> bytes:
    """Un PNG de 1x1. La firma son los primeros 8 bytes y `sniff_tipo` la mira."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    )


def _lectura(texto: str):
    """Un `ResultadoOCR` con el texto que se le pida, sin tocar Tesseract.

    `confianza_media` y no `confianza`: ese es el nombre del campo del dataclass. No es
    decorativo —si el nombre no existe, el constructor lanza y el paso del OCR reporta
    "el OCR fallo", que es un diagnostico que miente sobre la maquina.
    """
    from app.services.ocr import ResultadoOCR

    return ResultadoOCR(texto=texto, motor="tesseract", confianza_media=90.0)


def _todos_los_tickets():
    from sqlalchemy import select

    from app.models.ticket import TicketModel as T

    return select(T)


async def _ticket_guardado(db_session, empresa) -> TicketModel:
    """Un ticket real en la base, para contrastar contra el."""
    ticket = TicketModel(
        company_id=empresa.id,
        provider_name="TIENDAS RAMIREZ SA DE CV",
        provider_tax_id="TRAM910101XXX",
        total_amount=Decimal("1160.00"),
        tax_amount=Decimal("160.00"),
        subtotal=Decimal("1000.00"),
        expense_date=date(2026, 9, 15),
        raw_text="texto del comprobante",
        extraction_status=ExtractionStatus.AUTO_APROBADO.value,
        confidence_source=ConfidenceSource.RULES.value,
        confidence=Decimal("0.950"),
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket
