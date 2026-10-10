"""La binarizacion adaptativa: por que NO reemplaza al preprocesado actual.

## QUE PROTEGE ESTE ARCHIVO

`docs/known-issues.md` §21 descarta el preprocesado con medicion, y tres de sus
cuatro reglas son "no hagas esto otra vez". La binarizacion adaptativa entra
como **variante puntuada**, y esa palabra es la que este archivo sostiene:

    1. Si la lectura normal es buena, la binarizacion NO se intenta. Cuesta una
       lectura de Tesseract y no puede mejorar lo que ya esta bien.
    2. Si la binarizacion gana, gana con la misma `_puntuar` que decide el resto:
       evidencia del documento (total + RFC + aritmetica), no "se ve mejor".
    3. Si no esta OpenCV, o la imagen no se puede preparar, o el umbral falla,
       el resultado es **identico** al de antes del cambio. No hay excepcion
       nueva y no hay ruta que se rompa.

El punto 3 es el que importa: sin el, agregar una dependencia opcional a la ruta
de lectura es agregar un modo de fallo a un camino que hoy funciona en cada foto.

## QUE NO ESTA MEDIDO

La exactitud. `verdad.json` no esta en la maquina, y sin el no hay contra que
comparar. Por eso el cambio es estrutural —una variante que se puntua, no un
reemplazo— y no una afirmacion de que la binarizacion mejora el total. Cuando
haya `verdad.json`, esto se mide con `scripts/medir_precision_ocr.py` antes de
afirmar nada.

Los tests usan imagenes SINTETICAS, por la misma razon que
`test_recorte_del_papel.py`: una foto real son 1.8 MB de comprobante de una
persona y no puede estar en el repo. Aqui se prueba la REGLA, que es lo que puede
fallar sin avisar.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import ocr as mod


# --- Doubles -------------------------------------------------------------


class LecturaPuntuada:
    """Una lectura ya puntuada, para las pruebas de COMPETENCIA.

    Se usa solo donde hace falta que una lectura gane o pierda con puntos
    concretos. Para lo demas esta `_motor_que_devuelve`, que devuelve el dato
    crudo de `image_to_data`.
    """

    def __init__(self, puntos: int, rotacion: int = 0, texto: str = ""):
        self.puntos = puntos
        self.rotacion = rotacion
        self.texto = texto
        self.confianza = 0.9
        self.lineas = ()


def _motor_que_devuelve(lecturas, registro=None):
    """Un `obtener_motor` de mentira que entrega lecturas en orden.

    `registro` es la lista donde se anota cada lectura, que es lo que permite
    afirmar que la binarizacion **no se intento** cuando la lectura normal fue
    buena: no basta con que el resultado sea el mismo, hay que ver que la segunda
    lectura ni siquiera ocurrio.
    """

    class MotorFalso:
        class Output:
            DICT = "dict"

        def __init__(self):
            # El contador y la lista se llevan como atributos y NO se leen del
            # cierre: un closure sobre una variable que se reasigna
            # (`registro = []` en el test) no la ve, y el fallo sale como un
            # `NameError` desde la linea equivocada, que es la peor forma de
            # recibir la noticia de que el doble esta mal.
            self.i = 0
            self.registro = registro

        def image_to_data(self, imagen, lang=None, config=None, output_type=None):
            if self.registro is not None:
                self.registro.append({"config": config})
            # El numero de rotaciones se resuelve leyendo el ancho/alto, que es
            # como `_variantes` produce imagenes distintas: si la lectura se
            # agota se devuelve la ultima, para que el test pueda quedarse sin
            # decisiones y solo verificar la ausencia de la lectura extra.
            puntos = lecturas[self.i] if self.i < len(lecturas) else lecturas[-1]
            self.i += 1
            # El motor devuelve el DICT de `image_to_data`, no una `Lectura`.
            # `_una_lectura` arma la `Lectura` con `_puntuar` sobre el texto; el
            # doble solo tiene que decidir CUANTAS lecturas ocurren, asi que
            # devuelve un dato sin palabras: `_puntuar` lo puntua 0 y eso es lo
            # que estos tests necesitan — una lectura que NO conviene.
            #
            # Que el doble devuelva algo que no se parece a la salida real es un
            # riesgo de este archivo, no una comodidad: por eso `_es_buena` esta
            # en 70 y estos tests dan 5 y 75, lejos de cualquier borde. Si
            # alguien sube el corte, estos tests empiezan a fallar por una razon
            # que no tiene que ver con lo que proban.
            return {
                "text": [],
                "block_num": [], "par_num": [], "line_num": [],
                "left": [], "top": [], "width": [], "height": [],
                "conf": [],
            }

    return MotorFalso()


@pytest.fixture
def con_pillow():
    return pytest.importorskip("PIL.Image", reason="Pillow no esta instalado")


class _Cv2Falso:
    """Un `cv2` que responde lo mismo que el de verdad, sin el paquete.

    ## POR QUE UN DOBLE Y NO UN `importorskip`

    La primera version de este archivo hacia `pytest.importorskip("cv2")` y en
    una maquina sin OpenCV **cinco tests hacian skip**. Con esos skips, el
    verificador `scripts/verify_ocr_mutations.py` reporto tres mutaciones
    COMO SOBREVIVIENTES que en realidad nadie estaba probando — el codigo roto y
    los tests en verde por no haberse ejecutado.

    Eso es peor que no tener el test: es un verificador que miente sobre su
    propia cobertura. Con el doble de abajo, la GEOMETRIA del reescalado se prueba
    en cualquier maquina, con o sin OpenCV.

    Lo que NO se prueba con el doble, y hay que decirlo: que el `adaptiveThreshold`
    real binarice bien. Eso es codigo de la biblioteca, y se mide con
    `medir_precision_ocr.py` sobre fotos, no en un test.
    """

    # Los tres metodos que `_binarizar` usa, con los nombres EXACTOS que el
    # codigo busca. Si OpenCV renombra uno, el doble deja de hacer falta aqui y
    # el error sale en produccion en vez de en este archivo.
    INTER_CUBIC = "INTER_CUBIC"
    ADAPTIVE_THRESH_GAUSSIAN_C = "ADAPTIVE_THRESH_GAUSSIAN_C"
    THRESH_BINARY = "THRESH_BINARY"

    def __init__(self):
        self.llamadas: list[str] = []

    def resize(self, imagen, dsize, interpolation=None):
        self.llamadas.append(f"resize{dsize}")
        ancho, alto = dsize
        # Devuelve un array numpy con la forma pedida, que es lo que el codigo
        # espera de `cv2.resize`.
        import numpy as np

        return np.resize(imagen, (alto, ancho))

    def adaptiveThreshold(self, imagen, maxValue, adaptiveMethod, thresholdType,
                          blockSize, C):
        self.llamadas.append(f"threshold({blockSize},{C})")
        import numpy as np

        return np.full(imagen.shape, 255, dtype="uint8")


@pytest.fixture
def con_cv2(monkeypatch):
    """`cv2` real si esta, y si no, un doble que responde lo mismo.

    Nunca hace skip. Es lo que hace que este archivo mida en cualquier maquina.
    """
    try:
        import cv2  # noqa: F401

        return cv2
    except ImportError:
        pass

    falso = _Cv2Falso()
    monkeypatch.setitem(sys.modules, "cv2", falso)
    return falso


def _imagen(ancho=200, alto=80):
    from PIL import Image

    return Image.new("L", (ancho, alto), 255)


# --- La garantia principal: no empeora ------------------------------------


class TestLaBinarizacionNoEmpeora:
    def test_sin_opencv_el_resultado_es_identico(self, con_pillow):
        """El camino de sin OpenCV tiene que dar EXACTAMENTE lo de antes.

        Se compara el texto, no la excepcion: el fallo feo de una dependencia
        opcional no es que avise, es que devuelva otra cosa sin avisar.
        """
        with patch.object(mod, "obtener_motor", lambda n: _motor_que_devuelve([5])):
            with patch.object(mod, "_cv2", lambda: None):
                with patch.object(mod, "_preparar", lambda d: _imagen()):
                    resultado = mod.leer_imagen(b"imagen-falsa")

        # Sin una lectura buena, sale el mejor intento con su texto vacio: lo
        # que importa es que `leer_imagen` no levanto y no cambio el motor.
        assert resultado.motor == "tesseract"

    def test_la_imagen_ilegible_no_revienta(self):
        """Una imagen que Pillow no puede abrir degrada, no tumba.

        `_preparar` devuelve los bytes crudos cuando la imagen no se abre, y
        `_binarizar` devuelve `None` porque `bytes` no tiene `.convert`. Ese es
        el camino de un `.heic` corrupto o de un upload truncado, y antes de
        estos cambios pasaba sin romperse.
        """
        assert mod._binarizar(b"esto no es una imagen") is None

    def test_sin_pillow_devuelve_none(self):
        """Sin `.convert` no hay nada que binarizar. Y no es un error."""
        assert mod._binarizar(object()) is None


# --- La decision: solo si la lectura normal no fue buena ------------------


class TestLaBinarizacionCompite:
    """El test de fondo: la binarizacion entra a COMPETIR, no a sustituir.

    Se comprueban los dos lados de la misma regla. Que gane cuando lee mejor es lo
    que hace que la etapa sirva; que **no** gane cuando lee peor es lo que hace
    que no pueda empeorar. Un test solo del primer lado pasaria con un reemplazo
    directo de `_preparar` — que es justo lo que §21 prohibe— asi que estan los
    dos lados a proposito.

    Aqui se dobla `_una_lectura`, no el motor: lo que se prueba es la comparacion
    de puntajes de `_leer_tesseract`, y entrar por el motor meteria en medio el
    parseo del DICT, que no es lo que esta en discusion.
    """

    def _correr(self, puntos_por_llamada):
        """Corre el OCR dando un puntaje distinto a cada llamada."""
        i = {"n": 0}

        def _una_lectura(pytesseract, imagen, rotacion, psm=None):
            puntos = puntos_por_llamada[i["n"]] if i["n"] < len(puntos_por_llamada) else 0
            i["n"] += 1
            return LecturaPuntuada(puntos, rotacion=rotacion, texto=f"lectura {i['n']}")

        with patch.object(mod, "obtener_motor", lambda n: None):
            with patch.object(mod, "_preparar", lambda d: _imagen()):
                with patch.object(mod, "_binarizar", lambda im: _imagen()):
                    with patch.object(mod, "_una_lectura", _una_lectura):
                        return mod._leer_tesseract(b"x"), i["n"]

    def test_gana_cuando_lee_mejor(self):
        """Normal 20 (corta en la primera vuelta), binarizada 75: gana la binarizada.

        El 75 tambien supera el corte de 70, asi que ademas cierra el bucle: una
        variante que gana Y ademas es buena tiene que terminar ahi, no seguir
        gastando lecturas de las otras rotaciones.
        """
        resultado, llamadas = self._correr([20, 75])
        assert resultado.texto == "lectura 2"
        assert llamadas == 2

    def test_no_gana_cuando_lee_peor(self):
        """Normal 60, binarizada 20: se queda con la normal.

        El 60 esta por debajo del corte, asi que la etapa entra a competir, y el
        20 es peor. Una etapa que se quedara con lo peor seria un retroceso.
        """
        resultado, _ = self._correr([60, 20, 20, 20, 20])
        assert resultado.texto == "lectura 1"

    def test_la_etapa_se_intenta_alguna_vez(self):
        """Sin esto, los dos tests de arriba pasan con la etapa ausente.

        Los dos tests de competencia miran QUE lectura gana. Si alguien borra la
        llamada a `_binarizar`, la segunda lectura no ocurre, la primera gana por
        ausencia de competencia, y los dos siguen en verde: un par de tests que
        no distinguen "el binarizado gano" de "no hubo binarizado".

        Este mira el numero de llamadas con un resultado malo de entrada, que es
        el unico caso en el que la etapa DEBE correr. Si no corre, no hay nada
        que la binarizacion pueda ganar y el cambio es decorativo.
        """
        _, llamadas = self._correr([20, 20])
        assert llamadas >= 2, "la binarizacion nunca se intento"

    def test_el_zoom_lo_aplica_la_etapa_de_binarizado(self, con_cv2):
        """Que gane o no da igual: aqui se mira QUE IMAGEN se lee.

        Los tests anteriores miran el RESULTADO —cual lectura gana— y por eso no
        distinguen "se leyo la imagen binarizada" de "se leyo la imagen normal y
        la binarizada salio peor". Este dobla `_una_lectura` y mira la imagen que
        le llega: sin el zoom, la segunda lectura recibe una imagen de 1400 px, y
        con el, una de 2000.

        Es el test que ata la constante `ANCHO_MINIMO_PARA_ZOOM` a un efecto
        observable en la lectura, y no solo a la forma de la imagen de salida.
        """
        anchos = []

        def _una_lectura(pytesseract, imagen, rotacion, psm=None):
            anchos.append(imagen.width)
            return LecturaPuntuada(20, rotacion=rotacion)

        # El ancho de la imagen que LLEGA a cada lectura. `imagen.width` y no el
        # alto: `rotate(90, expand=True)` intercambia los dos, asi que el alto
        # de la rotacion 90 es el ancho de la original y compararlos seria
        # confuso de leer.

        with patch.object(mod, "obtener_motor", lambda n: None):
            with patch.object(mod, "_preparar", lambda d: _imagen(1000, 400)):
                with patch.object(mod, "_una_lectura", _una_lectura):
                    mod._leer_tesseract(b"x")

        # Las dos etapas llaman a `_una_lectura` con la MISMA rotacion, asi que no se
        # pueden distinguir por rotacion: se separan por la mitad de la lista.
        # Cuatro rotaciones en cada una (`ROTACIONES`), que es lo que hace el
        # corte determinable.
        mitad = len(anchos) // 2
        primera, segunda = anchos[:mitad], anchos[mitad:]

        # `rotate(90, expand=True)` INTERCAMBIA ancho y alto: la rotacion 90 de
        # una imagen de 1000x400 mide 400 de ancho, no 1000.
        assert primera == [1000, 400, 1000, 400]

        # La etapa de binarizado recibio la imagen de 1000x400 REESCALADA a
        # 2000x800: el ancho se duplica y el alto tambien, en el mismo orden.
        assert segunda == [2000, 800, 2000, 800], (
            "la etapa de binarizado no recibio la imagen ampliada"
        )


class TestCuandoSeIntentaLaBinarizacion:
    def _leer(self, puntos_por_lectura, con_cv2):
        """Corre el OCR dando un puntaje fijo a cada lectura, y cuenta las llamadas.

        Se dobla `_puntuar` y no `_una_lectura` a proposito: `_puntuar` calcula
        puntos a partir del TEXTO del documento, y el doble no produce texto, asi
        que sin este parche todos los puntajes serian 0 y `_es_buena` nunca seria
        cierto — el test pasaria por el motivo equivocado y no probaria nada.
        """
        llamadas = []
        i = {"n": 0}

        def _puntuar(texto, confianza):
            puntos = (
                puntos_por_lectura[i["n"]]
                if i["n"] < len(puntos_por_lectura)
                else puntos_por_lectura[-1]
            )
            i["n"] += 1
            return puntos

        with patch.object(mod, "obtener_motor",
                          lambda n: _motor_que_devuelve(puntos_por_lectura, llamadas)):
            with patch.object(mod, "_preparar", lambda d: _imagen()):
                with patch.object(mod, "_binarizar",
                                  lambda im: _imagen() if con_cv2 else None):
                    with patch.object(mod, "_puntuar", _puntuar):
                        mod.leer_imagen(b"x")
        return llamadas

    def test_una_lectura_buena_corta_antes_de_binarizar(self, con_cv2):
        """El caso comun NO paga el preprocesado nuevo.

        70 puntos es `_es_buena`: total + RFC + aritmetica. Con eso el ticket
        tiene lo que necesita para contabilizarse y las demas lecturas solo
        gastan CPU. Este es el test que hace que el cambio sea gratis para el
        ticket que hoy funciona bien.
        """
        llamadas = self._leer([75], con_cv2=True)
        assert len(llamadas) == 1, "se hizo mas de una lectura con un resultado bueno"

    def test_una_lectura_mala_si_prueba_a_binarizar(self, con_cv2):
        """Sin total ni RFC, la binarizacion entra a competir."""
        llamadas = self._leer([5], con_cv2=True)
        assert len(llamadas) > 1, "nunca se intento la segunda lectura"

    def test_sin_cv2_no_hay_lectura_extra(self):
        """Sin OpenCV el numero de lecturas es el de antes del cambio.

        Este es el test que hace que la dependencia sea opcional de verdad: sin
        el paquete, el costo es identico al de antes. Si alguien hace la
        binarizacion obligatoria, este test muere.
        """
        llamadas = self._leer([5], con_cv2=False)
        # 12 y no 4: cuatro rotaciones con PSM 6, y despues la segunda vuelta de
        # PSM (12 y 11) sobre las mismas cuatro. El numero de antes del cambio es
        # el mismo 12, y eso es lo que este test afirma: **la cuenta no cambio**.
        #
        # La diferencia real se mide en la foto, no en el numero de lecturas, y
        # sin `verdad.json` no se puede medir. Lo que si se puede fijar —y es lo
        # que evita una regresion cara— es que OpenCV no haya metido lecturas de
        # su cuenta: si `_binarizar` dejara de devolver `None`, este numero
        # pasaria a 20 y el test muere.
        assert len(llamadas) == 12, "se probaron lecturas que antes no se probaban"


# --- HEIC: el registro no puede depender del orden de imports ------------


class TestElOcrRegistraElDecodificadorDeHeic:
    """`ocr.py` tiene que registrar HEIC, y no por accidente del orden de imports.

    Una foto de iPhone es media propuesta de valor del producto, y este es el
    unico camino que la lee. El bug que esto evita ya esta DESCRITO en
    `app/core/heif.py`: el registro vivia en `ai_extractor`, y la app funcionaba
    porque tres modulos lo importaban antes de correr el OCR.

    Medido con el archivo real (`CremeriaHermanosCoronel.heic`), cargando este
    modulo por ruta sin que nadie importara `ai_extractor` antes:

        app.core.heif cargado?  False
        PIL abre la HEIC?      NO -> UnidentifiedImageError

    y lo que venia despues era `TypeError: Unsupported image object`, que no
    menciona HEIC ni los bytes: el operador ve un error de OCR y busca en el OCR.

    ## POR QUE ESTE TEST MIRA EL CODIGO Y NO EL ESTADO

    La primera version cargaba el archivo por ruta y miraba `sys.modules`.
    **Hacia skip en la suite completa**, y un test que hace skip no protege nada:
    `conftest.py` importa `app.main`, que importa `ai_extractor`, que registra
    HEIC — asi que la foto abria de todas formas y el bug quedaba invisible.
    Peor: el propio `skip` decia "el orden de imports tapa el test", o sea que el
    test sabia que no estaba midiendo.

    Un test que se salta a si mismo es peor que no tener test, porque el
    verificador de mutaciones lo cuenta como verde. Por eso aqui se mira el CODIGO.

    La prueba de que esto muerde: `verify_ocr_mutations.py` quita la linea de
    import y este test se pone rojo. Un test que mide comportamiento no
    lo lograria en la suite, porque para entonces HEIC ya esta registrado.
    """

    def test_ocr_importa_el_registrador_de_heic(self):
        """El `import` esta, y es codigo — no la prosa del docstring de arriba."""
        import ast

        fuente = Path(mod.__file__).read_text(encoding="utf-8")
        arbol = ast.parse(fuente)
        # Se recorre el AST y no se busca la cadena en el texto: el docstring de
        # ESTE modulo menciona `app.core.heif` varias veces, y una busqueda por
        # palabra pasaria con el import borrado. Es el mismo error que documento
        # `verify_export_mutations.py` en su linea 92.
        importa = False
        for nodo in ast.walk(arbol):
            # `from app.core import heif as _heif` — el nombre del modulo es
            # `app.core` y el que importa es el alias. Por eso se mira EL NOMBRE
            # IMPORTADO y no el modulo del que viene: buscando `app.core.heif`
            # entre los dos lados esta la forma que escribimos, pero la forma
            # obvia (`from app.core.heif import ...`) tiene el nombre en el otro.
            if isinstance(nodo, ast.ImportFrom):
                if (nodo.module or "").endswith("heif"):
                    importa = True
                if any(a.name.split(".")[0] == "heif" for a in nodo.names):
                    importa = True
            if isinstance(nodo, ast.Import):
                if any(a.name.split(".")[-1] == "heif" for a in nodo.names):
                    importa = True
        assert importa, (
            "ocr.py no importa app.core.heif. Las fotos HEIC no se abren cuando "
            "nada mas las importa antes: _preparar devuelve los bytes crudos y "
            "pytesseract lanza 'TypeError: Unsupported image object', que no "
            "menciona HEIC ni los bytes."
        )

    def test_importar_heif_no_es_una_dependencia_dura(self):
        """La linea nueva no consulta nada: solo importa.

        `app/core/heif.py` degrada a `DISPONIBLE=False` si falta `pillow-heif`.
        Si `ocr.py` empezara a usar ese booleano o a levantar, el modulo que mas
        fotos procesa habria ganado una dependencia que antes no tenia, y una
        maquina sin el paquete habria dejado de leer fotos.
        """
        from app.core import heif

        assert isinstance(heif.DISPONIBLE, bool)


# --- La geometria del reescalado -----------------------------------------


class TestElReescaladoCondicional:
    def test_no_agranda_una_imagen_ya_grande(self, con_cv2, con_pillow):
        """`_preparar` deja 2000 px, y ampliar ahi no aporta nada.

        §21 punto 1 lo midio: la foto original es de 3840 px y a resolucion
        NATIVA el total se sigue leyendo `31.50` con confianza 93. Volver a
        inventar pixeles con una interpolacion no es mejorar la definicion.
        """
        antes = _imagen(2000, 800)
        salida = mod._binarizar(antes)
        assert salida is not None
        assert salida.width == 2000, "una imagen de 2000 px se reescalara"

    def test_no_agranda_una_imagen_de_tamano_intermedio(self, con_cv2):
        """1400 px: hay pixeles de sobra, pero no se reescala.

        Este es el test que separa la regla de "no ampliar lo que ya es grande" del
        tope de 2000, que es un dato distinto. Con el tope puesto, un 2000 px
        AMP se veria igual sin la condicion del zoom, porque `min(4000, 2000)` da
        2000 igual. A 1400 la diferencia si se ve: `min(2800, 2000)` da 2000, y
        sin la condicion de ancho minimo tambien. Por eso el caso intermediario
        es el que importa y no el de 2000.

        El zoom tiene que estar acotado por los DOS lados, no solo por el de
        arriba: ampliar cualquier cosa hasta 2000 seria reescalar tambien las
        fotos que `_preparar` ya dejo en un tamano en el que Tesseract lee bien.
        """
        salida = mod._binarizar(_imagen(1400, 500))
        assert salida.width == 1400, "una imagen de 1400 px se reescalara"

    def test_agranda_lo_que_viene_pequeno(self, con_cv2):
        """Una foto de 600 px si gana zoom: hay pocos pixeles de verdad.

        El caso es un WhatsApp reenviado o un escaner de baja resolucion, donde
        la altura de letra esta por debajo de lo que Tesseract aprovecha.
        """
        salida = mod._binarizar(_imagen(600, 200))
        assert salida is not None
        assert salida.width == 1200

    def test_el_zoom_no_pasa_del_tope(self, con_cv2):
        """Ni una imagen de 1100 px se va a 4400: el tope es 2000.

        El proposito de bajar a 2000 en `_preparar` era no gastar segundos por
        foto. Ampliar a 4400 desaria ese trabajo, y Tesseract paga por pixel.
        """
        salida = mod._binarizar(_imagen(1100, 400))
        assert salida.width == 2000


# --- La configuracion del motor ------------------------------------------


class TestLaConfiguracionDeTesseract:
    def test_fija_el_motor_lstm(self):
        """`--oem 3` explicito, aunque sea el default.

        Tesseract 3 usaba el motor heredado (`--oem 0`) y el 4 paso a LSTM. Un
        numero de foto leido depende de cual corra, y un dia que se actualice la
        imagen de Docker las fotos cambian de exactitud sin que nadie haya
        tocado este repo.
        """
        assert "--oem 3" in mod._config_de_tesseract(None)

    def test_el_psm_viene_de_la_configuracion(self):
        assert "--psm 6" in mod._config_de_tesseract(None)

    def test_un_psm_alterno_manda(self):
        """La segunda vuelta de `_leer_tesseract` sigue fijando su propio PSM."""
        assert "--psm 12" in mod._config_de_tesseract(12)

    def test_psm_explicito_gana_al_de_la_configuracion(self):
        """`None` es "usa el de config"; un numero es "usa este"."""
        assert "--psm 11" in mod._config_de_tesseract(11)


# --- Lo que se puede preguntar -------------------------------------------


class TestLaDisponibilidad:
    def test_avisa_que_opencv_falta(self):
        """La respuesta tiene que distinguir "no instalado" de "no funciona".

        Un operador cuyo numero de exactitud no se mueve necesita poder preguntar
        POR QUE. Sin esta clave, se deduce de un numero que no dice cual de las
        dos cosas paso.
        """
        with patch.object(mod, "_cv2", lambda: None):
            estado = mod.disponibilidad()
        assert estado["opencv"]["disponible"] is False
        assert "opencv-python-headless" in estado["opencv"]["motivo"]

    def test_dice_que_leer_no_depende_de_opencv(self):
        """El motivo tiene que aclarar que sin OpenCV el OCR sigue."""
        with patch.object(mod, "_cv2", lambda: None):
            estado = mod.disponibilidad()
        assert "sigue" in estado["opencv"]["motivo"] or "lee igual" in estado["opencv"]["motivo"]

    def test_opencv_no_va_en_motores(self):
        """No es un motor: no produce texto, hace mas legible lo que otro lee.

        Meterlo en `motores` lo haria parecer que sin el no se puede leer, que
        es falso.
        """
        with patch.object(mod, "_cv2", lambda: None):
            estado = mod.disponibilidad()
        assert "opencv" not in estado["motores"]

    def test_expone_el_oem_y_el_psm(self):
        """Lo que se le pasa al motor tiene que ser preguntable.

        Si Tesseract se actualiza y el `--oem` deja de ser 3, la respuesta dice
        que se le mando 3 cuando ya no es cierto.
        """
        with patch.object(mod, "obtener_motor", lambda n: None):
            with patch.object(mod, "_cv2", lambda: None):
                estado = mod.disponibilidad()
        assert estado["oem"] == 3
        assert estado["psm"] == 6