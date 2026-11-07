"""Control de calidad de la foto y validaciones sin modelo.

Dos cosas que se prueban sin base de datos ni modelo, porque son funciones
puras y es ahi donde tienen que estar cubiertas:

1. `calidad_foto.revisar_calidad_foto`: el chequeo previo que marca una foto
   claramente mala (borrosa, oscura, con reflejo, muy baja resolucion, sin
   texto) ANTES de gastar OCR/Vision. Las imagenes son sinteticas pero con
   las propiedades que importan: una buena tiene bordes nitidos y papel
   claro; una borrosa es la misma con desenfoque; una oscura es casi negra;
   una con reflejo es casi toda blanca saturada.

2. `capture._discrepancias_de_lectura`: total que no cuadra con subtotal +
   IVA + IEPS, RFC sin forma, fecha futura, total no positivo. Sin modelo,
   sin base: solo lo que la propia lectura contradice.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from PIL import Image, ImageDraw, ImageFilter

from app.services.calidad_foto import revisar_calidad_foto
from app.services.capture import _discrepancias_de_lectura
from app.services.parser_service import TicketExtractionResult


# ---------------------------------------------------------------------------
# Imagenes sinteticas
# ---------------------------------------------------------------------------


def _foto_buena() -> Image.Image:
    """Papel claro con lineas oscuras nitidas: un ticket legible."""
    img = Image.new("L", (1000, 1400), 245)
    dibujo = ImageDraw.Draw(img)
    y = 60
    for _ in range(30):
        dibujo.rectangle([60, y, 940, y + 14], fill=20)
        y += 42
    return img


# ---------------------------------------------------------------------------
# 1. Calidad de la foto
# ---------------------------------------------------------------------------


class TestCalidadDeFoto:

    def test_una_foto_buena_pasa(self):
        assert revisar_calidad_foto(_foto_buena()) is None

    def test_sin_imagen_no_hay_veredicto(self):
        # Sin evidencia no se bloquea nada: la foto sigue a OCR como antes.
        assert revisar_calidad_foto(None) is None

    def test_una_foto_borrosa_se_marca(self):
        revision = revisar_calidad_foto(
            _foto_buena().filter(ImageFilter.GaussianBlur(8))
        )
        assert revision is not None
        assert "borrosa" in revision["motivos"]
        assert "borrosa" in revision["mensaje"]

    def test_una_foto_oscura_se_marca(self):
        img = Image.new("L", (1000, 1400), 20)
        dibujo = ImageDraw.Draw(img)
        y = 60
        for _ in range(30):
            dibujo.rectangle([60, y, 940, y + 14], fill=5)
            y += 42
        revision = revisar_calidad_foto(img)
        assert revision is not None
        assert "oscura" in revision["motivos"]
        assert revision["mensaje"].startswith("foto oscura")

    def test_una_foto_con_reflejo_se_marca(self):
        img = Image.new("L", (1000, 1400), 255)
        dibujo = ImageDraw.Draw(img)
        y = 60
        for _ in range(10):
            dibujo.rectangle([60, y, 940, y + 14], fill=20)
            y += 42
        revision = revisar_calidad_foto(img)
        assert revision is not None
        assert "reflejo" in revision["motivos"]
        assert "reflejo" in revision["mensaje"]

    def test_una_foto_muy_chica_se_marca(self):
        revision = revisar_calidad_foto(_foto_buena().resize((200, 280)))
        assert revision is not None
        assert "baja_resolucion" in revision["motivos"]

    def test_una_foto_sin_texto_se_marca(self):
        revision = revisar_calidad_foto(Image.new("L", (1000, 1400), 250))
        assert revision is not None
        assert "sin_texto" in revision["motivos"]

    def test_la_revision_trae_los_numeros(self):
        revision = revisar_calidad_foto(
            _foto_buena().filter(ImageFilter.GaussianBlur(8))
        )
        assert revision is not None
        assert revision["metricas"]["varianza_laplaciano"] < 40.0
        assert revision["metricas"]["ancho_px"] == 1000


# ---------------------------------------------------------------------------
# 2. Discrepancias de la lectura
# ---------------------------------------------------------------------------


def _lectura(**cambios) -> TicketExtractionResult:
    base = {
        "provider_name": "Tiendas Ramirez SA de CV",
        "provider_tax_id": "TRAM910101XXX",
        "total_amount": Decimal("1100.00"),
        "tax_amount": Decimal("151.72"),
        "subtotal": Decimal("948.28"),
        "expense_date": date(2025, 3, 15),
        "raw_text": "Tiendas Ramirez SA de CV TOTAL: 1100.00",
    }
    base.update(cambios)
    return TicketExtractionResult(**base)


class TestDiscrepanciasDeLectura:

    def test_una_lectura_que_cuadra_no_tiene_discrepancias(self):
        assert _discrepancias_de_lectura(_lectura()) == []

    def test_el_total_que_no_cuadra_con_el_desglose(self):
        # El caso medido IMG_4409: subtotal bien leido, total mal leido.
        assert _discrepancias_de_lectura(
            _lectura(subtotal=Decimal("948.00"))
        ) == ["total_no_cuadra"]

    def test_el_ieps_tambien_suma(self):
        # Subtotal + IVA + IEPS == total: sin discrepancia.
        lectura = _lectura(
            subtotal=Decimal("940.14"),
            ieps_amount=Decimal("8.14"),
        )
        assert _discrepancias_de_lectura(lectura) == []
        # Y sin el IEPS en la suma, el mismo desglose no cuadraria.
        assert _discrepancias_de_lectura(
            _lectura(subtotal=Decimal("940.14"), tax_amount=Decimal("151.72"))
        ) == ["total_no_cuadra"]

    def test_sin_desglose_no_hay_nada_que_comprobar(self):
        # Un campo ausente no es una discrepancia: es un dato que no se leyo.
        assert _discrepancias_de_lectura(_lectura(subtotal=None)) == []

    def test_un_rfc_sin_forma_es_discrepancia(self):
        # Una fecha leida como si fuera un RFC (caso real con vision).
        assert _discrepancias_de_lectura(
            _lectura(provider_tax_id="1905-01-01")
        ) == ["rfc_sin_formato"]

    def test_sin_rfc_no_es_discrepancia(self):
        # Que falte el RFC es normal en un ticket: no se marca.
        assert _discrepancias_de_lectura(_lectura(provider_tax_id=None)) == []

    def test_una_fecha_futura_es_discrepancia(self):
        assert _discrepancias_de_lectura(
            _lectura(expense_date=date.today() + timedelta(days=1))
        ) == ["fecha_futura"]

    def test_sin_fecha_no_es_discrepancia(self):
        assert _discrepancias_de_lectura(_lectura(expense_date=None)) == []

    def test_un_total_no_positivo_es_discrepancia(self):
        assert _discrepancias_de_lectura(
            _lectura(total_amount=Decimal("0.00"))
        ) == ["total_no_positivo"]
