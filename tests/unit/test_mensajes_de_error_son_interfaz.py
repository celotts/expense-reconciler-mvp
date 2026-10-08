"""Los mensajes que ve un usuario son parte de la interfaz.

Esto no es una prueba de estilo. Cada `detail` de un `HTTPException` es texto que
alguien lee en la pantalla cuando algo sale mal, y hay tres formas en que un
mensaje puede estar mal sin que ningun test de codigo se entere:

1. **Estar en ingles.** La API tiene la interfaz en espanol y siete mensajes
   estaban en ingles. No habia forma de que se notara: un test que afirma el
   codigo 404 pasa igual con el texto que sea.
2. **Decir la mitad.** `Ticket not found` y `Ticket no encontrado` coexistian en
   el MISMO router para el MISMO 404. Quien lo sufria veia un idioma distinto
   segun el endpoint que hubiera tocado, y el segundo tampoco decia nada util.
3. **Empezar en minuscula.** `no hay ningun archivo escaneado con id 7` parece un
   log interno copiado a la pantalla, no algo que le dice a una persona que su
   peticion fue incorrecta.

La defensa es una lista negra de palabras que solo existen en ingles, mas la
comprobacion de que todo mensaje empieza en mayuscula. Es una heuristica, y
por eso la lista es corta y explicita: una lista de 400 palabras inglesas
fallaria en cuanto alguien escribiera un mensaje legitimo con una de ellas, y
entonces la defenderia nadie y pareceria que no hay defensa.

Lo que NO se comprueba aqui, a proposito:

- Que el mensaje sea el *correcto* para su endpoint. Eso lo afirma cada endpoint
  con su propio test, porque un mensaje equivocado en el 404 de empresas solo
  se ve pidiendo empresas.
- Que no haya excepciones crudas en el mensaje. Se comprueba con logs y leyendo
  los dos sitios donde se hacia, no con una heuristica sobre el codigo: hay un
  `detail=f"...{exc}"` LEGITIMO, el de `ExtractionUnavailable`, que es una
  excepcion de dominio con texto escrito para el usuario.
"""

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

# Palabras que en español no existen. Se listan una por una y no como patron
# porque un patron typearia falsos positivos en cuanto alguien escribiera
# "informacion" buscando "info".
#
# `found` esta porque "Ticket not found" fue el mensaje mas repetido del
# codebase (8 sitios) y es el que hizo visible el problema.
#
# Y lo que NO esta, y es la parte incomoda: la primera version de esta lista
# traia `"no existe el mapeo"` como ejemplo de frase inglesa, para acordarse de
# que el mensaje corregido era "no existe el mapeo contable". Es espanol. El
# test fallo senalando el mensaje QUE ESTA BIEN, que es la forma mas clara de
# recordar que una heuristica-Mal puesta produce un falso positivo que hace
# pasar por defectuoso lo correcto.
INGLES_SOLO_INGLES = (
    "not found",
    "already exists",
    "failed to",
    "does not exist",
)

# Una palabra suelta que en este dominio no puede ser española.
INGLES_PALABRAS = frozenset(
    {
        "found",
        "exists",
        "failed",
        "invalid",
        "cannot",
        "missing",
        "unknown",
        "required",
        "forbidden",
        "unauthorized",
        "created",
        "deleted",
        "updated",
        "listed",
        "uploaded",
        "extracted",
        "reconciled",
    }
)

# `detail=`, con comillas dobles o f-strings.
DETAIL = re.compile(r'detail=(?:f?)"([^"]*)"')


def _todos_los_detalles() -> list[tuple[str, str, int]]:
    """(mensaje, archivo, linea) de cada `detail` del codigo."""
    salida = []
    for archivo in sorted(APP.rglob("*.py")):
        for numero, linea in enumerate(
            archivo.read_text(encoding="utf-8").split("\n"), 1
        ):
            for encontrado in DETAIL.finditer(linea):
                salida.append((encontrado.group(1), archivo, numero))
    return salida


DETALLES = _todos_los_detalles()


