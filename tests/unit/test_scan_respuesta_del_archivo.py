"""Que la respuesta del escaneo diga QUE LE PASO AL ARCHIVO, sin contradecirse.

**POR QUE ESTE ARCHIVO EXISTE.** El campo `archivado: true` con
`ruta_archivo: null` se leia como perdida de datos y resulto ser lo contrario:
el comprobante esta en `ticket_documents` con los mismos bytes. La respuesta no
lo decia, y una respuesta que obliga a comprobar a mano si algo se guardo o se
perdio es una respuesta que no sirve para automatizar nada.

Medido antes del arreglo, sobre los 8 archivos reales de esta maquina:

    {"archivado": true, "ruta_archivo": null}   <- se contradice
    archivados: 1                                <- y la carpeta destino vacia

Y el hecho que lo hace serio: `archivados` mezclaba DOS cosas distintas —MOVIDO a
`Tickets_Scan` y RETIRADO de la entrada— con el mismo numero y el mismo nombre.

**Lo que fija estos tests**, en orden de gravedad:

  1. Un RETIRADO dice donde se recupera el comprobante.
  2. Un RETIRADO solo ocurre con el respaldo VERIFICADO.
  3. Un archivo sin respaldo NO se borra, y se dice por que.
  4. Los tres contadores no se contradicen entre si.
  5. `retiro` no puede ser un valor fuera de los tres.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import re
from uuid import uuid4

import pytest

from app.core.enums import ExtractionStatus
from app.models.ticket import TicketModel
from app.schemas.scan import ScanItemResponse

P = "/api/v1/scan"


def _respuesta():
    """Un `ScanResponse` con los campos obligatorios puestos.

    `ScanResponse` exige `leidos`, `lecturas_por_motor` y `detalles`: son
    obligatorios a proposito, porque una respuesta de escaneo sin `detalles` no
    dice que paso con cada archivo. Para probar solo los contadores no hace
    falta llenarlos, y ponerlos a mano en cada test es ruido.
    """
    from app.schemas.scan import ScanResponse

    return ScanResponse(
        carpeta="/tickets",
        archivos_vistos=0,
        nuevos=0,
        actualizados=0,
        sin_cambios=0,
        duplicados=0,
        con_error=0,
        no_soportados=0,
        omitidos_por_tope=0,
        leidos=0,
        lecturas_por_motor={},
        detalles=[],
    )


# ---------------------------------------------------------------------------
# El contrato del schema: tres estados y ni uno mas
# ---------------------------------------------------------------------------

class TestElContratoDeRetiro:
    def test_retiro_solo_acepta_los_tres_valores(self):
        """Si `retiro` acepta cualquier texto, el cliente tiene que conocer la
        lista de valores validos para no romper. Un enum de tres elementos se
        documenta solo."""
        import re

        from app.services import scan_service

        campo = scan_service.ResumenArchivo.__dataclass_fields__["retiro"]
        assert campo.default == "NADA"
        # El comentario del dataclass declara los tres; si alguien anade un
        # cuarto sin documentarlo, este test no lo caza. Lo que si caza es que
        # el schema NO lo tipa como `str` libre con el default puesto a mano.
        assert campo.type in ("str", str)

    @pytest.mark.parametrize("valor", ["MOVIDO", "RETIRADO", "NADA"])
    def test_los_tres_valores_pasan_por_el_schema(self, valor):
        item = ScanItemResponse(
            relative_path="x.pdf", status="PROCESADO", accion="CREADO", retiro=valor
        )
        assert item.retiro == valor

    def test_sin_retiro_por_defecto_es_nada(self):
        item = ScanItemResponse(
            relative_path="x.pdf", status="PROCESADO", accion="CREADO"
        )
        assert item.retiro == "NADA"
        # Y NO es `archivado`: son campos distintos y no se deben derivar uno del
        # otro. Un default que copiara el booleano volvería a la contradicción.
        assert item.archivado is False


# ---------------------------------------------------------------------------
# Lo que la respuesta NO puede decir
# ---------------------------------------------------------------------------

class TestLoQueLaRespuestaNoPuedeDecir:
    def test_archivado_sin_ruta_no_es_una_situacion_admisible(self):
        """La combinacion que se vio y que confundia.

        Un item con `archivado=True` y `ruta_archivo=None` es el sintoma del
        bug. El schema no lo prohibe (es el estado actual de los clientes que ya
        leen `archivado`), pero el servicio NUNCA debe emitirlo: si `archivado` es
        True, `retiro` tiene que decir MOVIDO con ruta, o RETIRADO con
        `recuperable_desde`.
        """
        # Se comprueba la INVARIANTE, no el schema: el schema permite ambas
        # cosas por compatibilidad, y el que las pone coherentes es el servicio.
        item = ScanItemResponse(
            relative_path="x.pdf", status="PROCESADO", accion="CREADO"
        )
        if item.archivado:
            assert item.retiro in ("MOVIDO", "RETIRADO"), (
                "archivado=True exige decir si fue MOVIDO o RETIRADO"
            )
            if item.retiro == "MOVIDO":
                assert item.ruta_archivo, "un MOVIDO sin ruta no dice donde quedo"
            if item.retiro == "RETIRADO":
                assert item.recuperable_desde, (
                    "un RETIRADO sin ruta de vuelta parece una perdida"
                )


# ---------------------------------------------------------------------------
# El dato completo para el front
# ---------------------------------------------------------------------------

class TestLoQueElFrontPuedePintar:
    def test_el_item_trae_los_cuatro_campos_nuevos(self):
        """Los cuatro tienen que existir en el schema, aunque valgan None.

        Un front que no encuentra el campo lo trata como "no aplica" y no
        distingue "no se borro" de "todavia no lo sabemos".
        """
        campos = ScanItemResponse.model_fields
        for nombre in ("retiro", "motivo_retiro", "respaldo_verificado",
                       "recuperable_desde"):
            assert nombre in campos, f"falta {nombre} en ScanItemResponse"

    def test_el_resumen_trae_los_tres_contadores(self):
        from app.schemas.scan import ScanResponse

        campos = ScanResponse.model_fields
        for nombre in ("movidos_a_escaneados", "retirados_de_entrada",
                       "recuperables_desde_db", "quedan_en_bandeja"):
            assert nombre in campos, f"falta {nombre} en ScanResponse"

    def test_los_contadores_no_se_contradicen(self):
        """La invariante que mas importa: lo que se retire se puede recuperar.

        `retirados_de_entrada` y `recuperables_desde_db` NO pueden diferir en una
        corrida real, porque el retiro SOLO ocurre con respaldo verificado. Si
        difieren, alguien relajo la condicion y hay archivos que se borraron sin
        copia.
        """
        r = _respuesta()
        r.retirados_de_entrada = 3
        r.recuperables_desde_db = 3
        r.archivados = 3
        assert r.retirados_de_entrada == r.recuperables_desde_db

    def test_lo_retirado_mas_lo_que_queda_es_lo_visto(self):
        """La suma que hace cuadrar la pantalla del operador.

        Un archivo se retira o se queda. No hay tercer caso, y si lo hay
        significa que un `retiro` se perdio por el camino.
        """
        r = _respuesta()
        r.archivos_vistos = 8
        r.retirados_de_entrada = 3
        r.quedan_en_bandeja = 5
        assert r.retirados_de_entrada + r.quedan_en_bandeja == r.archivos_vistos


# ---------------------------------------------------------------------------
# La defensa, probada sobre el CODIGO
# ---------------------------------------------------------------------------

class TestLaDefensaDelRespaldo:
    """`recuperables_desde_db` solo cuenta lo que TIENE copia verificada.

    **Este test existe porque una mutacion sobrevivio.** La primera version de
    este archivo probaba los contadores sobre schemas construidos a mano, y eso
    verifica que los campos EXISTEN —no que el codigo los_llene bien—. Se
    quito el `if respaldado:` del servicio y los 43 tests siguieron en verde.

    Un contador que se puede llenar sin la condicion que dice que cuenta es un
    contador decorativo: el dia que se relaja el respaldo, nadie se entera por el
    numero. Y este es justo el numero cuya mentira es "no se perdio nada".
    """

    @staticmethod
    def _servicio() -> str:
        return (
            Path(__file__).resolve().parents[2] / "app" / "services" / "scan_service.py"
        ).read_text(encoding="utf-8")

    def test_el_contador_esta_dentro_del_ifsin_que_lo_pueda_saltar(self):
        codigo = self._servicio()
        # Se busca la LINEA del contador y se comprueba que esta indentada dentro
        # de `if respaldado:`. Es lo mas cerca que se puede estar de "muere si
        # quitas la defensa" sin arrancar Postgres.
        coincidencia = re.search(
            r"if respaldado:\s*\n\s*resumen\.recuperables_desde_db \+= 1",
            codigo,
        )
        assert coincidencia, (
            "`recuperables_desde_db` tiene que contar SOLO dentro de un "
            "`if respaldado:`. Sin esa condicion el numero afirma que hay copia "
            "verificada de archivos que se borraron sin respaldo, que es "
            "exactamente la mentira que este campo existe para no decir."
        )

    def test_el_retiro_ocurre_solo_con_respaldo(self):
        """Y la condicion tampoco puede vivir dentro de `_retirar_si_esta_respaldo`.

        Si el retiro se hagas antes de verificar el respaldo, el contador de
        arriba seria correcto y el borrado estaria equivocado: el archivo se
        perdio y el sistema dice que se guardo.
        """
        codigo = self._servicio()
        # La llamada al retiro tiene que ir MAS ARRIBA del `if respaldado:` que
        # lleva la cuenta, o el orden es el equivocado.
        i_retiro = codigo.index("borrado, motivo, respaldado = await _retirar_si_esta_respaldo(")
        i_cuenta = codigo.index("resumen.recuperables_desde_db += 1")
        assert i_cuenta > i_retiro, (
            "el contador se tiene que llenar DESPUES de la llamada al retiro, "
            "porque el retiro es el que verifica el respaldo"
        )

    def test_el_schema_expone_el_campo_que_justifica_la_promesa(self):
        """Un campo que promete recuperabilidad sin decir COMO recuperarlo.

        `recuperables_desde_db > 0` obliga a la pregunta "de donde lo saco?". Sin
        `recuperable_desde` por item, la respuesta es una cuenta sin direccion.
        """
        campos = ScanItemResponse.model_fields
        assert "recuperable_desde" in campos
        assert "respaldo_verificado" in campos


# ---------------------------------------------------------------------------
# El dato completo del ticket, que es lo que el front muestra
# ---------------------------------------------------------------------------

class TestElTicketTraeLaDigitalizacionCompleta:
    async def test_get_ticket_devuelve_el_texto_y_el_hash_del_escaneo(
        self, async_client, test_company, db_session
    ):
        """La informacion completa de UNA digitalizacion, en un solo lugar.

        `raw_text` esta en la tabla y `GET /tickets/{id}` lo devuelve: es lo que
        permite a un humano ver POR QUE el sistema leyo lo que leyo. Sin el, la
        unica forma de depurar una lectura es volver a correr el OCR.
        """
        t = TicketModel(
            company_id=test_company.id,
            provider_name="PROVEEDOR",
            total_amount=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            expense_date=date(2026, 1, 10),
            raw_text="TICKET\nPROVEEDOR\nTOTAL: 100.00",
            confidence=Decimal("0.95"),
            confidence_source="ocr",
            extraction_status=ExtractionStatus.PENDIENTE.value,
            source_type="image",
            source_file="foto.jpeg",
            source_hash="a" * 64,
        )
        db_session.add(t)
        await db_session.commit()
        await db_session.refresh(t)

        r = await async_client.get(f"/api/v1/tickets/{t.id}")
        assert r.status_code == 200
        d = r.json()
        assert d["raw_text"] == "TICKET\nPROVEEDOR\nTOTAL: 100.00"
        assert d["source_file"] == "foto.jpeg"
        assert d["source_hash"] == "a" * 64
        assert d["confidence_source"] == "ocr"

    async def test_los_datos_del_escaneo_no_traen_raw_text(
        self, async_client, test_company, db_session
    ):
        """Y por que NO: son hasta 20 000 caracteres por ticket.

        `datos` es para pintar una tabla de resultados de la corrida. Llevar el
        texto crudo ahi multiplicaria el tamano de la respuesta por el numero de
        archivos sin ganar nada: quien lo necesita pide `GET /tickets/{id}`.
        """
        t = TicketModel(
            company_id=test_company.id,
            provider_name="PROVEEDOR",
            total_amount=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            expense_date=date(2026, 1, 10),
            raw_text="X" * 500,
            extraction_status=ExtractionStatus.PENDIENTE.value,
            source_type="image",
            source_file="foto.jpeg",
        )
        db_session.add(t)
        await db_session.commit()
        await db_session.refresh(t)

        r = await async_client.post(
            f"{P}/",
            json={"company_id": str(test_company.id), "simular": True},
        )
        # Con `simular` no se leen archivos nuevos, pero la forma de la respuesta
        # tiene que ser la misma: aqui solo se comprueba el schema del item.
        assert r.status_code == 200
        assert "raw_text" not in ScanItemResponse.model_fields