"""La lectura que GANA no es la primera: gana la que cuadra consigo misma.

MEDIDO el 2026-10-13 sobre los 3 HEIC rotulados de esta maquina. La vision
acierta 3 de 3 y el OCR 1 de 3, y `POST /tickets/extract` devolvio la del OCR:
un total de `219.93` donde el papel decia `279.93`.

El sintoma visible no es "el numero esta mal" —el gate ya manda eso a revision,
`REQUIERE_REVISION`, no `AUTO_APROBADO`—. El sintoma es que **el operador recibe
el dato equivocado** y su trabajo de revisar se pierde. Por eso el criterio va
en la ELECCION del lector y no en el veredicto.

Y por que la aritmetica y no "que gane la vision": una defensa tiene que morir
si se quita, y para eso tiene que ser una PROPIEDAD del documento. Que gane la
vision es una constante aprendida de tres archivos; que el total cuadre con
subtotal+IVA+IEPS es una verdad del papel, y aplica a cualquier ticket.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.capture import (
    _aritmetica_se_sostiene,
    _mejor_de,
    TicketExtractionResult,
)


def _mk(**kw):
    base = {
        "provider_name": "ALGUN COMERCIO",
        "total_amount": Decimal("100.00"),
        "expense_date": date(2026, 1, 10),
        "raw_text": "",
    }
    base.update(kw)
    return TicketExtractionResult(**base)


class TestLaAritmeticaComoCriterio:
    """Tres valores, y por que no son dos. Ver el docstring de la funcion."""

    def test_una_lectura_cuyo_total_cuadra_suma_uno(self):
        r = _mk(total_amount=Decimal("279.93"), subtotal=Decimal("279.93"),
                tax_amount=Decimal("0.00"))
        assert _aritmetica_se_sostiene(r) == 1

    def test_una_lectura_sin_desglose_es_neutra_Y_NO_MALO(self):
        """El defecto de la primera version, medido el 2026-10-13.

        Con un bool ("cuadra o no"), esta lectura valia 0 y una que si cuadraba
        valia 1. Eso hacia que la AUSENCIA de subtotal contara como error, y
        rompio `test_por_omision_una_foto_sin_lineas_si_va_al_modelo`: el OCR
        traia un desglose que suma exacto y el modelo no traia ninguno, asi que
        el criterio empujaba al OCR y se saltaban las LINEAS DE PRODUCTO — que es
        lo unico que el modelo aporta y lo que el inventario necesita.

        Aqui tiene que ser 0: no comprueba nada, pero tampoco contradice nada.
        """
        r = _mk(total_amount=Decimal("219.93"), subtotal=None, tax_amount=Decimal("0.00"))
        assert _aritmetica_se_sostiene(r) == 0

    def test_una_lectura_cuyo_desglose_no_suma_es_menos_uno(self):
        """Este es el -1, y es lo unico que el criterio puede afirmar en negativo.

        Trae los campos NECESARIOS para comprobar y no cierran: eso es un
        defecto de la lectura, no una falta de informacion.
        """
        r = _mk(total_amount=Decimal("279.93"), subtotal=Decimal("217.27"),
                tax_amount=Decimal("8.14"))
        assert _aritmetica_se_sostiene(r) == -1

    def test_un_desglose_repartido_para_que_cuadre_TAMBIEN_pasa(self):
        """LO QUE ESTE CRITERIO **NO** PUEDE VER, y hay que dejarlo escrito.

        MEDIDO el 2026-10-13: el modelo devolvio `subtotal 241.41` e
        `IVA 38.52` para un total de `279.93`. Y `241.41 + 38.52 = 279.93`
        EXACTO. Es un reparto, no una lectura: el otro comprobante del mismo
        comercio que SI esta rotulado (`IMG_4253 2.HEIC`) trae IVA INCLUIDO,
        o sea subtotal == total == 279.93 con `tax_amount 0.00`.

        La primera version de este test afirmaba `-1` y FALLO. El fallo era el
        hallazgo: un desglose inventado que cuadra es indistinguible de uno
        impreso, y por eso este criterio NO puede ser la unica defensa. Lo que
        frena el dato inventado es que el ticket no se auto-aprueba: queda
        `REQUIERE_REVISION`, con una persona mirando el papel.
        """
        r = _mk(total_amount=Decimal("279.93"), subtotal=Decimal("241.41"),
                tax_amount=Decimal("38.52"))
        assert _aritmetica_se_sostiene(r) == 1

    def test_con_ieps_tambien_cuadra(self):
        """217.27 + 8.14 + 8.59 = 234.00, el supermercado de `AGENTS.md`.

        Sin el IEPS este criterio marcaria como inconsistente una lectura
        CORRECTA, que es el defecto opuesto: un check que rechaza lo bien leido
        deja de significar "esta mal leido".
        """
        r = _mk(total_amount=Decimal("234.00"), subtotal=Decimal("217.27"),
                tax_amount=Decimal("8.14"), ieps_amount=Decimal("8.59"))
        assert _aritmetica_se_sostiene(r) == 1

    def test_un_total_cero_es_neutro(self):
        assert _aritmetica_se_sostiene(_mk(total_amount=Decimal("0.00"))) == 0


class TestLaComparacionEntreLectores:
    """El fallo real, con los dos numeros exactos que devolvio el endpoint.

    ## POR QUE ESTOS CASOS NO SON IGUALES A LOS DE ARRIBA

    MEDIDO el 2026-10-13: la primera version de esta clase pasava aunque se
    eliminara el criterio de la aritmetica. Y no por casualidad — la vision ya
    ganaba por un criterio que YA EXISTIA, `subtotal is not None`: la lectura
    del OCR llega sin subtotal y la de vision con subtotal, asi que el desempate
    lo resolvia la presencia del campo, no la coherencia del total.

    Eso significa que un test con esos dos numeros NO DEFIENDE el criterio nuevo:
    pasa por otra razon, y un test que pasa por otra razon no puede morir cuando
    se quita lo que dice proteger. Para que el criterio sea el UNICO que separa,
    los dos casos de abajo tienen que empatar en todo lo demas —mismo subtotal
    presente, mismos items, mismo proveedor, mismo RFC— y diferir SOLO en si
    sus numeros cuadran.
    """

    def _lectura(self, total):
        return _mk(
            provider_name="GRUPO COMERCIAL DSW S.A. DE C.V.",
            provider_tax_id="GCD170101GS6",
            total_amount=total,
            subtotal=Decimal("217.27"),
            tax_amount=Decimal("8.14"),
            confidence=0.8,
        )

    def test_gana_la_lectura_cuya_aritmetica_cuadra(self):
        """217.27 + 8.14 = 225.41. Este total CIERRA la suma."""
        buena = self._lectura(Decimal("225.41"))
        mala = self._lectura(Decimal("279.93"))
        assert _mejor_de(mala, buena) is buena

    def test_no_importa_cual_llegue_primero(self):
        buena = self._lectura(Decimal("225.41"))
        mala = self._lectura(Decimal("279.93"))
        assert _mejor_de(buena, mala) is buena

    def test_las_lineas_mandan_sobre_la_aritmetica(self):
        """EL REQUISITO DE PRODUCTO QUE MI PRIMER INTENTO ROMPIO.

        Sin lineas de producto no hay compra que confirmar: `registrar_compra`
        devuelve `None` sin error y el inventario no se entera. El modelo es el
        unico escalon que las trae, asi que una lectura con lineas tiene que
        ganar a una sin ellas aunque su desglose no cierre.

        La primera version de este criterio iba SEGUNDO en la tupla (antes de
        las lineas) y rompio
        `test_capture_ocr.py::TestElOrdenDeLaEscalada::test_por_omision_una_foto_
        sin_lineas_si_va_al_modelo`, que documenta exactamente esto.
        """
        con_lineas_desglose_roto = _mk(
            total_amount=Decimal("279.93"), subtotal=Decimal("217.27"),
            tax_amount=Decimal("8.14"),          # no suma: -1
            items=[{"description": "Cafe", "quantity": 1, "total": Decimal("279.93")}],
        )
        sin_lineas_desglose_bueno = _mk(
            total_amount=Decimal("225.41"), subtotal=Decimal("217.27"),
            tax_amount=Decimal("8.14"),          # suma: +1
        )
        elegido = _mejor_de(sin_lineas_desglose_bueno, con_lineas_desglose_roto)
        assert elegido.items, "sin lineas no hay compra que confirmar"
        assert elegido.total_amount == Decimal("279.93")

    def test_dos_lecturas_igualmente_consistentes_gana_la_primera(self):
        """El criterio es de VERACIDAD, no de rango entre lectores.

        Si las dos cuadran, este criterio no las separa y el desempate sigue
        siendo el orden. Forzar a que gane la vision seria justo el parche
        aprendido de tres archivos que este criterio existe para evitar.
        """
        a = _mk(total_amount=Decimal("234.00"), subtotal=Decimal("234.00"),
                tax_amount=Decimal("0.00"))
        b = _mk(total_amount=Decimal("234.00"), subtotal=Decimal("234.00"),
                tax_amount=Decimal("0.00"))
        assert _mejor_de(a, b) is a

    def test_un_total_malo_no_puede_ganar_a_uno_bueno_por_merecerlo(self):
        """Tener subtotal e IVA NO alcanza: lo que cuenta es que sumen."""
        con_desglose_roto = _mk(total_amount=Decimal("279.93"),
                                 subtotal=Decimal("217.27"),
                                 tax_amount=Decimal("8.14"))
        con_desglose_bueno = _mk(total_amount=Decimal("225.41"),
                                 subtotal=Decimal("217.27"),
                                 tax_amount=Decimal("8.14"))
        assert _mejor_de(con_desglose_roto, con_desglose_bueno) is con_desglose_bueno


class TestLaDecisionNoSeAjustaAUnaSolaTienda:
    """El criterio tiene que ser una PROPIEDAD del papel, no de quien lo leyo."""

    @pytest.mark.parametrize(
        "nombre,subtotal,iva,ieps,total",
        [
            ("gasolinera", "180.00", "28.80", None, "208.80"),
            ("restaurante", "150.00", "24.00", None, "174.00"),
            ("supermercado", "217.27", "8.14", "8.59", "234.00"),
        ],
    )
    def test_cuadra_en_cualquier_comercio(self, nombre, subtotal, iva, ieps, total):
        r = _mk(total_amount=Decimal(total), subtotal=Decimal(subtotal),
                tax_amount=Decimal(iva),
                ieps_amount=Decimal(ieps) if ieps else None)
        assert _aritmetica_se_sostiene(r), nombre