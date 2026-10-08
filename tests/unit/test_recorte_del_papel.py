"""El recorte del papel antes del OCR.

## POR QUE ESTE ARCHIVO EXISTE

Medido sobre `IMG_4316.HEIC`, una foto real: el comprobante ocupa el 46% del
cuadro y el resto es la pantalla de un editor. Tesseract lee las dos superficies
y el parser agarra la linea que reconoce. El total salia `1.50` —un fragmento de
`51.50` que cayo en la linea siguiente— cuando el papel decia `51.50`.

`1.50` es peor que `0.00`, y esa es la razon de que esto sea urgente: con cero,
el gate emite `total_not_positive` y hay una razon para desconfiar. Con un
positivo, ese check no dice nada, el gasto queda subrereportado y nadie busca los
gastos que faltan.

Con el recorte, el mismo archivo da `51.50`.

## LA GUARDA, Y POR QUE ESTE ARCHIVO LA PROTEGE

El recorte por brillo tiene un fallo obvio: una foto OSCURA de un papel claro
deja poca region por encima del umbral, la caja sale diminuta y se recorta el
ticket en vez del fondo — o sea, se empeora justo el caso que se queria
arreglar.

Por eso, si la region clara cubre menos de `_FRACCION_MINIMA` del area, NO se
recorta. Sin esa guarda este cambio arregla una foto y rompe otras, y nadie lo
notaria porque las otras ya salian mal.

Los tests de abajo usan imagenes SINTETICAS y no fotos reales a proposito: una
foto real en el repo son 1.8 MB de comprobante de una persona, y un test que
necesite un disco con imagenes Esas se puede ejecutar ni se puede correr en CI.
La foto real esta en el papel; aqui se prueba la REGLA, que es lo que puede
fallar.
"""

import io

import pytest

from app.services import ocr as mod


def _png(imagen) -> bytes:
    buffer = io.BytesIO()
    imagen.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def con_pillow():
    Image = pytest.importorskip("PIL.Image", reason="Pillow no esta instalado")
    return Image


@pytest.fixture
def numpy():
    return pytest.importorskip("numpy")


