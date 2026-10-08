"""El generador de Insomnia: que la coleccion no pierda rutas ni scripts.

POR QUE ESTE ARCHIVO EXISTE
===========================

Porque el generador tiene tres modos de fallar que **no se ven en la salida**:

  1. **Una ruta nueva sin nombre en `NOMBRES`.** Sale con el `summary` del OpenAPI, que
     no esta mal —pero el `summary` es el docstring entero, y en la coleccion anterior
     salia el nombre del ultimo segmento de la ruta—. El sintoma es una coleccion
     donde esta todo y no esta en orden, y no dice nada de que falte una entrada.
  2. **Una ruta nueva sin script.** Sale sin el, y el efecto es que las variables del
     environment no se llenan: correr el listado y luego la ruta de detalle da 404 con
     la variable vacia, y parece un id mal escrito.
  3. **Una ruta que cae en la carpeta equivocada.** `_carpeta_de` devuelve
     `CARPETA_SIN_V1` para lo que no reconoce, sin avisar. Las 12 peticiones de
     inventario se fueron al cajon de "Sistema" durante meses sin que nadie lo notara.

Los tres son silenciosos. Este archivo los convierte en fallos de test.

LO QUE NO COMPRUEBA
===================

Que el JSON sea importable por Insomnia, ni que los scripts corran en su motor de
plantillas. Eso no se puede probar sin Insomnia. Lo que se comprueba es que la
coleccion tiene lo que el flujo necesita, que es lo que se rompio.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
GENERADOR = RAIZ / "scripts" / "generar_insomnia.py"
COLECCION = RAIZ / "expense-reconciler-insomnia.json"


@pytest.fixture(scope="module")
def gen():
    """El generador importado como modulo, sin ejecutarlo."""
    spec = importlib.util.spec_from_file_location("generar_insomnia", GENERADOR)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def spec_api(gen):
    """El OpenAPI de la API viva, con las rutas ya SIN `/api/v1`.

    Es la misma normalizacion que hace el generador (`ruta_de`), y se replica aqui a
    proposito: `NOMBRES` esta escrito sin el prefijo, y un test que usara las rutas
    crudas pasaria mientras el generador no encuentra nada —o al reves, que es el modo
    de fallo que importa.
    """
    from app.main import app

    crudo = app.openapi()["paths"]
    return {
        (metodo.upper(), _sin_prefijo(gen, ruta)): op
        for ruta, ops in crudo.items()
        for metodo, op in ops.items()
    }


def _sin_prefijo(gen, ruta: str) -> str:
    """La ruta como la escriben los diccionarios: sin `/api/v1`.

    `/health` no lo tiene y se queda como esta. Por eso esta funcion y no un
    `replace` a pelo: quitar el prefijo "donde se pueda" deja `/health` intacto, que es
    lo que se quiere, pero un `startswith` mal puesto dejaria `/health` como
    `ealth` y el test compararia contra rutas que no existen.
    """
    return ruta[len(gen.PREFIJO_V1):] if ruta.startswith(gen.PREFIJO_V1) else ruta


def _con_prefijo(gen, ruta: str) -> str:
    """La inversa, para lo que llama a `_carpeta_de`, que espera la ruta completa."""
    return ruta if ruta.startswith(gen.PREFIJO_V1) else gen.PREFIJO_V1 + ruta


# ---------------------------------------------------------------------------
# 1. Toda ruta tiene nombre
# ---------------------------------------------------------------------------


class TestTodaRutaTieneNombre:
    def test_no_falta_ninguna_entrada_en_nombres(self, gen, spec_api):
        """LA DEFENSA. Sin esto, las rutas nuevas salen con el docstring de nombre.

        Es el fallo 1 del docstring del archivo, y no se ve en la salida del
        generador: reporta "76 peticiones" y todas estan ahi. Lo que falta es que
        esten *rotuladas*.
        """
        sin_nombre = [
            f"{metodo} {ruta}"
            for metodo, ruta in spec_api
            if (metodo, ruta) not in gen.NOMBRES
        ]
        assert not sin_nombre, (
            f"estas rutas no tienen entrada en NOMBRES ({len(sin_nombre)}):\n  "
            + "\n  ".join(sorted(sin_nombre))
            + "\n\nSin nombre salen con el `summary` del OpenAPI, que es el docstring "
            "entero. Anadelas al diccionario de `scripts/generar_insomnia.py`."
        )

    def test_ninguna_entrada_de_nombres_apunta_a_una_ruta_inexistente(self, gen, spec_api):
        """Y al reves: un nombre que no corresponde a nada.

        Una entrada que se queda cuando se borra una ruta es menos grave —solo ocupa
        lineas— pero esconde el borrado: si alguien borra el endpoint y olvida la
        entrada, el proximo que lea el diccionario cree que la ruta sigue existiendo.
        """
        sobran = [
            f"{metodo} {ruta}"
            for metodo, ruta in gen.NOMBRES
            if (metodo, ruta) not in spec_api
        ]
        assert not sobran, (
            f"NOMBRES tiene entradas para rutas que ya no existen:\n  "
            + "\n  ".join(sorted(sobran))
            + "\n\nBorrarlas evita que el proximo lea 'esta ruta existe' de un "
            "diccionario que nadie ha revisado desde el ultimo commit."
        )

    def test_los_nombres_del_flujo_estan_numerados(self, gen):
        """Los que llevan numero son los que dicen en que orden correrlos.

        Solo algunos: `Categorias de gasto` y `Dashboard` no son un paso de nada. Los
        que si lo son tienen que decirlo, porque una coleccion sin orden obliga a
        adivinar por donde empezar.

        El prefijo es el de la ETAPA, y hay dos formas: `4a`/`4b`/`4c` son el mismo
        paso (numero con letra pegada) y `i1`/`t1`/`r1`/`u1`/`c1`/`s1`/`z1` son etapas
        que empiezan a contar por su cuenta (letra y numero). Se comprueban las dos
        porque el orden de la coleccion depende de ellas: `orden_de` parsea el par y
        una peticion sin numero cae al final de su carpeta.
        """
        sin_numero = [
            f"{metodo} {ruta}"
            for metodo, ruta in gen.NOMBRES
            if ruta.startswith(("/tickets", "/scan", "/inventario", "/usuarios",
                                "/auth", "/companies", "/reconciliations"))
            and not re.match(r"^([a-z]?\d+[a-z]?)\.\s", gen.NOMBRES[(metodo, ruta)])
        ]
        assert not sin_numero, (
            "estas rutas del flujo no llevan numero en su nombre, y el numero es lo "
            "que dice el orden:\n  " + "\n  ".join(sorted(sin_numero))
        )


# ---------------------------------------------------------------------------
# 2. Las variables del environment se llenan
# ---------------------------------------------------------------------------


class TestLasVariablesSeLlenan:
    #: (metodo, ruta) -> la variable que su script tiene que fijar.
    #: Cada entrada es un enlace del flujo: sin este script, la siguiente peticion
    #: falla con la variable vacia.
    DEBE_FIJAR = {
        ("POST", "/auth/login"): "token",
        ("GET", "/companies/"): "company_id",
        ("GET", "/tickets/"): "ticket_id",
        ("GET", "/scan/files"): "file_id",
        # El diagnostico NO fija nada porque no guarda nada. Esta entrada esta para
        # que nadie lo anada por inercia: el unico enlace que haria falta es
        # `ticket_id`, y el diagnostico lo RECIBE, no lo produce.
        ("POST", "/tickets/extract-and-create"): "ticket_id",
    }

    def test_cada_enlace_del_flujo_tiene_script(self, gen):
        """LA DEFENSA. Sin el script, la ruta siguiente da 404 con la variable vacia.

        El sintoma es desconcertante porque la peticion anterior respondio bien: se
        ve el `id` en la respuesta y se ve que la URL de la siguiente lo trae
        vacio, y no parece el mismo problema.
        """
        sin_script = [
            f"{metodo} {ruta} (necesita fijar {variable})"
            for (metodo, ruta), variable in self.DEBE_FIJAR.items()
            if (metodo, ruta) not in gen.SCRIPT_POR_RUTA
        ]
        assert not sin_script, (
            "estas rutas fijan una variable del environment y no tienen script:\n  "
            + "\n  ".join(sin_script)
        )

    def test_los_scripts_declaran_la_variable_que_anuncian(self, gen):
        """El script tiene que hacer `env.set('x', ...)`, no solo hablar de `x`.

        Un script que loguea "company_id = ..." sin llamar a `env.set` deja la
        variable vacia y produce exactamente el fallo de arriba, con un script
        presente. Se comprobo: el mensaje de `console.log` se confunde con el efecto.
        """
        problemas = []
        for (metodo, ruta), variable in self.DEBE_FIJAR.items():
            script = gen.SCRIPT_POR_RUTA.get((metodo, ruta))
            if script is None:
                continue  # ya lo reporta el test de arriba
            if f"env.set('{variable}'" not in script and f'env.set("{variable}"' not in script:
                problemas.append(f"{metodo} {ruta}: no hace env.set('{variable}')")
        assert not problemas, (
            "estos scripts anuncian una variable pero no la escriben:\n  "
            + "\n  ".join(problemas)
            + "\n\nUn `console.log` con el valor no es lo mismo que `env.set`: el "
            "primero se ve, el segundo se usa."
        )


# ---------------------------------------------------------------------------
# 3. Las carpetas
# ---------------------------------------------------------------------------


class TestLasCarpetas:
    def test_toda_ruta_cae_en_una_carpeta_conocida(self, gen, spec_api):
        """LA DEFENSA. Lo que no se reconoce se va a "Sistema", sin avisar.

        Ahi cayeron las 12 peticiones de inventario, durante meses. La coleccion
        "funcionaba" —estaban todas— y no estaba en orden.
        """
        # `/health` NO va en CARPETAS a proposito: cuelga del app y no de la v1, y por
        # eso el generador lo manda a "Sistema". Ese caso es el correcto, asi que se
        # excluye en vez de inventar una carpeta para el.
        huerfanas = [
            f"{metodo} {ruta}"
            for metodo, ruta in spec_api
            if ruta != "/health"
            and gen._carpeta_de(_con_prefijo(gen, ruta)) == gen.CARPETA_SIN_V1
        ]
        assert not huerfanas, (
            f"estas rutas caen en '{gen.CARPETA_SIN_V1}', que es donde va lo que "
            f"CARPETAS no reconoce:\n  " + "\n  ".join(sorted(huerfanas))
            + f"\n\nAnadelas a CARPETAS en {GENERADOR.name}, con su prefijo."
        )

    def test_toda_carpeta_de_la_lista_tiene_al_menos_una_ruta(self, gen, spec_api):
        """Al reves: una carpeta vacia es una carpeta que no se limpio.

        O la ruta se borro del servidor y nadie quito la carpeta, o el prefijo esta
        mal escrito y ya caeria en "Sistema" — que lo reporta el test de arriba—.
        """
        con_rutas = {gen._carpeta_de(_con_prefijo(gen, ruta)) for _, ruta in spec_api}
        vacias = [nombre for _, nombre in gen.CARPETAS if nombre not in con_rutas]
        assert not vacias, (
            f"estas carpetas de CARPETAS no reciben ninguna ruta: {vacias}\n"
            "Revisa el prefijo: si no coincide con ninguna ruta, todo cae en "
            f"'{gen.CARPETA_SIN_V1}' sin avisar."
        )


# ---------------------------------------------------------------------------
# 4. Los secretos
# ---------------------------------------------------------------------------


class TestLosSecretosNoSalen:
    def test_la_coleccion_no_tiene_contrasenas(self):
        """LA DEFENSA. El archivo esta versionado con remoto en GitHub.

        Mismo criterio que `.env` y que la regla de *Reglas que no se rompen*: un
        secreto escrito en un archivo que se sube es un secreto publicado.
        """
        texto = COLECCION.read_text(encoding="utf-8")
        # Una contrasena de las nuestras, o de ejemplo. Las dos aparecen como
        # `password` en el login, asi que no basta con buscar la palabra.
        assert "4ASV1Jy4nG3x938u7m4i" not in texto, (
            "la contrasena de insomnia@test.mx esta escrita en la coleccion, que "
            "esta versionada con remoto en GitHub."
        )

    def test_el_environment_trae_las_contrasenas_vacias(self):
        """Y no solo "no esta la de ejemplo": las dos variables salen vacias.

        Una vacia es una que alguien tiene que pegar a mano, en su maquina. Es lo
        unico que se puede hacer con un archivo que se versiona.
        """
        import json

        datos = json.loads(COLECCION.read_text(encoding="utf-8"))
        vacias = []
        for recurso in _recursos(datos):
            if recurso.get("_type") == "environment":
                for clave in ("password", "contrasena_actual"):
                    if clave in recurso.get("data", {}):
                        if recurso["data"][clave] != "":
                            vacias.append(f"{clave} = {recurso['data'][clave]!r}")
        assert not vacias, f"variables con valor en la coleccion:\n  " + "\n  ".join(vacias)


# ---------------------------------------------------------------------------
# 5. La coleccion esta al dia con la API
# ---------------------------------------------------------------------------


def _recursos(datos: dict):
    """Los recursos y sus hijos, en cualquier nivel."""
    pila = list(datos.get("resources", []))
    while pila:
        recurso = pila.pop()
        yield recurso
        pila.extend(recurso.get("children", []))


class TestLaColeccionEstaAlDia:
    def test_make_insomnia_check_sale_cero(self):
        """`make insomnia-check` compara el archivo contra la API viva.

        Es el unico test que detecta "se anadio una ruta y nadie regenero". Los demas
        de este archivo miran el generador; este mira el resultado.
        """
        proc = subprocess.run(
            [sys.executable, str(GENERADOR), "--check"],
            capture_output=True,
            text=True,
            cwd=RAIZ,
        )
        assert proc.returncode == 0, (
            "la coleccion esta desfasada. Regenerala con `make insomnia`.\n"
            f"{proc.stdout}\n{proc.stderr}"
        )

    def test_toda_peticion_de_la_api_esta_en_la_coleccion(self):
        """Ninguna ruta se queda fuera, y ninguna sobra.

        "Ninguna sobra" tambien importa: una peticion de una ruta que ya no existe es
        una que falla con 404 y parece un problema de la API.
        """
        import json

        from app.main import app

        datos = json.loads(COLECCION.read_text(encoding="utf-8"))
        # La URL del JSON lleva `{{ base_url }}` (que ya trae `/api/v1`) o
        # `{{ api_root }}` (que no), y las variables van como `{{ x }}`. Se
        # deshacen las dos cosas para comparar rutas crudas, porque comparar
        # plantillas contra plantillas no diria nada: los dos lados tendrian el
        # mismo error.
        en_json = set()
        for recurso in _recursos(datos):
            if recurso.get("_type") != "request":
                continue
            limpio = (
                recurso["url"]
                .replace("{{ base_url }}", "/api/v1")
                .replace("{{ api_root }}", "")
                .replace("{{ ", "{")
                .replace(" }}", "}")
            )
            en_json.add((recurso["method"].upper(), limpio))

        en_spec = {
            (metodo.upper(), ruta)
            for ruta, ops in app.openapi()["paths"].items()
            for metodo in ops
        }

        sobran = [f"{m} {r}" for m, r in sorted(en_json - en_spec)]
        faltan = [f"{m} {r}" for m, r in sorted(en_spec - en_json)]
        assert not sobran, (
            "la coleccion tiene peticiones que ya no existen en la API:\n  "
            + "\n  ".join(sobran)
            + "\n\nRegenera con `make insomnia`."
        )
        assert not faltan, (
            "la API tiene peticiones que no estan en la coleccion:\n  "
            + "\n  ".join(faltan)
            + "\n\nRegenera con `make insomnia`."
        )