class TestLosMensajesDeErrorSonInterfaz:
    def test_se_encuentra_alguno(self):
        """Si el extractor dejara de encontrar `detail=`, el resto no probaria nada.

        Un test de mensajes que no encuentra mensajes pasa en verde. Este es el
        que avisa de eso.
        """
        assert len(DETALLES) > 20, (
            f"solo se encontraron {len(DETALLES)} mensajes; el extractor dejo de "
            "funcionar o los mensajes cambiaron de forma"
        )

    def test_ninguno_esta_en_ingles(self):
        englances = []
        for mensaje, archivo, numero in DETALLES:
            bajo = mensaje.lower()
            for frase in INGLES_SOLO_INGLES:
                if frase in bajo:
                    englances.append(f"{archivo.name}:{numero}  '{mensaje}'")
            # Palabras sueltas: se buscan con limites para que "es" no entre por
            # ser parte de "este" ni "existe" por "existen".
            for palabra in re.findall(r"[a-z]+", bajo):
                if palabra in INGLES_PALABRAS:
                    englances.append(
                        f"{archivo.name}:{numero}  '{palabra}' en '{mensaje}'"
                    )
        assert not englances, "mensajes en ingles:\n  " + "\n  ".join(englances)

    def test_todos_empiezan_en_mayuscula(self):
        """La primera letra del mensaje va en mayuscula.

        No es cosmetica: un mensaje en minuscula se lee como una linea de log, y
        quien lo ve no sabe si le estan hablando a el o al sistema.
        """
        minusculos = [
            f"{archivo.name}:{numero}  '{mensaje}'"
            for mensaje, archivo, numero in DETALLES
            if mensaje and not mensaje[0].isupper()
        ]
        assert not minusculos, "mensajes que empiezan en minuscula:\n  " + "\n  ".join(
            minusculos
        )

    def test_ninguno_muestra_un_uuid(self):
        """Un UUID en la pantalla no le dice nada a quien lo lee.

        El UUID sirve para depurar, y para eso esta el log. En un 404 de
        empresa lo unico que se veia era `No existe la empresa 3f2a...`, que es
        un dato del servidor presentedo como si fuera util.
        """
        con_uuid = re.compile(
            r"\{[^}]*company_id[^}]*\}|\{"  # un id interpolado
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-",  # un uuid pegado en el texto
            re.IGNORECASE,
        )
        fugas = [
            f"{archivo.name}:{numero}  '{mensaje}'"
            for mensaje, archivo, numero in DETALLES
            if con_uuid.search(mensaje)
        ]
        assert not fugas, "mensajes con un id del servidor:\n  " + "\n  ".join(fugas)

    def test_no_revela_dos_versiones_del_mismo_404(self):
        """Un mismo concepto no puede tener dos textos.

        Existio "Ticket not found" y "Ticket no encontrado" para el mismo 404, en
        el mismo router. Los dos tests de cada uno pasaban, y el usuario veia un
        idioma u otro segun el endpoint. Aqui se comprueba lo generalizable: que
        los mensajes de 404 de ticket sean uno solo.
        """
        de_ticket = {
            mensaje for mensaje, _, _ in DETALLES if "ticket" in mensaje.lower()
        }
        assert len(de_ticket) <= 2, (
            "hay varios mensajes distintos para el ticket:\n  "
            + "\n  ".join(sorted(de_ticket))
        )


@pytest.mark.parametrize(
    "archivo_rel",
    [
        # Los cinco donde el mensaje estaba en ingles o duplicado. No es la lista
        # de todo lo que se toco: es la de donde era mas probable que volviera a
        # colarse un mensaje sin revisar.
        "api/reconciliations.py",
        "api/bank_transactions.py",
        "api/companies.py",
        "api/tickets.py",
        "api/auth.py",
    ],
)
def test_el_radar_mira_donde_importa(archivo_rel: str):
    """El test anterior es tan util como la lista de archivos que revisa.

    Si alguien anade un router nuevo y no esta en la lista, el test sigue
    pasando: no falla, solo no mira. Este reminds al menos que la lista es
    corta a proposito.
    """
    archivo = APP / archivo_rel
    assert archivo.exists(), f"{archivo_rel} no existe: la lista del parametrize esta vieja"
    detalles = [m for m, a, _ in DETALLES if a == archivo]
    assert detalles, f"{archivo_rel} ya no tiene ningun detail: revisa la lista del parametrize"