class TestLaGuardaDelRecorte:
    """Cada test aqui muere si se quita `_FRACCION_MINIMA`."""

    def test_una_foto_oscura_no_se_recorta_al_papel(self, con_pillow, numpy):
        """Fondo oscuro, un papel CHICO dentro: no se recorta.

        Es el caso que la guarda existe para proteger. Sin ella, la caja sale del
        papel y la imagen se empeora — que es peor que no hacer nada, porque
        parece que se probo.
        """
        Image = con_pillow
        imagen = Image.new("L", (1000, 1000), 30)  # fondo oscuro
        # Un papel claro de 200x200 = 4% del area, muy por debajo del 15%.
        for x in range(400, 600):
            for y in range(400, 600):
                imagen.putpixel((x, y), 220)

        recortada, fraccion = mod._recortar_el_papel(imagen)

        assert recortada.size == imagen.size, (
            "una region clara del 4% NO debe disparar el recorte: "
            f"salio {recortada.size} de {imagen.size}"
        )
        assert fraccion < mod._FRACCION_MINIMA

    def test_una_imagen_oscura_que_no_tiene_papel_se_deja_igual(self, con_pillow):
        """Sin region clara, se devuelve la imagen sin tocar.

        Es el caso degenerado: no hay ningun `np.where` que devolver, y un
        `min()` sobre un array vacio revienta. Sin esto, una foto totalmente
        oscura tumba la lectura entera en vez de devolver los bytes.
        """
        Image = con_pillow
        imagen = Image.new("L", (400, 400), 10)

        recortada, fraccion = mod._recortar_el_papel(imagen)

        assert recortada.size == imagen.size
        assert fraccion == 0.0

    def test_una_caja_angosta_no_es_una_foto(self, con_pillow, numpy):
        """Una franja alta y angosta NO se recorta, aunque sea grande en area.

        ## POR QUE ESTE CASO ES EL DIFICIL, Y POR QUE NO PUEDE SER OTRO

        La primera version de este test dibujo UNA fila de pixeles claros. Es el
        caso que se le ocurre a cualquiera, y no probaba NADA de esta guarda: la
        fila da una caja de area ~0, la atrapa `_FRACCION_MINIMA`, y la guarda de
        tamano nunca llega a ejecutarse. Al correr la mutacion —quitando el
        `width < 50 or height < 50`— los tests siguieron en verde.

        O sea: la guarda era decorativa, y por el mismo motivo por el que este
        repo no acepta defensas decorativas.

        Para alcanzarla hacen falta las DOS dimensiones: un area que pase el 15%
        y aun asi una dimension menor de 50 px. Con los quantiles al 2%, eso
        exige que los pixeles claros se concentren en menos del 2% del ancho —
        o sea, una franja vertical— y que la imagen seaestrecha para que el area
        siga siendo grande. Medido: en una imagen de 200x800, una franja de 45
        columnas por 729 filas da 20.5% de area con 45 px de ancho.

        Si un dia se sube `_FRACCION_MINIMA` por encima de 0.205, este test
        dejaria de alcanzar la guarda y volveria a ser decorativo. Por eso
        afirma las dos condiciones por separado y no solo el resultado.
        """
        Image = con_pillow
        imagen = Image.new("L", (200, 800), 40)  # fondo oscuro, imagen ESTRECHA
        for x in range(70, 115):  # 45 columnas: la franja angosta
            for y in range(20, 749):  # 729 filas: casi toda la altura
                imagen.putpixel((x, y), 230)

        recortada, fraccion = mod._recortar_el_papel(imagen)

        # Las DOS condiciones, para que el test siga siendo valido aunque el
        # umbral cambie: sin esto, dejaria de probar lo que dice probar.
        assert fraccion >= mod._FRACCION_MINIMA, (
            f"el caso de este test dio {fraccion:.1%}, que NO pasa el umbral "
            f"de {mod._FRACCION_MINIMA:.0%}: la guarda de tamano no se ejercita"
        )
        # Y aun asi no se recorta, porque la caja habria sido de 45 px de ancho.
        assert recortada.size == imagen.size, (
            f"una franja de {fraccion:.0%} del area pero angosta produjo una "
            f"caja de {recortada.size}; Tesseract no puede leer eso"
        )


    def test_una_foto_que_ya_casi_es_el_papel_no_se_recorta(
        self, con_pillow, numpy
    ):
        """Si el papel ya llena el cuadro, recortar NO se toca nada.

        ## LA SEGUNDA GUARDA, Y POR QUE NO PUDO OLVIDARME DE ELLA

        La primera version solo tenia `_FRACCION_MINIMA`. Medido sobre las siete
        fotos reales, con y sin recorte:

            IMG_4316   papel al 46%   0.00 -> 51.50    GANA
            AA4D0E8F   papel al 90%   97.56 -> 0.00   PIERDE
            EE2C6866   papel al 85%   117 -> 0.00     PIERDE

        Arreglaba uno y rompia dos. La causa es que los quantiles al 2% recortan
        un 8-15% del area SIEMPRE, y cuando el papel ya llena el cuadro ese
        8-15% no es fondo: es el borde del comprobante, donde vive el importe de
        una linea de detalle.

        Y solo se vio al medir los SIETE. Con los tres de `IMG_*` el resultado
        era "1 gana, 0 pierde" y el cambio habria entrado arrastrando dos
        comprobantes correctos.
        """
        Image = con_pillow
        # Oscuro en el borde: el recorte de los quantiles MORDERIA ese borde.
        # El papel llega al 85.6% del area (370x370 de 400x400), que pasa el piso
        # del 15% y tiene que chocar con el techo del 75%.
        imagen = Image.new("L", (400, 400), 40)
        for x in range(15, 385):
            for y in range(15, 385):
                imagen.putpixel((x, y), 225)

        recortada, fraccion = mod._recortar_el_papel(imagen)

        assert fraccion > mod._FRACCION_MINIMA, (
            f"el caso de este test dio {fraccion:.1%}, que no pasa el piso; "
            "la guarda de techo no se ejercita"
        )
        assert recortada.size == imagen.size, (
            f"con el papel al {fraccion:.0%} del cuadro se recortó a "
            f"{recortada.size}: se están comiendo el borde del comprobante"
        )

    def test_la_fraccion_maxima_es_un_techo_y_no_un_piso(self):
        """El techo tiene que estar MAS ARRIBA que el piso.

        Trivial, pero no: si alguien los intercambia por error, el recorte solo
        pasaria en el rango [75%, 15%], que es vacio, y la funcion devolveria la
        imagen siempre. Los tests de arriba seguirian en verde — el recorte no
        hace nada, pero no rompe — y el cambio pasaria inadvertido.
        """
        assert mod._FRACCION_MINIMA < mod._FRACCION_MAXIMA, (
            f"el rango [{mod._FRACCION_MINIMA}, {mod._FRACCION_MAXIMA}] esta vacio: "
            "el recorte nunca ocurriria y nadie lo notaria"
        )


