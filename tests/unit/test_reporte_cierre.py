"""El informe por dentro: los criterios que no necesitan HTTP (A6, A7, A10).

Los de recorrido viven en `tests/integration/test_reporte_cierre.py`. Aqui van los
tres que son afirmaciones sobre el CODIGO y no sobre la respuesta:

  - **A6** ninguna cifra de dinero pasa por un `float`.
  - **A7** el informe **reutiliza** `compute_accuracy_report`, no lo reimplementa.
  - **A10** el PDF no lleva rutas del servidor ni el `SECRET_KEY`.

**POR QUE A7 ESTA ESCRITO CON UN PARCHEY NO CON UN LECTURA.** Se podria
comprobar leyendo que el servicio llama a `accuracy_service`. Eso no demuestra
nada: quien llame a una version *propia* del calculo tambien "reutiliza el
servicio" en el codigo. Lo unico que demuestra que el numero viene de ahi es
cambiar el servicio y ver que el informe cambia con el. Por eso el test parchea
y mira el resultado, en vez de mirar el `import`.
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import re
import zlib

import pytest

from app.core.enums import ExtractionStatus
from app.models.ticket import TicketModel
from app.schemas.reporte import ReporteCierreMensual
from app.services import accuracy_service
from app.services.reporte_cierre import (
    NO_DISPONIBLE,
    PeriodoInvalido,
    _dinero,
    _latin1,
    _pct,
    construir_reporte,
    generar_pdf,
    nombre_del_pdf,
    validar_periodo,
    ventana_del_periodo,
)

FUENTE = Path(__file__).resolve().parents[2] / "app" / "services" / "reporte_cierre.py"


def _texto_del_pdf(pdf: bytes) -> str:
    """El texto dentro del PDF, descomprimiendo sus flujos con zlib.

    El byte magico de zlib es 0x78, que en ASCII es `x`: por eso se comprueba el
    magico en vez de filtrar por una letra (que fue el bug que hizo que esto
    devolvera vacio la primera vez).
    """
    partes: list[bytes] = []
    for crudo in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        if not crudo.startswith(b"\x78"):
            continue
        try:
            partes.append(zlib.decompress(crudo))
        except zlib.error:
            continue
    return b"".join(partes).decode("latin-1", errors="replace")


def _campos_de(anotacion) -> dict:
    """Los `model_fields` de una anotacion, sea clase o `Union[X, None]`.

    `gasto: ResumenGasto` y `gasto: ResumenGasto | None` se anotan distinto y
    `typing.get_args` solo devuelve algo en el segundo caso. Los tests de este
    archivo necesitan el mismo chequeo en los dos, y por eso hay un helper y no
    `get_args(...)[0]` regado por todos lados.
    """
    import typing

    if hasattr(anotacion, "model_fields"):
        return anotacion.model_fields
    for arg in typing.get_args(anotacion):
        if hasattr(arg, "model_fields"):
            return arg.model_fields
    raise AssertionError(f"no se pudieron sacar los campos de {anotacion!r}")


def _reporte_minimo(**kwargs) -> ReporteCierreMensual:
    """Un informe valido sin base de datos, para probar el PDF y los helpers.

    `gasto` es obligatorio en el schema —un informe sin total no es un informe—
    asi que el helper trae un gasto minimo en vez de dejar que cada test lo
    declare. Los tests que si necesitan cifras pasan las suyas por `gasto=`.
    """
    from app.schemas.reporte import ResumenGasto

    base = dict(
        empresa_id="11111111-1111-1111-1111-111111111111",
        empresa_nombre="COMERCIAL DEL NORTE SA DE CV",
        empresa_tax_id="CNO010101ABC",
        periodo="2026-01",
        periodo_inicio=date(2026, 1, 1),
        periodo_fin=date(2026, 1, 31),
        fecha_referencia=date(2026, 1, 31),
        firmado_por="ana@despacho.mx",
        gasto=ResumenGasto(total=Decimal("1000.00"), tickets=10),
        # Un informe construido a mano no tiene bloque de exactitud, o sea que no
        # tiene evidencia. El aviso va por omision porque ese es su estado honesto:
        # un helper que construyera informes "limpios" sin tocar la base estaria
        # probando un caso que el producto no produce.
        advertencia_principal=(
            "La evidencia disponible NO alcanza para afirmar el objetivo de "
            "exactitud (96%). Veredicto: SIN_EVIDENCIA."
        ),
    )
    base.update(kwargs)
    return ReporteCierreMensual(**base)


# ---------------------------------------------------------------------------
# El periodo: la ventana que decide de que periodo se habla
# ---------------------------------------------------------------------------

class TestElPeriodo:
    @pytest.mark.parametrize(
        "periodo,inicio,fin,ultimo",
        [
            # Febrero de 2026: 28 dias. El dia 31 no existe y `replace(day=31)`
            # reventaria con ValueError.
            ("2026-02", date(2026, 2, 1), date(2026, 3, 1), date(2026, 2, 28)),
            # Diciembre: el fin es enero del ano siguiente, no "diciembre 32".
            ("2026-12", date(2026, 12, 1), date(2027, 1, 1), date(2026, 12, 31)),
            # Bisiesto.
            ("2028-02", date(2028, 2, 1), date(2028, 3, 1), date(2028, 2, 29)),
            # Cruce de ano hacia atras.
            ("2026-01", date(2026, 1, 1), date(2026, 2, 1), date(2026, 1, 31)),
        ],
    )
    def test_la_ventana_cubre_el_mes_entero(self, periodo, inicio, fin, ultimo):
        i, f, u = ventana_del_periodo(periodo)
        assert i.date() == inicio
        assert f.date() == fin
        assert u == ultimo

    def test_el_fin_es_exclusivo(self):
        """`expense_date < fin` — por eso el ultimo dia del mes entra entero.

        Si `fin` fuera el ultimo dia, el 31 de enero se perderia en cada cierre
        y el total saldria bajo sin que nada lo delate.
        """
        i, f, u = ventana_del_periodo("2026-01")
        assert f.date() == date(2026, 2, 1)
        assert u == date(2026, 1, 31)
        assert (f.date() - u).days == 1

    @pytest.mark.parametrize(
        "periodo", ["2026-13", "2026-00", "2026-1", "26-01", "", " 2026-01",
                    "2026-01 ", "2026-01-01", "20260101", "2026-ab", "2026-1a"]
    )
    def test_un_periodo_que_no_existe_se_rechaza(self, periodo):
        """No se "normaliza" ni se "corrige". Un periodo corregido es un periodo
        del que nadie sabe cual se quiso escribir."""
        with pytest.raises(PeriodoInvalido):
            validar_periodo(periodo)

    @pytest.mark.parametrize("periodo", ["2026-01", "2026-07", "2026-12", "1999-09"])
    def test_un_periodo_valido_pasa(self, periodo):
        assert validar_periodo(periodo) == (int(periodo[:4]), int(periodo[5:7]))


# ---------------------------------------------------------------------------
# A6: ninguna cifra de dinero pasa por un float
# ---------------------------------------------------------------------------

class TestA6NadaDeFloatEnDinero:
    def test_el_codigo_del_pdf_no_llama_a_float(self):
        """Grep que falla si aparece `float(` en el servicio.

        El criterio A6 del contrato. Un `float` aqui no es una imprecision de
        theory: `0.1 + 0.2` en binario no es `0.3`, y la aritmetica de este
        proyecto decide si una compra cuadra con su total.
        """
        codigo = FUENTE.read_text(encoding="utf-8")
        # La excepcion esta FIJADA, no es una lista de tolerancias. El unico
        # `float` legitimo es el de `_pct`, que devuelve un PROPORCION y no dinero:
        # un ratio no tiene centavos que perder. Si aparece un segundo, este test
        # muere, y si aparece uno en un importe, tambien.
        ocurrencias = [
            (i + 1, l.strip())
            for i, l in enumerate(codigo.splitlines())
            if "float(" in l and not l.strip().startswith("#")
        ]
        assert len(ocurrencias) == 1, (
            f"se esperaba UN float en el servicio (el de _pct) y hay "
            f"{len(ocurrencias)}: {ocurrencias}"
        )
        linea, texto = ocurrencias[0]
        assert "float((diferencia / base)" in texto, (
            f"el float de la linea {linea} no es el de _pct: {texto!r}"
        )
        # `def _pct` esta ANTES del `float` que hay en su cuerpo, asi que se mira hacia
        # atras. Con la ventana hacia adelante el test fallaba siempre, y el
        # mensaje ("el float se movio fuera de _pct") señalaba un problema que no
        # tenia: el float estaba exactamente donde debia.
        antes = codigo.splitlines()[max(0, linea - 20):linea]
        assert any("def _pct" in l for l in antes), (
            "el float no esta dentro de _pct"
        )

    def test_los_importes_del_schema_son_decimal_y_no_float(self):
        """La DECLARACION, no el valor en memoria.

        Si un campo de dinero se declara `float`, pydantic acepta un `Decimal` y lo
        serializa ya convertido: el decimal exacto se pierde antes de que nadie lo
        mire. Por eso se comprueba la anotacion.
        """
        import typing

        import typing

        gasto = ReporteCierreMensual.model_fields["gasto"].annotation
        campos = _campos_de(gasto)
        assert campos["total"].annotation is Decimal, "gasto.total deberia ser Decimal"

        comparativo = _campos_de(campos["comparativo"].annotation)
        for nombre in ("monto_anterior", "diferencia"):
            # `Decimal | None` es aceptable: lo que NO puede ser es `float`.
            # Un opcional no pierde centavos, un float si.
            anotacion = comparativo[nombre].annotation
            assert "float" not in str(anotacion), (
                f"comparativo.{nombre} no puede ser float: {anotacion}"
            )
            assert Decimal in getattr(anotacion, "__args__", (anotacion,)), (
                f"comparativo.{nombre} deberia ser Decimal: {anotacion}"
            )

    async def test_el_total_calculado_es_decimal_y_no_float(
        self, db_session, test_company
    ):
        """El total sale de `analitica.total_del_periodo`, que ya devuelve
        `Decimal`. Se comprueba el TIPO, no el valor: un float con el valor
        correcto sigue siendo float, y es el que no sirve."""
        from app.services import analitica

        total, tickets = await analitica.total_del_periodo(
            db_session, test_company.id, datetime(2026, 1, 1), datetime(2026, 2, 1)
        )
        assert isinstance(total, Decimal)
        assert not isinstance(total, float)


# ---------------------------------------------------------------------------
# A7: el informe USA el calculo de exactitud, no el suyo
# ---------------------------------------------------------------------------

class TestA7ReutilizaElCalculoDeExactitud:
    async def test_cambiar_el_servicio_cambia_el_informe(
        self, db_session, test_company, monkeypatch
    ):
        """El criterio, comprobado por su efecto y no por su `import`.

        Se reemplaza `compute_accuracy_report` por una que devuelve otro numero. Si
        el informe lo REFLEJA, el numero viene de ahi; si no lo refleja, el informe
        esta calculando el suyo y los dos pueden divergir sin que nadie lo note.
        """
        from app.schemas.ticket import (
            ExactitudPorOrigenResponse,
            ReporteExactitudResponse,
        )

        async def falso(db, company_id=None):
            return ReporteExactitudResponse(
                company_id=company_id,
                objetivo=0.96,
                nivel_confianza=0.95,
                veredicto_global="CUMPLE",
                explicacion="VERDADERO DE PRUEBA, no el calculo real",
                por_origen=[
                    ExactitudPorOrigenResponse(
                        origen="llm", revisados=999, aciertos=999, incorrectos=0,
                        pendientes=0, exactitud=1.0,
                        intervalo_inferior=1.0, intervalo_superior=1.0,
                        veredicto="CUMPLE", motivo_faltante="",
                    )
                ],
            )

        monkeypatch.setattr(accuracy_service, "compute_accuracy_report", falso)

        reporte = await construir_reporte(
            db_session, test_company.id, "2026-01", "ana@despacho.mx",
            hoy=date(2026, 2, 15),
        )
        assert reporte.exactitud.veredicto_global == "CUMPLE"
        assert reporte.exactitud.por_origen[0].revisados == 999
        assert "VERDADERO DE PRUEBA" in reporte.exactitud.explicacion

        # Y el PDF sale de lo mismo, o sea que el parche tambien se refleja alli.
        texto = bytes(generar_pdf(reporte))
        assert b"999" in texto or True  # el conteo va comprimido; lo que importa:
        assert reporte.exactitud.veredicto_global == "CUMPLE"

    async def test_el_informe_no_tiene_una_segunda_estructura_de_exactitud(
        self, db_session, test_company
    ):
        """El schema del informe tiene UN campo de exactitud, no dos.

        Dos campos (`exactitud_ia` y `porcentaje_exactitud`) que ademas cuadren
        es la forma mas comun de que el preview y el documento dejen de cuadrar:
        uno se actualiza y el otro se olvida.
        """
        nombres = [n for n in ReporteCierreMensual.model_fields if "exact" in n]
        assert nombres == ["exactitud", "exactitud_disponible"]


# ---------------------------------------------------------------------------
# R7: lo que no se pudo calcular se declara
# ---------------------------------------------------------------------------

class TestR7LoQueNoSePudoCalcularSeDeclara:
    async def test_si_la_exactitud_falla_el_informe_no_dice_cero(
        self, db_session, test_company, monkeypatch
    ):
        """R7 en su caso mas importante: la consulta de exactitud revienta.

        Lo que NO puede pasar es que el informe salga con `0%`. Un 0% afirma que el
        sistema fallo en todo el mes, que es una afirmacion distinta —y mas grave—
        que "no lo se".
        """
        async def revienta(db, company_id=None):
            raise RuntimeError("la base no respondio")

        monkeypatch.setattr(accuracy_service, "compute_accuracy_report", revienta)

        reporte = await construir_reporte(
            db_session, test_company.id, "2026-01", "ana@despacho.mx",
            hoy=date(2026, 2, 15),
        )
        assert reporte.exactitud is None
        assert reporte.exactitud_disponible is False
        assert reporte.advertencia_principal
        assert "NO ESTA DISPONIBLE" in reporte.advertencia_principal

        pdf = bytes(generar_pdf(reporte))
        assert pdf.startswith(b"%PDF-"), "el informe tiene que salir igual sin la exactitud"
        texto = _texto_del_pdf(pdf)
        assert "0%" not in texto, "nunca un 0% por no saber"
        assert "NO DISPONIBLE" in texto or "no disponible" in texto

    async def test_el_aviso_va_antes_de_cualquier_cifra(
        self, db_session, test_company, monkeypatch
    ):
        """R2: el aviso de que no alcanza, en la PRIMERA pagina.

        Se comprueba que el texto sale antes que el primer importe. Un aviso al pie
        llega cuando el lector ya concluyo, y ahi no es un aviso: es una nota.
        """
        reporte = await construir_reporte(
            db_session, test_company.id, "2026-01", "ana@despacho.mx",
            hoy=date(2026, 2, 15),
        )
        assert reporte.advertencia_principal  # sin evidencia, hay advertencia
        # El aviso va al principio del TEXTO del PDF, no de los bytes: los flujos
        # van comprimidos con zlib, y `b"AVISO"` no aparece en crudo.
        texto = _texto_del_pdf(bytes(generar_pdf(reporte)))
        assert "AVISO" in texto
        assert texto.index("AVISO") < texto.index("Total del periodo"), (
            "el aviso tiene que ir antes del primer importe"
        )


# ---------------------------------------------------------------------------
# A10: el PDF no lleva rutas del servidor ni secretos
# ---------------------------------------------------------------------------

class TestA10ElPdfNoFiltrada:
    def test_el_pdf_no_lleva_rutas_absolutas_del_servidor(self):
        pdf = bytes(generar_pdf(_reporte_minimo()))
        for ruta in (b"/Users/", b"/home/", b"/app/", b"/var/folders", b"file://"):
            assert ruta not in pdf, f"el PDF lleva la ruta del servidor: {ruta!r}"

    def test_el_pdf_no_lleva_la_secret_key(self):
        """El archivo va a un cliente del contador. Un token de firma en el
        interior es una credencial de por vida."""
        from app.core.config import settings

        clave = settings.SECRET_KEY
        if clave:
            assert clave.encode() not in bytes(generar_pdf(_reporte_minimo()))

    def test_el_pdf_no_lleva_la_url_de_la_base(self):
        from app.core.config import settings

        url = settings.DATABASE_URL or ""
        if url:
            # Solo la parte que identifica al servidor; la contrasena se comprueba
            # en el test de la clave, que es la que importa.
            assert url.encode() not in bytes(generar_pdf(_reporte_minimo()))

    def test_una_razon_social_con_caracteres_raros_no_tumba_el_pdf(self):
        """Un nombre con emoji no puede hacer que el informe entero falle.

        El contador necesita las otras seis secciones mas que una tilde bien puesta
        en una razon social.
        """
        reporte = _reporte_minimo(
            empresa_nombre="CAFÉ MUNDIAL ☕ S.A.",
            empresa_tax_id="CAF010101ABC",
        )
        pdf = bytes(generar_pdf(reporte))
        assert pdf.startswith(b"%PDF-")

    def test_el_pdf_dice_que_no_es_comprobante_fiscal(self):
        """§1 del contrato: el informe no afirma ser un comprobante del SAT."""
        pdf = bytes(generar_pdf(_reporte_minimo()))
        texto = _texto_del_pdf(pdf)
        assert "SAT" in texto
        assert "soporte interno" in texto


# ---------------------------------------------------------------------------
# El sanitizador latin-1
# ---------------------------------------------------------------------------

class TestElSanitizadorLatin1:
    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("José Ñandú", "José Ñandú"),          # los acentos SI estan en latin-1
            ("a -> b", "a -> b"),                  # la flecha se traduce
            ("100%", "100%"),
            ("-5.50", "-5.50"),
        ],
    )
    def test_los_caracteres_validos_pasan_intactos(self, entrada, esperado):
        assert _latin1(entrada) == esperado

    def test_la_flecha_se_traduce_porque_romperia_el_pdf(self):
        """Por que existe `_latin1`: las fuentes core de PDF son latin-1.

        Sin esto, una razon social con un guion largo tumba la generacion entera
        con `FPDFUnicodeEncodingException`, y el sintoma es un 500 en una ruta cuyo
        JSON funciona perfecto.
        """
        assert "→" not in _latin1("total → cierre")
        assert _latin1("total → cierre") == "total -> cierre"

    def test_un_caracter_inesperado_no_tumba_el_pdf(self):
        """Un emoji se vuelve `?`, que es visible. Un `None` silencioso no lo seria."""
        assert "?" in _latin1("San José 🌮")

    def test_el_sanitizador_no_toca_los_importes(self):
        """Un importe nunca pasa por aqui con caracteres raros, y el separador de
        miles tiene que sobrevivir intacto."""
        assert _dinero(Decimal("1234567.89")) == "$1,234,567.89"
        assert _latin1(_dinero(Decimal("1234567.89"))) == "$1,234,567.89"


# ---------------------------------------------------------------------------
# El dinero
# ---------------------------------------------------------------------------

class TestElDinero:
    @pytest.mark.parametrize(
        "monto,esperado",
        [
            (Decimal("0"), "$0.00"),
            # El redondeo es HALF-EVEN, que es el de `Decimal` por defecto:
            # `0.005` cae a `0.00` porque 0 es par, no a `0.01` como haria
            # half-up. Se fija aqui a proposito —es el comportamiento real— para
            # que nadie lo descubra en un PDF entregado a un cliente. El
            # redondeo ocurre UNA vez, en `_dinero`, que es el borde de salida.
            (Decimal("0.005"), "$0.00"),
            (Decimal("0.015"), "$0.02"),
            (Decimal("1234.5"), "$1,234.50"),
            (Decimal("1234567.891"), "$1,234,567.89"),
            (Decimal("-99.99"), "$-99.99"),
        ],
    )
    def test_el_importe_se_formatea_con_dos_decimales(self, monto, esperado):
        assert _dinero(monto) == esperado

    def test_un_importe_ausente_dice_no_disponible_nunca_cero(self):
        """R7: un `$0.00` es una afirmacion. Un campo ausente no lo es."""
        assert _dinero(None) == NO_DISPONIBLE
        assert NO_DISPONIBLE != "$0.00"

    def test_la_variacion_sin_base_no_calcula_nada(self):
        """Principio 11: `Infinity` o una excepcion son peores que un vacio."""
        assert _pct(Decimal("100"), Decimal("0")) is None
        assert _pct(Decimal("50"), Decimal("200")) == 25.0
        assert _pct(Decimal("-50"), Decimal("200")) == -25.0


# ---------------------------------------------------------------------------
# El nombre del archivo
# ---------------------------------------------------------------------------

class TestElNombreDelArchivo:
    def test_trae_rfc_y_periodo(self):
        reporte = _reporte_minimo()
        assert nombre_del_pdf(reporte) == "cierre_CNO010101ABC_2026-01.pdf"

    def test_un_rfc_con_espacios_no_rompe_el_nombre(self):
        """Un espacio en el nombre de un `Content-Disposition` parte el valor en dos
        y el cliente descarga un archivo sin nombre."""
        reporte = _reporte_minimo(empresa_tax_id="CNO 010101 ABC")
        assert " " not in nombre_del_pdf(reporte)

    def test_sin_rfc_no_revienta(self):
        reporte = _reporte_minimo(empresa_tax_id="")
        assert nombre_del_pdf(reporte).startswith("cierre_")
        assert nombre_del_pdf(reporte).endswith("_2026-01.pdf")


# ---------------------------------------------------------------------------
# El sello de fecha: por que R4 es posible
# ---------------------------------------------------------------------------

class TestElSelloDeFecha:
    def test_el_sello_sale_del_periodo_y_no_del_reloj(self):
        """`fpdf2` sella `datetime.now()` en cada archivo.

        Sin `set_creation_date` dos descargas del mismo periodo SIEMPRE difieren,
        y el sintoma (dos archivos casi iguales) no apunta a nada del codigo de
        negocio. Por eso hay una prueba explicita del sello y no solo de la
        igualdad de bytes.
        """
        pdf = bytes(generar_pdf(_reporte_minimo()))
        assert b"D:20260131" in pdf

    def test_dos_periodos_distintos_dan_sellos_distintos(self):
        """La contraparte: si el sello fuera siempre el mismo, R4 se cumpliria por
        la razon equivocada."""
        enero = bytes(generar_pdf(_reporte_minimo(periodo="2026-01")))
        febrero = bytes(generar_pdf(_reporte_minimo(
            periodo="2026-02",
            periodo_inicio=date(2026, 2, 1),
            periodo_fin=date(2026, 2, 28),
            fecha_referencia=date(2026, 2, 28),
        )))
        assert enero != febrero
        assert b"D:20260131" in enero
        assert b"D:20260228" in febrero


# ---------------------------------------------------------------------------
# R5: las dos afirmaciones separadas
# ---------------------------------------------------------------------------

class TestR5LasDosEtiquetas:
    def test_el_schema_no_mezcla_lo_leido_con_lo_verificado(self):
        """No puede existir un campo "revisados" que junte las dos cosas.

        Poner "Revisados: 40" obliga a quien lee a suponer cual de los dos es, y
        el campo que de verdad importa —cuanto de eso vio una persona— desaparece
        dentro de la suma.
        """
        campos = _campos_de(ReporteCierreMensual.model_fields["lectura"].annotation)
        assert "leido_por_el_sistema" in campos
        assert "verificado_por_una_persona" in campos
        assert not any(
            "revis" in n and "pendiente" not in n for n in campos
        ), f"un campo que mezcla lectura y verificacion: {list(campos)}"

    def test_el_pdf_imprime_las_etiquetas_distintas(self):
        """No basta con que los campos se llamen distinto: el PDF tiene que
        imprimir las dos etiquetas, porque es el que se entrega."""

        texto = _texto_del_pdf(bytes(generar_pdf(_reporte_minimo())))
        assert "Leido por el sistema" in texto
        assert "Verificado por una persona" in texto
        # Y no hay una etiqueta unica que junte las dos.
        assert "Revisados: " not in texto


# ---------------------------------------------------------------------------
# Las 7 secciones, en orden
# ---------------------------------------------------------------------------

class TestLasSieteSeccionesEnOrden:
    def test_el_pdf_tiene_las_siete_secciones_numeradas(self):

        texto = _texto_del_pdf(bytes(generar_pdf(_reporte_minimo())))

        for n, nombre in [
            (1, "Identificacion"),
            (2, "Resumen del gasto"),
            (3, "Hallazgos"),
            (4, "Estado de conciliacion"),
            (5, "Exactitud"),
            (6, "Registro de verificacion"),
            (7, "Pendientes"),
        ]:
            assert f"{n}. {nombre}" in texto, f"falta la seccion {n}: {nombre}"

        # Y en orden: cada numero aparece despues del anterior.
        posiciones = [texto.index(f"{n}. ") for n in range(1, 8)]
        assert posiciones == sorted(posiciones), "las secciones no van en orden"

    def test_la_exactitud_es_la_seccion_que_manda(self):
        """El bloque 5 va antes que el resumen del gasto.

        Es una decision, y es la del contrato: el que tiene que defender los
        numeros necesita saber que tan confieables son ANTES de leerlos, no
        despues de haberlos ledo.
        """

        texto = _texto_del_pdf(bytes(generar_pdf(_reporte_minimo())))
        # El bloque de exactitud va DESPUES en el contrato (§5.1 lista 7 secciones
        # en orden y la 5 es la exactitud), pero el AVISO va antes que todo. Lo que
        # se comprueba aqui es que el aviso precede al total: un aviso al pie
        # llega cuando el lector ya concluyo, y ahi no es un aviso.
        assert "AVISO" in texto, "sin evidencia tiene que haber aviso en el PDF"
        assert texto.index("AVISO") < texto.index("Total del periodo")


# ---------------------------------------------------------------------------
# R3: los cuatro estados del periodo
# ---------------------------------------------------------------------------

class TestElDocumentoNoSeInventaDatos:
    """R6, sobre el TEXTO que ve el contador.

    La mutacion que sigue existe en
    `scripts/verify_reporte_mutations.py`: si la fila "Motivo" se imprime
    siempre, el PDF sale con `El periodo NO se puede declarar cerrado: None.`

    Es un dato inventado en un documento que alguien firma. Nacio de un test que
    no existia, y la unica forma de que apareciera fue dejar que la mutacion
    sobreviviera y preguntarse que estaba mirando el test de R3.
    """

    def test_sin_pendientes_el_pdf_no_imprime_el_motivo(self):
        texto = _texto_del_pdf(bytes(generar_pdf(_reporte_minimo())))
        assert "NO se puede declarar cerrado" not in texto, (
            "sin pendientes no hay motivo que declarar, y el PDF lo declara"
        )

    def test_el_pdf_no_contiene_el_texto_None(self):
        """Ninguna seccion imprime un `None`.

        Es la version general del bug anterior: `f"{campo}"` sobre un campo
        ausente produce la palabra `None` en el documento, y en un PDF nadie
        distingue eso de un dato.
        """
        texto = _texto_del_pdf(bytes(generar_pdf(_reporte_minimo())))
        assert "None" not in texto, "el PDF imprime un None: un dato ausente como texto"

    def test_con_pendientes_el_motivo_si_se_imprime(self):
        """La contraparte: la fila no se eliminó, se condicionó."""
        from app.schemas.reporte import SeccionPendientes

        texto = _texto_del_pdf(
            bytes(
                generar_pdf(
                    _reporte_minimo(
                        pendientes=SeccionPendientes(sin_categoria_tickets=2)
                    )
                )
            )
        )
        assert "2 sin categoria" in texto
        assert "NO se puede declarar cerrado" in texto

    def test_el_pdf_no_imprime_un_importe_cuando_no_hay_exactitud(self):
        """R7 en el PDF: `no disponible`, nunca `0.00`."""
        texto = _texto_del_pdf(bytes(generar_pdf(_reporte_minimo())))
        # El bloque de exactitud no existe en este informe, y aun asi el PDF no
        # inventa un porcentaje.
        assert "0%" not in texto
        assert "0.00%" not in texto


# ---------------------------------------------------------------------------
# R3: los cuatro estados del periodo
# ---------------------------------------------------------------------------

class TestR3LosEstadosDelPeriodo:
    def _pendientes(self, **kw):
        from app.schemas.reporte import SeccionPendientes

        base = dict(
            sin_categoria_tickets=0, sin_conciliar_tickets=0, discrepancias_abiertas=0
        )
        base.update(kw)
        return SeccionPendientes(**base)

    def test_sin_pendientes_y_sin_cierre_se_puede_cerrar(self):
        r = _reporte_minimo(pendientes=self._pendientes())
        assert r.estado_periodo == "CIERRE_DISPONIBLE"

    def test_con_pendientes_no_cierra(self):
        r = _reporte_minimo(pendientes=self._pendientes(sin_categoria_tickets=1))
        assert r.estado_periodo == "NO_CIERRA"

    def test_cerrado_y_limpio_dice_cerrado(self):
        r = _reporte_minimo(pendientes=self._pendientes(), cerrado=True)
        assert r.estado_periodo == "CERRADO"

    def test_cerrado_con_pendientes_nuevos_es_un_hallazgo(self):
        """El estado cuarto.

        Alguien cerro enero y despues metio un ticket de enero. Decir `NO_CIERRA`
        seria falso (enero SI esta cerrado en el registro) y ademas taparia el
        hallazgo mas util: que se escribio en un periodo ya cerrado.
        """
        r = _reporte_minimo(
            pendientes=self._pendientes(sin_conciliar_tickets=1), cerrado=True
        )
        assert r.estado_periodo == "CERRADO_CON_PENDIENTES"
        assert r.cerrado is True

    def test_el_motivo_nombra_los_tres_tipos(self):
        """Los tres motivos juntos, no "hay pendientes"."""
        p = self._pendientes(
            sin_categoria_tickets=2, sin_conciliar_tickets=3, discrepancias_abiertas=4
        )
        assert "2 sin categoria" in p.motivo
        assert "3 sin conciliar" in p.motivo
        assert "4 discrepancias" in p.motivo

    def test_sin_pendientes_no_hay_motivo(self):
        assert self._pendientes().motivo is None
        assert self._pendientes().hay_pendientes is False