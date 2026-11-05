"""La medicion tiene que MEDIR lo que dice que mide.

**POR QUE UN ARCHIVO DE TESTS PARA UN SCRIPT QUE NO ES TEST.**
`scripts/medir_precision_ocr.py` esta fuera de `tests/` a proposito: necesita
Tesseract, tarda ~30 s por foto y lee un archivo de verdad de una maquina
concreta. Un test que dependa de eso deja de ser un test. Eso vale para la
lectura con OCR dentro, y **no** vale para decidir **a que fotos se mide**.

## EL DEFECTO, MEDIDO EL 2026-10-15

El archivo de verdad de esta maquina nombra `IMG_4398.HEIC` y el archivo en disco
se llama `IMG_4398.heic`. El filtro era `f.name in verdad`, comparacion exacta, y
esa foto **no se media y no se decia nada**: el script reportaba los otros dos
con la misma seguridad, y el porcentaje salia igual de presentable.

Es el peor modo de fallo posible en un instrumento de medir: no es un numero
malo, es **menos datos de los que creias**. Y la foto que se perdio era
justo la del defecto que se estaba mirando (`total 5.00` donde el papel decia
`193.65`), o sea que la medicion no era inutil: era peor, porque REPORTABA sin
la evidencia que lo hacia rebatible.

La extension ya se comparaba en minusculas (`f.suffix.lower()`): la mitad del
emparejamiento era tolerante y la otra no, y por eso el mismo archivo entraba
con una forma y salia con la otra.

## LO QUE ESTA AQUI

No el OCR ni el parser: **la seleccion**. Que es pura, no necesita Tesseract, y
es la parte que por su silencio podia mentir.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
GENERADOR = RAIZ / "scripts" / "medir_precision_ocr.py"


@pytest.fixture(scope="module")
def medir():
    """El script importado como modulo, sin ejecutarlo."""
    spec = importlib.util.spec_from_file_location("medir_precision_ocr", GENERADOR)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _falsa(nombre: str) -> Path:
    """Un archivo vacio con la extension que le toca. El script de verdad mira
    los BYTES; esta funcion es para la SELECCION, que solo mira el nombre."""
    return Path(nombre)


@pytest.fixture
def carpeta(tmp_path) -> Path:
    d = tmp_path / "fotos"
    d.mkdir()
    for nombre in ("IMG_4407.heic", "IMG_4398.heic", "IMG_4409.HEIC"):
        (d / nombre).write_bytes(b"")
    # Un archivo que NO es una foto de comprobante: no se mide aunque su nombre
    # este en la verdad.
    (d / "verdad.json").write_text("{}")
    return d


class TestLaSeleccionNoEsSensibleAMayusculas:
    def test_el_archivo_se_mide_aunque_la_verdad_lo_nombre_distinto(
        self, medir, carpeta
    ):
        """El caso medido: `IMG_4398.heic` en disco, `IMG_4398.HEIC` en la verdad.

        Es el nombre REAL de la foto que se perdia, no uno inventado. Con el
        filtro exacto este test daria verde con dos fotos de las tres.
        """
        verdad = {"IMG_4398.HEIC": {"total_amount": "193.65"}}

        fotos, sin_foto = medir.selecciona_fotos(carpeta, verdad)

        assert [f.name for f in fotos] == ["IMG_4398.heic"], (
            "la foto esta en la carpeta y en la verdad: si no se mide, la "
            "medicion esta midiendo menos de lo que dice"
        )
        assert sin_foto == []

    def test_las_tres_formas_de_escribir_el_nombre(self, medir, carpeta):
        verdad = {
            "img_4407.HEIC": {"total_amount": "234.00"},
            "Img_4398.Heic": {"total_amount": "193.65"},
            "IMG_4409.heic": {"total_amount": "279.93"},
        }

        fotos, sin_foto = medir.selecciona_fotos(carpeta, verdad)

        assert len(fotos) == 3
        assert sin_foto == []


class TestLoQueNoSeMideSeDice:
    def test_una_rotulada_sin_foto_se_reporta(self, medir, carpeta):
        """MEDIDO: `IMG_4220.jpeg` se rotulo y despues se movio al respaldo.

        Sin este aviso, el denominador de la medicion baja y nadie sabe por que:
        el porcentaje sale, se ve igual de bonito, y ya no es el mismo numero.
        """
        verdad = {
            "IMG_4407.heic": {"total_amount": "234.00"},
            "IMG_4220.jpeg": {"total_amount": "97.56"},
        }

        fotos, sin_foto = medir.selecciona_fotos(carpeta, verdad)

        assert [f.name for f in fotos] == ["IMG_4407.heic"]
        assert sin_foto == ["img_4220.jpeg"], (
            "una foto rotulada que no esta en la carpeta tiene que salir en la "
            "lista de 'sin foto': sin eso, la medicion pierde evidencia en "
            "silencio"
        )

    def test_un_que_no_es_foto_no_se_mide_aunque_esten_rotulado(self, medir, carpeta):
        """La verdad puede nombrar cosas que no son fotos; la extension manda."""
        (carpeta / "notes.txt").write_text("no soy un comprobante")
        verdad = {"notes.txt": {"total_amount": "1.00"}, "IMG_4407.heic": {}}

        fotos, sin_foto = medir.selecciona_fotos(carpeta, verdad)

        assert [f.name for f in fotos] == ["IMG_4407.heic"]
        assert sin_foto == ["notes.txt"]


class TestLasClavesDeAyudaNoSonFotos:
    def test_las_claves_guion_subrayado_no_cuentan_como_una_foto_mas(
        self, medir, carpeta
    ):
        """`_COMO_SE_LLENA`, `_CAMPOS`, `_QUIEN_ROTULO` son documentacion.

        El mensaje de "no hay fotos con etiqueta" decia `len(verdad)` sobre el
        diccionario entero, y por eso contaba cuatro archivos que no existen.
        Un mensaje de error con el numero equivocado hace que quien lo lee busque
        en el sitio equivocado.
        """
        verdad = {
            "_COMO_SE_LLENA": "una linea por foto",
            "_CAMPOS": ["total_amount"],
            "_QUIEN_ROTULO": "alguien, a ojo",
            "IMG_4407.heic": {"total_amount": "234.00"},
        }

        assert len(medir._entradas_de_verdad(verdad)) == 1

    def test_una_entrada_que_no_es_objeto_no_cuenta(self, medir):
        """`_CAMPOS` es una lista, no un objeto de valores. Sin este filtro, un
        `.get(campo)` sobre una lista revienta en la medicion."""
        verdad = {"_CAMPOS": ["total_amount"], "IMG_4407.heic": {"total_amount": "1.00"}}

        assert list(medir._entradas_de_verdad(verdad)) == ["img_4407.heic"]


class TestElEsqueletoQueEscribeElInit:
    def test_el_esqueleto_no_se_pisa(self, medir, carpeta):
        """`--init` escribe `verdad.json` y solo si no existe.

        Es el archivo con los valores leidos A MANO del papel: pisarlo es
        perder horas de rotulado y no hay forma de saber que paso.
        """
        (carpeta / "verdad.json").write_text(json.dumps({"IMG_4407.heic": {}}))

        with pytest.raises(SystemExit):
            medir._esqueleto(carpeta)

    def test_el_esqueleto_lleva_las_formas_de_nombre_que_se_pisan(self, medir, tmp_path):
        """El esqueleto se escribe dentro de la carpeta, y el nombre del archivo
        sale de lo que haya ahi. Se escribe tal cual, con su extension como venga, y
        por eso el emparejamiento tiene que ser tolerante en vez de exacto: no
        se puede obligar a que la verdad se llame igual que el archivo."""
        d = tmp_path / "vacia"
        d.mkdir()
        (d / "IMG_1234.HEIC").write_bytes(b"")

        medir._esqueleto(d)
        escrito = json.loads((d / "verdad.json").read_text(encoding="utf-8"))

        # El ejemplo del archivo es "EJEMPLO.jpg", no el nombre real: el esqueleto
        # no adivina. Lo que importa es que el emparejamiento sea tolerante.
        assert "EJEMPLO.jpg" in escrito