class TestElRecorteCuandoSiAplica:
    def test_un_ticket_en_medio_de_la_foto_se_recorta(
        self, con_pillow, numpy
    ):
        """El caso que motiva el cambio: papel pequeno en un cuadro grande."""
        Image = con_pillow
        imagen = Image.new("L", (1000, 1400), 40)  # fondo oscuro de escritorio
        for x in range(200, 800):  # 600 de 1000 = 60% del ancho
            for y in range(200, 1200):  # 1000 de 1400 = 71% del alto
                imagen.putpixel((x, y), 225)

        recortada, fraccion = mod._recortar_el_papel(imagen)

        assert recortada.size != imagen.size, "el recorte no ocurrio"
        assert fraccion > mod._FRACCION_MINIMA
        # La caja debe estar dentro de la foto, nunca fuera.
        assert 0 <= recortada.width <= imagen.width
        assert 0 <= recortada.height <= imagen.height

    def test_una_foto_que_ya_es_el_ticket_no_se_toca(self, con_pillow):
        """Si el papel llena el cuadro, el recorte casi no hace nada.

        Es lo que protege a los comprobantes que ya funcionan: el recorte no
        puede empeorar lo que estaba bien, porque cuando la region clara es casi
        toda la imagen, la caja es casi la imagen.
        """
        Image = con_pillow
        imagen = Image.new("L", (900, 900), 210)  # todo claro

        recortada, fraccion = mod._recortar_el_papel(imagen)

        # Con todo claro, los quantiles dan ~la imagen entera.
        assert recortada.width >= imagen.width * 0.9
        assert recortada.height >= imagen.height * 0.9
        assert fraccion > 0.8


class Test_prepararUsaElRecorte:
    def test_preparar_pasa_por_el_recorte(self, con_pillow, monkeypatch):
        """`_preparar` tiene que LLAMAR al recorte, no solo existir.

        Sin esta prueba, `_recortar_el_papel` podria quedarse huerfana — con sus
        tests en verde y sin efecto en la lectura. Se comprobo que ese es el modo
        de falla que importa: una defensa con tests propios que nadie conecta.
        """
        Image = con_pillow
        llamadas = []
        original = mod._recortar_el_papel

        defmarked = lambda imagen: (llamadas.append(1), original(imagen))[1]
        monkeypatch.setattr(mod, "_recortar_el_papel", defmarked)

        mod._preparar(_png(Image.new("L", (400, 400), 200)))

        assert llamadas, "_preparar no llamo a _recortar_el_papel"

    def test_preparar_sigue_devolviendo_una_imagen(self, con_pillow):
        """El recorte no puede cambiar el CONTRATO de `_preparar`.

        Devuelve algo que `pytesseract` sepa leer. Si el recorte devolviera un
        array de numpy en vez de una imagen de Pillow, todo lo de abajo pasaria
        en los tests unitarios y fallaria en la primera foto real.
        """
        Image = con_pillow
        resultado = mod._preparar(_png(Image.new("L", (600, 400), 190)))

        assert isinstance(resultado, Image.Image), (
            f"_preparar devolvio {type(resultado).__name__}, no una imagen"
        )
        assert resultado.mode == "L", f"volvio en modo {resultado.mode}, no en gris"
        assert resultado.width <= 2000, "no se respeta el ancho maximo de 2000"

    def test_un_archivo_que_no_es_imagen_devuelve_los_bytes(self):
        """Un `.txt` o un PDF no se recorta: se devuelven tal cual.

        La cascada usa `_preparar` tambien en rutas que no son foto, y una
        excepcion ahi seria un 500 en vez de una lectura.
        """
        resultado = mod._preparar(b"esto no es una imagen, es texto plano")

        assert isinstance(resultado, (bytes, bytearray))
        assert resultado == b"esto no es una imagen, es texto plano"
