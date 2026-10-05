"""El final del escaneo: el total, y el canal que dice que esta pasando.

LOS TRES BUGS QUE ESTOS TESTS MATAN, Y POR QUE NO SE VIAN
-----------------------------------------------------------

1. **El tope del registro de corridas no se ejecutaba nunca.** Con 20 o menos
   corridas, el `while` no entra y el `NameError` del interior no aparece. Los
   tests de escaneo abren un archivo cada uno, asi que hace falta la corrida 21
   para verlo. Es el bug mas caro de los tres: uno que solo se manifiesta bajo
   carga, que es exactamente cuando el canal de progreso hace falta.

2. **El total contaba dinero dos veces.** Un DUPLICADO no crea ticket: apunta al
   del otro archivo. Con `foto.jpg` y `foto-copia.jpg` con los mismos bytes, los
   dos detalles traen el MISMO `ticket_id`, y sumarlos inventa gasto.

3. **El importe "confiable" se llevaba los tickets corregidos a mano.** Un
   `APROBADO` es una decision humana, y meterlo en el numero de lo que el
   sistema leyo por si solo hace que un dato humano parezca automatico. Es la
   confusion que el reporte de exactitud no puede tolerar.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.enums import ScanStatus
from app.services import scan_registry
from app.services.scan_service import ResumenArchivo, _calcular_resumen


class TestElTopeDelRegistro:
    """El `MAX_CORRIDAS` tiene que funcionar, no solo estar escrito."""

    def test_la_corrida_21_no_revienta(self):
        """Este es el test del typo: `velha` definido y `vieja` usado.

        Fallaba en la 21 y no antes, porque el `while` no entra hasta que hay mas
        de `MAX_CORRIDAS` corridas. Un test que abre una sola corrida lo deja
        pasar siempre.
        """
        for i in range(scan_registry.MAX_CORRIDAS + 1):
            scan_registry.abrir_corrida("/tickets", "ana", False)
        assert len(scan_registry._corridas) == scan_registry.MAX_CORRIDAS

    def test_el_tope_se_aplica_aunque_todas_sean_recientes(self):
        """Sin este, un `simular` en bucle llena la memoria de entradas viejas.

        El filtro por TTL no las alcanza porque todas son recientes: hace falta el
        tope por cantidad.
        """
        for _ in range(scan_registry.MAX_CORRIDAS * 3):
            scan_registry.abrir_corrida("/tickets", "ana", False)
        assert len(scan_registry._corridas) <= scan_registry.MAX_CORRIDAS
        assert len(scan_registry._orden) == len(scan_registry._corridas)

    def test_el_registro_no_crece_y_no_se_corrompe(self):
        """El `_orden` y `_corridas` tienen que seguir siendo del mismo tamano.

        Si uno se queda con entradas huerfanas, `listar()` las consulta y
        devuelve `None` donde deberia haber una corrida.
        """
        for i in range(scan_registry.MAX_CORRIDAS * 2):
            scan_registry.abrir_corrida("/tickets", "ana", False)
        assert len(scan_registry._orden) == len(scan_registry._corridas)
        assert all(c is not None for c in scan_registry.listar(limite=50))


class TestElCanalDeProgreso:
    def test_una_corrida_reporta_el_archivo_que_esta_leyendo(self):
        """Un contador que sube solo no dice si se trabo en uno concreto."""
        corrida = scan_registry.abrir_corrida("/tickets", "ana", False)

        scan_registry.marcar_archivo(corrida, "foto-01.jpg")
        scan_registry.marcar_archivo(corrida, "foto-02.jpg")

        assert corrida.archivos_vistos == 2
        assert corrida.actual == "foto-02.jpg"
        assert corrida.terminada is False

    def test_al_cerrar_desaparece_el_archivo_actual(self):
        """Un archivo "actual" en una corrida terminada dice que se trabo."""
        corrida = scan_registry.abrir_corrida("/tickets", "ana", False)
        scan_registry.marcar_archivo(corrida, "foto-01.jpg")

        scan_registry.cerrar_corrida(corrida, {"archivos_vistos": 1})

        assert corrida.terminada is True
        assert corrida.actual is None
        assert corrida.resumen == {"archivos_vistos": 1}

    def test_el_resumen_es_none_mientras_corre(self):
        """Un resumen a medias parece un resumen de una corrida corta."""
        corrida = scan_registry.abrir_corrida("/tickets", "ana", False)
        scan_registry.marcar_con_ticket(corrida)
        assert corrida.resumen is None

    def test_las_corridas_sin_decimal_en_la_respuesta(self):
        """`Decimal` no es JSON, y `json.dumps` revienta con el.

        Se serializa como texto, que es lo que hace el resto del proyecto.
        """
        corrida = scan_registry.abrir_corrida("/tickets", "ana", False)
        scan_registry.cerrar_corrida(
            corrida, {"importe_total_leido": Decimal("927.27")}
        )

        import json

        d = corrida.a_dict()
        assert d["resumen"]["importe_total_leido"] == "927.27"
        # Y no revienta al serializar de verdad, que es el punto.
        json.dumps(d)

    def test_las_operaciones_son_seguras_con_corrida_none(self):
        """Sin corrida, marcar no debe reventar.

        El reproceso de un archivo suelto no abre corrida propia, y las rutas de
        lectura lo llaman igual.
        """
        scan_registry.marcar_archivo(None, "x.jpg")
        scan_registry.marcar_con_ticket(None)
        scan_registry.marcar_error(None)
        scan_registry.cerrar_corrida(None, None)


class TestElTotalNoMiente:

    @staticmethod
    def _detalle(accion="SIN_CAMBIOS", ticket_id="t1", datos=None, estado="PENDIENTE"):
        return ResumenArchivo(
            relative_path="x.jpg",
            ticket_id=ticket_id,
            accion=accion,
            status=ScanStatus.PROCESADO,
            extraction_status=estado,
            datos=datos,
        )

    def test_el_mismo_ticket_no_se_suma_dos_veces(self):
        """Este es el bug del dinero duplicado.

        Un DUPLICADO no crea ticket: apunta al del otro archivo. Sin deduplicar por
        `ticket_id`, una foto y su copia cuentan $10 000 dos veces.
        """
        datos = {"total_amount": "10000.00", "extraction_status": "AUTO_APROBADO"}
        detalles = [
            ResumenArchivo(
                relative_path="foto.jpg", ticket_id="mismo", accion="CREADO",
                status=ScanStatus.PROCESADO, datos=datos,
            ),
            ResumenArchivo(
                relative_path="foto-copia.jpg", ticket_id="mismo", accion="OMITIDO",
                status=ScanStatus.DUPLICADO, datos=datos,
            ),
        ]

        r = _calcular_resumen(detalles)
        assert r["importe_total_leido"] == Decimal("10000.00")
        assert r["importe_total_confiable"] == Decimal("10000.00")
        # Y los dos archivos siguen contados como archivos vistos.
        assert r["archivos_vistos"] == 2

    def test_un_ticket_corregido_a_mano_no_cuenta_como_confiable(self):
        """`APROBADO` es una persona. Meterlo en "confiable" la disfraza de maquina."""
        detalles = [
            self._detalle(
                datos={"total_amount": "97.56", "extraction_status": "APROBADO"},
                estado="APROBADO",
            )
        ]

        r = _calcular_resumen(detalles)
        assert r["importe_total_leido"] == Decimal("97.56")
        assert r["importe_total_confiable"] == Decimal("0.00")

    def test_separa_leido_de_confiable_de_por_revisar(self):
        """Las tres columnas tienen que sumar lo que dicen, y solo eso."""
        detalles = [
            self._detalle(ticket_id="a", estado="AUTO_APROBADO",
                          datos={"total_amount": "100.00", "extraction_status": "AUTO_APROBADO"}),
            self._detalle(ticket_id="b", estado="PENDIENTE",
                          datos={"total_amount": "200.00", "extraction_status": "PENDIENTE"}),
            self._detalle(ticket_id="c", estado="REQUIERE_REVISION",
                          datos={"total_amount": "300.00", "extraction_status": "REQUIERE_REVISION"}),
        ]

        r = _calcular_resumen(detalles)
        assert r["importe_total_leido"] == Decimal("600.00")
        assert r["importe_total_confiable"] == Decimal("100.00")
        assert r["importe_requiere_revision"] == Decimal("500.00")
        assert r["requiere_revision"] == 2

    def test_sin_totales_da_none_y_no_cero(self):
        """"No se leyo nada" y "se leyo cero" son cosas distintas.

        Cero es un ticket que hay que mirar —el caso de `IMG_4222.jpeg`—, y
        reportarlo como `None` lo esconderia.
        """
        detalles = [self._detalle(datos={"extraction_status": "PENDIENTE"})]

        r = _calcular_resumen(detalles)
        assert r["importe_total_leido"] is None
        assert r["tickets_sin_total"] == 1
        assert r["tickets_con_total"] == 0

    def test_quedan_en_bandeja_y_archivados_vienen_del_escaneo(self):
        """Los dos numeros NO se deducen de los detalles.

        Se intentaron deducir —"tiene ticket y no tiene ruta de archivo"— y es
        incorrecto desde que el borrado sustituyo al movimiento: el borrado no
        deja ruta de destino, asi que `ruta_archivo` es None en TODOS los casos y
        la cuenta daba "10 de 10 en bandeja" con 2 archivos que si se retiraban.
        Medido contra la API viva, no supuesto.

        Aqui se comprueba que los valores que salen son los que se le pasaron, no
        un recalculo. Con los mismos detalles, dos entradas distintas tienen que
        dar dos salidas distintas.
        """
        from app.services.scan_service import _calcular_resumen

        detalles = [
            self._detalle(ticket_id="a", accion="SIN_CAMBIOS",
                          datos={"total_amount": "10.00", "extraction_status": "AUTO_APROBADO"}),
            self._detalle(ticket_id="b", accion="SIN_CAMBIOS",
                          datos={"total_amount": "20.00", "extraction_status": "PENDIENTE"}),
        ]

        # Uno se retira y el otro no.
        uno = _calcular_resumen(detalles, quedan_en_bandeja=1, archivados=1)
        # Ninguno se retira.
        ninguno = _calcular_resumen(detalles, quedan_en_bandeja=2, archivados=0)

        assert uno["quedan_en_bandeja"] == 1
        assert uno["archivados"] == 1
        assert uno["borrados_de_entrada"] == 1
        assert ninguno["quedan_en_bandeja"] == 2
        assert ninguno["archivados"] == 0
        # Y el dinero NO depende de eso: se lee de los detalles.
        assert uno["importe_total_leido"] == ninguno["importe_total_leido"]

    def test_los_campos_nuevos_llegan_en_la_respuesta_de_la_api(self):
        """Un campo en el servicio y no en el schema NO LLEGA. No llega en cero: no llega.

        `borrados_de_entrada` se calculaba y se devolvia en el dict, y la API lo
        peridia en silencio porque `ResumenScan` no lo declaraba: Pydantic
        descarta lo que no conoce. La respuesta traia `KeyError` en vez de `0`.

        Es el modo de falla mas incomodo que hay —no es un error, es un campo que
        desaparece— asi que el test comprueba la respuesta COMPLETA, no el
        servicio.
        """
        from app.schemas.scan import ResumenScan

        completo = {
            "archivos_vistos": 10, "leidos": 0, "nuevos": 0, "actualizados": 0,
            "sin_cambios": 10, "duplicados": 0, "con_error": 0,
            "no_soportados": 0, "omitidos_por_tope": 0,
            "importe_total_leido": None, "importe_total_confiable": None,
            "importe_requiere_revision": None, "tickets_con_total": 0,
            "tickets_sin_total": 0, "tickets_por_estado": {},
            "tickets_por_motor": {}, "tickets_con_rfc": 0, "tickets_con_lineas": 0,
            "requiere_revision": 8, "requiere_accion": 8, "sin_ticket": 0,
            "quedan_en_bandeja": 8, "archivados": 2, "borrados_de_entrada": 2,
            "colas": {"revision": "/review-queue"},
        }

        r = ResumenScan(**completo)

        assert r.borrados_de_entrada == 2
        assert r.quedan_en_bandeja == 8
        assert r.archivados == 2
        # Los tres son consistentes entre si, que es lo que el operador lee.
        assert r.borrados_de_entrada + r.quedan_en_bandeja == r.archivos_vistos

    def test_cuenta_lo_que_hay_que_mirar(self):
        """`requiere_accion` es la pregunta "¿tengo que hacer algo?"."""
        detalles = [
            self._detalle(ticket_id="a", estado="PENDIENTE",
                          datos={"total_amount": "1.00", "extraction_status": "PENDIENTE"}),
            ResumenArchivo(relative_path="malo.zip", accion="ERROR",
                           status=ScanStatus.ERROR, ticket_id=None),
            ResumenArchivo(relative_path="raro.xyz", accion="OMITIDO",
                           status=ScanStatus.NO_SOPORTADO, ticket_id=None),
        ]

        r = _calcular_resumen(detalles)
        assert r["con_error"] == 1
        assert r["no_soportados"] == 1
        assert r["sin_ticket"] == 2
        # 1 en revision + 1 error + 1 no soportado + 2 sin ticket
        assert r["requiere_accion"] == 5

    def test_cuenta_rfc_y_lineas(self):
        """Los dos campos que dicen si una lectura sirve de algo."""
        detalles = [
            self._detalle(ticket_id="a", datos={
                "extraction_status": "AUTO_APROBADO", "provider_tax_id": "OCO030116UR4",
                "items": [{"description": "X"}], "confidence_source": "llm",
            }),
            self._detalle(ticket_id="b", datos={
                "extraction_status": "PENDIENTE", "provider_tax_id": None,
                "items": None, "confidence_source": "ocr",
            }),
        ]

        r = _calcular_resumen(detalles)
        assert r["tickets_con_rfc"] == 1
        assert r["tickets_con_lineas"] == 1
        assert r["tickets_por_motor"] == {"llm": 1, "ocr": 1}