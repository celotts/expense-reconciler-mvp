"""Una linea de AHORRO no es el total, y una etiqueta puede venir con puntuacion.

POR QUE ESTE ARCHIVO EXISTE
===========================

Dos defectos del parser, los dos medidos sobre los 7 comprobantes reales de esta
maquina, los dos con el mismo tipo de fallo: **el parser tomo un numero que no es
el importe que se pago**.

---

## Defecto 1. La linea de ahorro se leia como total

El papel de DSW (`IMG_4253 2.HEIC`) imprime:

```
Usted ahorra $119.97 de $399.90
SUBTOTAL 279.33
Usted ahorra en Total: $119.97
```

El total real es **279.93**. El sistema leia **119.97**, que es el precio ya
descontado, y de ahi venia un `subtotal_plus_tax_mismatch` — o sea, **el gate se
paraba por una razon que no era la real**.

Eso es lo que hace este defecto caro. El gate hizo bien en parar (119.93 no es el
total de un comprobante sin descuento visible), pero se paro por el motivo
equivocado, y un gate que se para por cualquier cosa es un gate que nadie
revisa cuando se para por la buena.

Despues del arreglo el ticket da `total 0.00`, que dispara `total_not_positive`.
Ese motivo **si es el real**: el sistema no supo leer el total de ese papel. Un
`0.00` con el motivo correcto es mas util que un `119.97` con el motivo equivocado,
porque el primero dice "no lo leimos" y el segundo dice "lo leimos mal" cuando en
realidad no se leyo.

---

## Defecto 2. `SUBTOTAL` con puntuacion pegada

La etiqueta exige `^` o whitespace antes. Con el texto real del OCR eso falla
porque las lineas empiezan con el ruido de la caja de la maquina:

```
“SUBTOTAL 108.31     <- Bodega Aurrera, comilla tipografica   NO SE LEIA
«SUBTOTAL 217.27     <- angle bracket                          NO SE LEIA
; SUBTOTAL 964.00    <- este si entraba
```

En los dos primeros el importe **estaba a la vista** y no se leia. El ticket iba
a la cola con `subtotal = None`.

Medido sobre los 7 reales:

| | antes | despues |
|---|---|---|
| exactitud del **subtotal** | 20% (1 de 5) | **40% (2 de 5)** |

El que se recupera es el de Aurrera: `None` -> `108.31`, que es el valor correcto
del papel.

---

LO QUE ESTOS ARREGLOS NO HACEN, Y ES LO IMPORTANTE
==================================================

**El total NO sube.** Sigue en **3 de 5 = 60%**, igual que antes. Lo que cambia es
que el quinto caso pasa de *afirmar un numero equivocado* a *no afirmar nada*, y el
gate reporta el motivo que corresponde.

Eso es exactamente el criterio del proyecto: la exactitud de lo que el sistema
AFIRMA no mejora, y no se presenta como que mejore. Lo que mejora es que ahora
afirma menos cosas y las que afirma se pueden rastrear.

**Mercastar sigue en `0.00`.** El OCR funde la etiqueta —`ARTIC ATOTAL: 5000` en
vez de `ARTIC. TOTAL: 48.00`— y `known-issues.md` §21 documenta por que relajar
ese patron **no se adopto**: leeria `5000`, que es 100 veces el importe real, en una
fila que pareceria buena. Sigue en pie.

**El subtotal de DSW sigue en `279.33` cuando el papel dice `279.93`.** Eso NO es
del parser: es un `9` leido como `3` por el OCR, y es la clase de error que §21
midio y descarta como no corregible sin un segundo motor.

Si alguien lee estos numeros y concluye que el parser mejoro un 20% la exactitud
del total, leyo mal la tabla. La del total esta igual.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.parser_service import (
    AHORRO_EN_LINEA_RE,
    _parse_receipt_text,
)


# ---------------------------------------------------------------------------
# Defecto 1: la linea de ahorro
# ---------------------------------------------------------------------------


class TestUnaLineaDeAhorroNoEsElTotal:
    """La linea de DSW, tal como la devuelve el OCR."""

    # Las cuatro lineas del bloque, en el orden del papel.
    DSW = (
        "o 08 Usted ahorra $119.97 de $399.90\n"
        "E cora SUBTOTAL 279.33\n"
        "CARNET 219.93\n"
        "Usted ahorra en Total: $119.97\n"
    )

    def test_no_toma_el_precio_ya_descontado(self):
        """El numero del que viene el bug: `119.97`, no el total.

        Este es el test que muere si se quita el filtro. El valor exacto esta en
        el papel de `IMG_4253 2.HEIC`, que se puede volver a mirar.
        """
        resultado = _parse_receipt_text(self.DSW)
        assert resultado.total_amount != Decimal("119.97")

    def test_no_lo_toma_tampoco_desde_el_fallback_final(self):
        """La mitad del arreglo que faltaba la primera vez.

        El `continue` del bucle principal saca la linea de ahorro de ahi, pero el
        **fallback del final reescanea `lines` completas** para buscar
        `total|importe`. Con el filtro solo en el bucle, DSW seguia leyendo
        119.97 por esa puerta y el arreglo no hacia NADA. Medido: fue exactamente
        lo que paso en la primera implementacion.
        """
        # Un texto donde el unico "total" esta en una linea de ahorro. Si el
        # fallback no filtra, esto devuelve 119.97.
        solo_ahorro = "Usted ahorra en Total: $119.97"
        resultado = _parse_receipt_text(solo_ahorro)
        assert resultado.total_amount == Decimal("0.00")

    def test_el_motivo_del_gate_ya_no_cita_el_numero_inventado(self):
        """Lo que se comprueba es que el motivo ya no hable de `119.97`.

        Y aqui hay que ser preciso, porque la primera version de este test
        afirmaba algo mas fuerte y mas falso: que el `subtotal_plus_tax_mismatch`
        **desaparecia**. No desaparece, y no deberia.

        Lo que ocurre es esto:

        - Antes: `total 119.97`, y el motivo decia
          `subtotal_plus_tax_mismatch(leido=119.97,esperado=279.33)`. El check
          comparaba el **precio ya descontado** contra el subtotal, y se paraba
          por una razon que no era la real.
        - Ahora: `total 0.00`, y el motivo dice
          `subtotal_plus_tax_mismatch(leido=0.00,esperado=279.33)`, **ademas** de
          `total_not_positive`.

        El mismatch sigue porque `279.33 + 0 != 0.00`, y es correcto que siga: el
        sistema de verdad no leyo el total de ese papel. Lo que cambio es que
        ahora el motivo dice `leido=0.00` —"no lei nada"— y no `leido=119.97`,
        que era una cifra que jamas estuvo en el papel como total.

        Por eso la asercion mira el **numero que el motivo cita**, y no si el
        motivo aparece. Un test que exigiera que el mismatch desapareciera
        obligaria a un arreglo peor: a inventar un total para callar un check.

        Se pasa por `validate_extraction` y no por `gate_ticket` a proposito: lo
        que se comprueba es el **check** que dispara, y `gate_ticket` mete ademas
        la confianza, el origen y el umbral, que son otra decision. Un test que
        dependiera del umbral entero dejaria de morir si el umbral cambiara.
        """
        from app.services.confidence_gate import validate_extraction

        resultado = _parse_receipt_text(self.DSW)
        out = validate_extraction(
            provider_name=resultado.provider_name,
            total_amount=resultado.total_amount,
            tax_amount=resultado.tax_amount,
            expense_date=resultado.expense_date,
            provider_tax_id=resultado.provider_tax_id,
            subtotal=resultado.subtotal,
        )

        # El motivo de "no se leyo" esta, y es el que manda sobre cual leer.
        assert "total_not_positive" in out.failures

        # Y ningun motivo cita el numero del descuento como si fuera el total.
        # Esta es la asercion que importa: que `119.97` no aparezca en ningun
        # motivo del gate como si fuera un importe leido del comprobante.
        assert not any("119.97" in f for f in out.failures)

    def test_el_total_inventado_no_llega_a_la_base(self):
        """La defensa final, y la que de verdad importa.

        Este test mira el `total_amount` que el parser entrega, no el motivo del
        gate. El gate es la segunda linea; la primera es que el numero no exista.

        Y por que se prueba con `0.00` y no con "cualquier valor distinto de
        119.97": porque un arreglo posterior podria inventar OTRO numero —el
        279.93 del papel, por ejemplo, que seria lo correcto en valor pero
        inventado en origen—. La unica asercion que sobrevive a eso es "no hay
        ningun total-affirmado mientras la linea sea de ahorro".
        """
        resultado = _parse_receipt_text(self.DSW)
        assert resultado.total_amount == Decimal("0.00")

    def test_el_subtotal_de_la_misma_foto_si_se_lee(self):
        """El filtro es de la linea del total, no del documento entero.

        `SUBTOTAL 279.33` esta en el mismo bloque y **no** habla de ahorro, asi que
        tiene que seguir entrando. Un filtro de "si el documento habla de ahorro,
        no leas nada" habria tirado el subtotal correcto de este comprobante.
        """
        resultado = _parse_receipt_text(self.DSW)
        assert resultado.subtotal == Decimal("279.33")

    def test_el_filtro_no_toca_el_proveedor(self):
        """`AHORRO EXPRESS` es un comercio real, no una linea de ahorro.

        El filtro es del bucle de IMPORTES y corre despues de la primera pasada,
        que es la que elige el proveedor. Si el filtro se subiera arriba del todo,
        un comercio con "ahorro" en el nombre perderia su nombre.
        """
        texto = "AHORRO EXPRESS SA DE CV\nTOTAL $55.00\n"
        resultado = _parse_receipt_text(texto)
        assert "AHORRO" in resultado.provider_name
        # Y el total SI se lee, porque "AHORRO EXPRESS SA DE CV" no dice nada
        # sobre el importe.
        assert resultado.total_amount == Decimal("55.00")


class TestQuePalabrasCuentanComoAhorro:
    """La lista, medida contra lo que imprimen los tickets."""

    def test_las_formas_reales_estan_cubiertas(self):
        for linea in [
            "Usted ahorra $119.97 de $399.90",
            "Usted ahorra en Total: $119.97",
            "AHORRASTE $8.00",
            "AHORRO $18.50",
            "DESCUENTO $50.00",
            "REBAJA 1X$35 -5.00",
            "Precio final $279.93",
        ]:
            assert AHORRO_EN_LINEA_RE.search(linea), linea

    def test_la_palabra_tiene_que_estar_en_la_linea(self):
        """`\b` en los dos extremos, no una subcadena.

        Sin el `\b` final, `ahorros` o una palabra que la contenga tambien se
        filtrarian. Se comprueba con una linea que NO es de ahorro y que es
        real de un ticket.
        """
        # "DESCUENTOS DEL 3%" es un aviso, no un importe, pero "TOTAL" aqui es el
        # total de verdad y no debe filtrarse solo por mencionar "descuento" en
        # otra parte del renglon... salvo que si lo mencione ESTA linea. Ver
        # abajo el caso que si debe filtrarse.
        assert not AHORRO_EN_LINEA_RE.search("ARTICULOS 3 TOTAL $48.00")

    def test_no_filtra_una_linea_que_no_habla_de_ahorro(self):
        for linea in [
            "TOTAL 234.00",
            "SUBTOTAL 217.27",
            "ARTICULOS COMPRADOS: 28",
            "TARJETA: VISA DEBIT",
        ]:
            assert not AHORRO_EN_LINEA_RE.search(linea), linea


class TestUnTotalDeVerdadSigueLeyendose:
    """Lo que el filtro NO puede romper.

    Un filtro de importes es dangerous por construccion: cualquier cosa que se
    excluya es un total que no se lee. Estos son los casos que tienen que
    seguir funcionando, y por eso estan aqui al lado de los del filtro.
    """

    def test_un_total_normal(self):
        assert _parse_receipt_text("TOTAL 234.00").total_amount == Decimal("234.00")

    def test_un_total_con_cambio_en_la_misma_linea(self):
        """El caso de Carne Mart, que es de los que SI salen bien.

            O/CREDITO TOTAL (* ENTRE HtvENTA $ 97.56 SU Cahbio $ 0 .00

        El primer importe despues de la etiqueta es el que va con ella.
        """
        linea = "O/CREDITO TOTAL (* ENTRE HtvENTA $ 97.56 SU Cahbio $ 0 .00"
        assert _parse_receipt_text(linea).total_amount == Decimal("97.56")

    def test_el_total_de_dsw_si_se_lee_cuando_el_papel_si_lo_imprime(self):
        """La contra-prueba del filtro.

        Si un ticket de descuento imprime la palabra "ahorra" en una parte y su
        `TOTAL` de verdad en otra, el total tiene que entrar. Un filtro de
        "documento con ahorro, desconfiar" perderia este ticket; un filtro de
        "linea con ahorro, descartar" no.
        """
        texto = "Usted ahorra $50.00 de $299.90\nTOTAL $249.90\n"
        assert _parse_receipt_text(texto).total_amount == Decimal("249.90")


# ---------------------------------------------------------------------------
# Defecto 2: la puntuacion antes de la etiqueta
# ---------------------------------------------------------------------------


class TestSubtotalConPuntuacionPegada:
    """El numero esta a la vista y el patron no lo alcanza."""

    def test_comilla_tipografica(self):
        """El caso medido, de Bodega Aurrera.

            “SUBTOTAL 108.31

        El importe es el correcto del papel (108.31) y antes no se leia. Es el
        arreglo que sube el subtotal de 20% a 40%.
        """
        assert _parse_receipt_text("“SUBTOTAL 108.31").subtotal == Decimal("108.31")

    def test_angle_bracket(self):
        assert _parse_receipt_text("«SUBTOTAL 217.27").subtotal == Decimal("217.27")

    def test_las_formas_que_ya_funcionaban_siguen(self):
        for linea, esperado in [
            ("SUBTOTAL 108.31", Decimal("108.31")),
            ("SUBTOTAL: $1,000.00", Decimal("1000.00")),
            ("SUB-TOTAL 500.00", Decimal("500.00")),
            ("; SUBTOTAL 964.00", Decimal("964.00")),
        ]:
            assert _parse_receipt_text(linea).subtotal == esperado, linea

    def test_subtotal_no_contamina_el_total(self):
        """El requisito original del borde: "SUBTOTAL" no es "TOTAL".

        Este es el motivo por el que el patron existia, y sigue valiendo. Un
        `SUBTOTAL 964.00` sin `TOTAL` no puede dejar el total en 964.
        """
        resultado = _parse_receipt_text("SUBTOTAL 964.00\n")
        assert resultado.subtotal == Decimal("964.00")
        assert resultado.total_amount == Decimal("0.00")

    def test_sin_subtotal_sigue_siendo_none_y_no_cero(self):
        """Cero es una afirmacion distinta de "no vine nada".

        El parser ya distingue las dos cosas y este test fija que el arreglo del
        borde no las mezcle: un `0.00` de subtotal haria que
        `subtotal + IVA == total` diera falso negativo para siempre.
        """
        assert _parse_receipt_text("TOTAL 234.00").subtotal is None

    def test_una_letra_pegada_sigue_no_entrando(self):
        r"""El limite del borde relajado.

        `[^\w]` acepta puntuacion y rechaza letras y digitos. `XSUBTOTAL` es ruido
        de OCR, no una etiqueta, y leerlo seria inventar un subtotal.

        El docstring va en crudo porque `\w` en un docstring normal es un escape
        invalido: Python avisa con `SyntaxWarning` hoy y lo va a hacer fallar en
        una version futura. El aviso aparece en el `pytest` de cualquiera que
        corra la suite, y un warning que se repite en cada corrida deja de leerse.
        """
        assert _parse_receipt_text("XSUBTOTAL 108.31").subtotal is None


class TestElTotalTambienAceptaElBordeRelajado:
    """El mismo borde, aplicado al total.

    No es simetria por estetica: es que el texto del OCR pone el ruido delante de
    CADA etiqueta, no solo de unas. Con solo el subtotal arreglado, un ticket
    cuyo total viene con `“Total:` seguiria sin leerse, y el arreglo se veria
    como completo cuando esta a medias.
    """

    def test_total_con_comilla(self):
        assert _parse_receipt_text("“TOTAL 234.00").total_amount == Decimal("234.00")

    def test_total_con_angle_bracket(self):
        assert _parse_receipt_text("«TOTAL 279.93").total_amount == Decimal("279.93")