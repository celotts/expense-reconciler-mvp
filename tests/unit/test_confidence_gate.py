"""Tests del gate de confianza.

El gate es la pieza que hace medible la exactitud. Si el gate dice que un
documento esta bien, se asume bien; si dice que no, el documento tiene que
quedar visible en la cola. Cualquier permiso que se le conceda al gate se
convierte en un hueco por donde entra basura sin que nadie lo note.

Por eso los tests van en dos direcciones:
  - los checks que DEBEN rechazar (regresiones de confianza falsa)
  - los que NO deben rechazar (regresiones de falsos positivos: si el gate
    bloquea tickets validos, la cola se llena de ruido y nadie la revisa)
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.core.enums import (
    AUTO_APPROVE_CONFIDENCE, ConfidenceSource, ExtractionStatus, REVIEW_CONFIDENCE,
)
from app.services.confidence_gate import (
    MONEY_TOLERANCE, compute_source_hash, confidence_to_decimal, decide_status,
    gate_manual_ticket, gate_ticket, validate_extraction,
)

# Fecha fija y dentro de la ventana plausible del gate.
FECHA_OK = date(2025, 6, 15)


def _validar(**kwargs):
    """Atajo: los checks con defaults validos, sobreescribiendo lo que se pruebe."""
    base = {
        "provider_name": "TIENDAS RAMIREZ",
        "total_amount": Decimal("1160.00"),
        "tax_amount": Decimal("160.00"),
        "expense_date": FECHA_OK,
    }
    base.update(kwargs)
    return validate_extraction(**base)


class TestValidationBasica:
    def test_ticket_valido_pasa_todos_los_checks(self):
        out = _validar()
        assert out.ok
        assert out.failures == []
        assert "provider_present" in out.passed
        assert "total_positive" in out.passed
        assert "date_plausible" in out.passed

    def test_as_text_devuelve_none_cuando_no_hay_fallos(self):
        # Importante: la columna es nullable y se usa para filtrar "lo que
        # fallo". Si devolviera "" en vez de None, el filtro "validation_errors
        # IS NOT NULL" traeria tickets correctos.
        assert _validar().as_text() is None

    def test_as_text_serializa_los_fallos(self):
        out = _validar(total_amount=Decimal("0"), tax_amount=Decimal("0"))
        assert out.as_text() == "total_not_positive"


class TestValidacionProveedor:
    def test_proveedor_vacio_falla(self):
        assert "provider_missing" in _validar(provider_name="").failures

    def test_proveedor_solo_espacios_falla(self):
        # Una IA que devuelve "   " parece haber leído algo. No ha leído nada.
        assert "provider_missing" in _validar(provider_name="   ").failures

    def test_unknown_provider_falla(self):
        # Es la cadena de contrato con el parser: significa "no supe leer el emisor".
        assert "provider_missing" in _validar(provider_name="Unknown Provider").failures

    @pytest.mark.parametrize(
        "nombre",
        [
            "TIENDA GENERICA",
            "LA CASA DE LAS CAJAS",
            "ABARROTES EL CONSUMIDOR",
            "SUPERMERCADOS CENTRO",
            "TIENDA DONA TODO",
        ],
    )
    def test_nombres_de_negocio_reales_no_se_rechazan(self, nombre):
        # Regresion: el filtro de palabras en español puede rechazar nombres
        # legitimos. Un ticket valido bloqueado es un falso positivo caro.
        assert "provider_missing" not in _validar(provider_name=nombre).failures

    def test_nombre_se_compara_con_espacios_perimetrales(self):
        # El parser puede devolver espacios. " Unknown Provider " es igual de
        # inusable que "Unknown Provider".
        assert "provider_missing" in _validar(provider_name="  Unknown Provider  ").failures


class TestValidacionMontos:
    def test_total_cero_falla(self):
        assert "total_not_positive" in _validar(total_amount=Decimal("0")).failures

    def test_total_negativo_falla(self):
        assert "total_not_positive" in _validar(total_amount=Decimal("-50.00")).failures

    def test_total_none_falla(self):
        assert "total_not_positive" in _validar(total_amount=None).failures

    def test_iva_negativo_falla(self):
        out = _validar(tax_amount=Decimal("-1.00"))
        assert "tax_negative" in out.failures

    def test_iva_mayor_que_total_falla(self):
        # 200 de IVA sobre 100 de total es aritmeticamente imposible.
        out = _validar(total_amount=Decimal("100.00"), tax_amount=Decimal("200.00"))
        assert "tax_exceeds_total" in out.failures

    def test_iva_igual_al_total_es_valido(self):
        # Caso limite: gasto sin IVA mas IVA igual al total. No es un error.
        out = _validar(total_amount=Decimal("116.00"), tax_amount=Decimal("116.00"))
        assert "tax_exceeds_total" not in out.failures

    def test_decimal_se_compara_exacto_sin_deriva_de_float(self):
        # 0.1 + 0.2 en float da 0.30000000000000004. Con Decimal da 0.30.
        # Este test falla si alguien mete un float en el camino del monto.
        out = _validar(
            total_amount=Decimal("0.30"),
            tax_amount=Decimal("0.20"),
            subtotal=Decimal("0.10"),
        )
        assert out.ok, out.failures


class TestAritmeticaDocumento:
    def test_subtotal_mas_iva_cuadra(self):
        out = _validar(subtotal=Decimal("1000.00"))
        assert "arithmetic_consistent" in out.passed

    def test_subtotal_mas_iva_no_cuadra_falla(self):
        out = _validar(subtotal=Decimal("500.00"))
        assert any(f.startswith("subtotal_plus_tax_mismatch") for f in out.failures)

    def test_diferencia_de_un_centimo_se_tolera(self):
        # Los redondeos de IVA a dos decimales son normales, no son un error.
        out = _validar(
            total_amount=Decimal("1160.01"),
            tax_amount=Decimal("160.00"),
            subtotal=Decimal("1000.01"),
        )
        assert out.ok, out.failures

    def test_diferencia_dos_centimos_no_se_tolera(self):
        # 1000.01 + 160.00 = 1160.01, pero se leyo 1160.03. Dos centavos.
        out = _validar(
            total_amount=Decimal("1160.03"),
            tax_amount=Decimal("160.00"),
            subtotal=Decimal("1000.01"),
        )
        assert any(f.startswith("subtotal_plus_tax_mismatch") for f in out.failures)

    def test_sin_subtotal_no_se_evalua_la_aritmetica(self):
        # No todos los comprobantes traen subtotal. No inventar el dato.
        out = _validar(subtotal=None)
        assert "arithmetic_consistent" not in out.passed
        assert "arithmetic_consistent" not in out.failures
        assert out.ok

    def test_el_mensaje_de_mismatch_dice_los_dos_numeros(self):
        # Con solo un numero, revisar 500 tickets es comparar a ciegas
        # contra el documento original.
        out = _validar(subtotal=Decimal("500.00"))
        msg = next(f for f in out.failures if f.startswith("subtotal_plus_tax_mismatch"))
        assert "leido=1160.00" in msg
        assert "esperado=660.00" in msg

    def test_tolerancia_es_de_un_centimo(self):
        assert MONEY_TOLERANCE == Decimal("0.01")


class TestValidacionRFC:
    def test_rfc_valido_pasa(self):
        out = _validar(provider_tax_id="TRAM910101XXX")
        assert "rfc_valid" in out.passed
        assert out.ok

    def test_rfc_en_minusculas_se_normaliza(self):
        out = _validar(provider_tax_id="tram910101xxx")
        assert out.ok, out.failures

    def test_rfc_malformado_falla(self):
        # "WALM910101" son 10 caracteres: le falta la homoclave.
        out = _validar(provider_tax_id="WALM910101")
        assert "malformed_rfc" in out.failures

    def test_rfc_sin_homoclave_falla(self):
        out = _validar(provider_tax_id="WALM910101XXXXX")
        assert "malformed_rfc" in out.failures

    def test_sin_rfc_no_es_fallo(self):
        # Mucha gente no pide factura. La ausencia de RFC no es un error.
        out = _validar(provider_tax_id=None)
        assert "malformed_rfc" not in out.failures
        assert "rfc_valid" not in out.passed
        assert out.ok

    def test_rfc_vacio_no_es_fallo(self):
        out = _validar(provider_tax_id="")
        assert out.ok


class TestValidacionFecha:
    def test_fecha_actual_pasa(self):
        from datetime import datetime, timezone
        hoy = datetime.now(timezone.utc).date()
        assert "date_plausible" in _validar(expense_date=hoy).passed

    def test_fecha_futura_lejana_falla(self):
        from datetime import datetime, timezone
        manana = datetime.now(timezone.utc).date() + timedelta(days=30)
        out = _validar(expense_date=manana)
        assert any(f.startswith("date_in_future") for f in out.failures)

    def test_tolera_uno_o_dos_dias_de_tolerancia(self):
        # El reloj del cajero y el del servidor no coinciden nunca al segundo.
        from datetime import datetime, timezone
        manana = datetime.now(timezone.utc).date() + timedelta(days=1)
        assert "date_plausible" in _validar(expense_date=manana).passed

    def test_fecha_muy_vieja_falla(self):
        out = _validar(expense_date=date(2015, 1, 1))
        assert any(f.startswith("date_too_old") for f in out.failures)

    def test_fecha_none_falla(self):
        out = _validar(expense_date=None)
        assert "date_missing" in out.failures

    def test_el_mensaje_de_fecha_incluye_la_fecha_leida(self):
        # Sin la fecha leida, revisar el documento original es a ciegas.
        out = _validar(expense_date=date(2015, 1, 1))
        msg = next(f for f in out.failures if f.startswith("date_too_old"))
        assert "2015-01-01" in msg


class TestDecideStatus:
    """La regla de decision. Aqui esta el comportamiento de riesgo:
    que la confianza alta NO compense checks rotos."""

    def _ok_validation(self):
        return validate_extraction(
            "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), FECHA_OK,
        )

    def test_confianza_alta_auto_aprueba(self):
        d = decide_status(0.97, self._ok_validation())
        assert d.status is ExtractionStatus.AUTO_APROBADO
        assert not d.needs_human

    def test_confianza_al_limite_exacto_auto_aprueba(self):
        d = decide_status(AUTO_APPROVE_CONFIDENCE, self._ok_validation())
        assert d.status is ExtractionStatus.AUTO_APROBADO

    def test_confianza_justo_debajo_del_limite_va_a_revision(self):
        d = decide_status(AUTO_APPROVE_CONFIDENCE - 0.001, self._ok_validation())
        assert d.status is ExtractionStatus.REQUIERE_REVISION
        assert d.needs_human

    def test_confianza_media_va_a_revision(self):
        d = decide_status(REVIEW_CONFIDENCE, self._ok_validation())
        assert d.status is ExtractionStatus.REQUIERE_REVISION

    def test_confianza_baja_va_a_revision(self):
        d = decide_status(0.10, self._ok_validation())
        assert d.status is ExtractionStatus.REQUIERE_REVISION

    def test_confianza_none_no_auto_aprueba(self):
        # El parser por reglas no estima confianza. Ante la duda, revision.
        d = decide_status(None, self._ok_validation())
        assert d.status is ExtractionStatus.REQUIERE_REVISION
        assert d.needs_human

    def test_confianza_alta_no_compensa_check_roto(self):
        # ESTE es el test que define el gate. Un total en 0 con 0.99 de
        # confianza es exactamente el caso donde un modelo se equivoca
        # confiado. Auto-aprobarlo mete basura en la conciliacion.
        roto = validate_extraction(
            "TIENDAS RAMIREZ", Decimal("0"), Decimal("0"), FECHA_OK,
        )
        d = decide_status(0.99, roto)
        assert d.status is not ExtractionStatus.AUTO_APROBADO
        assert d.status is ExtractionStatus.PENDIENTE

    def test_rfc_malformado_bloquea_aunque_confianza_alta(self):
        roto = validate_extraction(
            "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), FECHA_OK,
            provider_tax_id="WALM910101",
        )
        d = decide_status(0.99, roto)
        assert d.status is ExtractionStatus.REQUIERE_REVISION

    def test_sin_proveedor_es_pendiente_no_revision(self):
        # Sin proveedor y sin total no hay ticket, hay un documento ilegible.
        # Va a la cola de pendientes, que es donde el usuario lo vera.
        roto = validate_extraction("Unknown Provider", Decimal("0"), None, FECHA_OK)
        d = decide_status(0.99, roto)
        assert d.status is ExtractionStatus.PENDIENTE

    def test_sin_fecha_es_pendiente(self):
        roto = validate_extraction(
            "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), None,
        )
        d = decide_status(0.99, roto)
        assert d.status is ExtractionStatus.PENDIENTE

    def test_cada_fallo_aparece_en_las_razones(self):
        # Las razones se persisten. Sin ellas no hay forma de agrupar el error.
        roto = validate_extraction(
            "TIENDAS RAMIREZ", Decimal("0"), Decimal("0"), FECHA_OK,
            provider_tax_id="MALA",
        )
        d = decide_status(0.9, roto)
        assert any(r == "check:total_not_positive" for r in d.reasons)
        assert any(r == "check:malformed_rfc" for r in d.reasons)

    def test_auto_aprobado_registra_que_fue_por_confianza(self):
        d = decide_status(0.97, self._ok_validation())
        assert any(r.startswith("confidence_high") for r in d.reasons)

    def test_confianza_se_conserva_en_la_decision(self):
        d = decide_status(0.97, self._ok_validation())
        assert d.confidence == pytest.approx(0.97)


class TestGateTicket:
    def test_flujo_feliz_auto_aprueba(self):
        d = gate_ticket(
            "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), FECHA_OK,
            confidence=0.96,
        )
        assert d.status is ExtractionStatus.AUTO_APROBADO

    def test_flujo_feliz_sin_confianza_va_a_revision(self):
        d = gate_ticket(
            "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), FECHA_OK,
        )
        assert d.status is ExtractionStatus.REQUIERE_REVISION

    def test_documento_ilegible_queda_pendiente(self):
        d = gate_ticket(
            "Unknown Provider", Decimal("0.00"), Decimal("0.00"), FECHA_OK,
            confidence=0.99,
        )
        assert d.status is ExtractionStatus.PENDIENTE
        assert d.needs_human

    def test_aritmetica_rota_bloquea_el_auto_aprobado(self):
        d = gate_ticket(
            "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), FECHA_OK,
            subtotal=Decimal("10.00"), confidence=0.99,
        )
        assert d.status is not ExtractionStatus.AUTO_APROBADO
        assert d.needs_human

    def test_la_fuente_se_propaga(self):
        d = gate_ticket(
            "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), FECHA_OK,
            confidence=0.96, source=ConfidenceSource.LLM_VALIDATED,
        )
        assert d.confidence_source is ConfidenceSource.LLM_VALIDATED

    def test_todo_estado_no_aprobado_exige_persona(self):
        # Invariante: nada sale de la cola sin que un humano lo toque.
        for conf in (0.0, 0.3, 0.7, 0.85, 0.89, 0.90, 0.99, 1.0):
            d = gate_ticket(
                "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), FECHA_OK,
                confidence=conf,
            )
            esperado_automatico = conf >= AUTO_APPROVE_CONFIDENCE
            assert d.needs_human != esperado_automatico, f"confianza={conf}"


class TestGateManual:
    def test_captura_manual_valida_queda_aprobada(self):
        d = gate_manual_ticket(
            "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), FECHA_OK,
        )
        assert d.status is ExtractionStatus.APROBADO
        assert not d.needs_human

    def test_captura_manual_no_declara_confianza_uma(self):
        # Poner 1.0 seria mentir: un dedo humano no se mide. La confianza
        # mide lectura automatica.
        d = gate_manual_ticket(
            "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), FECHA_OK,
        )
        assert d.confidence == 0.0
        assert d.confidence_source is ConfidenceSource.MANUAL

    def test_captura_manual_invalida_no_se_aprueba(self):
        # Escribir a mano no exime de que el total sea positivo.
        d = gate_manual_ticket(
            "TIENDAS RAMIREZ", Decimal("0"), Decimal("0"), FECHA_OK,
        )
        assert d.status is not ExtractionStatus.APROBADO
        assert d.needs_human

    def test_captura_manual_con_rfc_malformado_no_se_aprueba_solo(self):
        d = gate_manual_ticket(
            "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), FECHA_OK,
            provider_tax_id="MALA",
        )
        assert d.status is ExtractionStatus.REQUIERE_REVISION

    def test_captura_manual_marca_el_motivo(self):
        d = gate_manual_ticket(
            "TIENDAS RAMIREZ", Decimal("1160.00"), Decimal("160.00"), FECHA_OK,
        )
        assert "manual_entry" in d.reasons


class TestConfidenceToDecimal:
    def test_none_queda_none(self):
        assert confidence_to_decimal(None) is None

    def test_texto_no_numerico_queda_none(self):
        # La IA puede devolver confidence como texto. No reventar la columna.
        assert confidence_to_decimal("alta") is None

    def test_nan_queda_none(self):
        assert confidence_to_decimal(float("nan")) is None

    def test_redondea_a_tres_decimales(self):
        # La columna es Numeric(4,3). Un 0.99999 no cabe.
        assert confidence_to_decimal(0.99999) == Decimal("1.000")

    def test_acota_por_encima_de_uno(self):
        assert confidence_to_decimal(1.5) == Decimal("1.000")

    def test_acota_por_debajo_de_cero(self):
        assert confidence_to_decimal(-0.5) == Decimal("0.000")

    def test_preserva_el_decimal(self):
        # El camino de montos es Decimal end-to-end. Un float aqui
        # reintroduce el error que se intento eliminar.
        assert isinstance(confidence_to_decimal(0.9), Decimal)


class TestSourceHash:
    def test_mismo_contenido_mismo_hash(self):
        # De esto depende la idempotencia de la carga masiva.
        assert compute_source_hash(b"ticket") == compute_source_hash(b"ticket")

    def test_contenido_distinto_hash_distinto(self):
        assert compute_source_hash(b"ticket-a") != compute_source_hash(b"ticket-b")

    def test_hash_tiene_64_hex(self):
        h = compute_source_hash(b"ticket")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_un_byte_de_diferencia_cambia_el_hash(self):
        # Adjuntar la misma foto con un byte distinto debe detectarse como
        # documento distinto, no fusionarse con el anterior.
        assert compute_source_hash(b"ticket") != compute_source_hash(b"tickeT")

    def test_archivo_vacio_no_revienta(self):
        assert len(compute_source_hash(b"")) == 64


class TestExtractionStatusEnum:
    @pytest.mark.parametrize(
        "estado",
        [ExtractionStatus.REQUIERE_REVISION, ExtractionStatus.PENDIENTE],
    )
    def test_los_estados_abiertos_requieren_accion(self, estado):
        assert estado.is_open

    @pytest.mark.parametrize(
        "estado",
        [
            ExtractionStatus.AUTO_APROBADO,
            ExtractionStatus.APROBADO,
            ExtractionStatus.RECHAZADO,
        ],
    )
    def test_los_estados_cerrados_no_requieren_accion(self, estado):
        assert not estado.is_open

    def test_el_estado_serializa_a_su_valor(self):
        # El filtro de la cola compara strings contra la columna. Si el enum
        # no coincide con lo guardado, la cola sale vacia sin error visible.
        assert ExtractionStatus.PENDIENTE.value == "PENDIENTE"
        assert ExtractionStatus.REQUIERE_REVISION.value == "REQUIERE_REVISION"
