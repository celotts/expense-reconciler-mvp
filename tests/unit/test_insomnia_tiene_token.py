"""La coleccion de Insomnia tiene que ser USABLE, no solo estar al dia.

`make insomnia-check` ya responde a la pregunta que hace bien —"el archivo del
repositorio coincide con lo que produce el generador"— y por eso no alcanza.

## EL DEFECTO QUE ESTE ARCHIVO EXISTE PARA QUE NO VUELVA

`AGENTS.md` decia que la coleccion anterior estaba "sin un solo header
`Authorization`" y que por eso respondia 401 en practicamente todo, y que por eso
la coleccion se genera en vez de escribirse a mano. La intencion era buena y el
diagnostico era exacto.

**El generador no hacia nada al respecto.** Medido sobre el archivo que el propio
script produce: 76 de 78 peticiones sin el header y sin `authentication`. El
script guardaba el token en el environment con el script post-respuesta del login
y nunca lo mandaba en ninguna peticion. La coleccion quedaba tan inutil como la
que se iba a arreglar, solo que con 78 peticiones en vez de 25 y con la promesa
de que ya no pasaba.

Y `insomnia-check` salia con 0, porque el archivo SI coincidia con el script: los
dos tenian el mismo defecto. Un check de consistencia no puede detectar un
problema que es la consistencia misma.

Por eso este test existe: comprueba una PROPIEDAD del archivo, no que el archivo
sea el que el script queria escribir.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
COLECCION = RAIZ / "expense-reconciler-insomnia.json"
GENERADOR = RAIZ / "scripts" / "generar_insomnia.py"

# Las unicas dos rutas que NO llevan token, y el motivo de cada una. Vive aqui y
# no se importa del generador porque el generador es lo que se esta probando: si
# el test importara su propia constante, un `SIN_AUTH` que creciera por error
# seguiria dando verde.
SIN_TOKEN = {
    ("POST", "/auth/login"): "no se puede mandar un token a la ruta que lo da",
    ("GET", "/health"): "el health check se usa antes de tener sesion, y en un "
    "portaemonitores no hay donde pegar un token",
}


def _peticiones() -> list[dict]:
    datos = json.loads(COLECCION.read_text(encoding="utf-8"))
    return [r for r in datos["resources"] if r.get("_type") == "request"]


def _ruta_de(peticion: dict) -> str:
    """La ruta tal como la declara el generador: `/api/v1/...`, sin barra final.

    ## POR QUE HAY QUE NORMALIZARLA, Y SON TRES COSAS

    Cada una de las tres costo un test rojo, y las tres producen el mismo
    sintoma — "falta una cabecera" — con causas distintas:

    - `url` es un STRING en el formato v4 de export de Insomnia
      (`"{{ base_url }}/dashboard/"`), no un objeto con `raw` dentro. Escribir
      `url.get("raw")` revienta con `'str' object has no attribute 'get'`.
    - Todas las rutas llevan barra final, porque `base_url` ya acaba en
      `/api/v1` y el generador une con `/`. Comparar contra `/auth/login` sin
      barra no matchea nunca.
    - **El health usa OTRA variable.** El resto de las peticiones cuelgan de
      `{{ base_url }}` (que ya trae `/api/v1`), pero `/health` cuelga de
      `{{ api_root }}`, que es solo el origen. Buscar siempre `{{ base_url }}`
      deja al health con la ruta entera como nombre y la comparacion contra
      `("/health")` falla — que es como el test reportaba un header faltante en
      una peticion que correctamente no lo lleva.

    La ultima es la peligrosa: sin normalizar, el test que quiere decir "faltan
    76 cabeceras" dice "falta una", y parece un problema del generador en vez
    de un problema del test.
    """
    crudo = peticion.get("url") or ""
    if isinstance(crudo, dict):  #tolera ambas formas del export
        crudo = crudo.get("raw", "")

    # Se corta en la primera variable de environment que aparezca, sin importar
    # cual sea: las dos son el origen y las dos valen igual para este proposito.
    for variable in ("{{ base_url }}", "{{ api_root }}"):
        if variable in crudo:
            crudo = crudo.split(variable, 1)[1]
            break

    ruta = crudo.split("?", 1)[0]  # los query params no son parte de la ruta
    return ruta.rstrip("/") or "/"


def _metodo(peticion: dict) -> str:
    return (peticion.get("method") or "GET").upper()


class TestLaColeccionSirve:
    def test_el_archivo_existe_y_tiene_peticiones(self):
        assert COLECCION.exists(), "falta la coleccion de Insomnia"
        peticiones = _peticiones()
        assert len(peticiones) > 50, (
            f"solo hay {len(peticiones)} peticiones; el archivo se genero a medias"
        )

    def test_todas_las_peticiones_salen_de_la_API_viva(self):
        """El archivo tiene que ser el que produce el generador HOY.

        Sin esto, las demas pruebas de este archivo pasan sobre una coleccion
        vieja que si tenia el header — y el defecto vuelve sin que nadie lo note.
        """
        resultado = subprocess.run(
            [sys.executable, str(GENERADOR), "--check"],
            cwd=RAIZ,
            capture_output=True,
            text=True,
        )
        assert resultado.returncode == 0, (
            "la coleccion esta desfasada del generador:\n"
            f"{resultado.stdout}{resultado.stderr}"
        )

    def test_toda_peticion_con_token_lo_manda(self):
        """Ninguna peticion autenticada sale sin la cabecera.

        El fallo que se corrige aqui es `headers: []` en 76 peticiones: un
        archivo de 78 peticiones donde 76 dan 401 es un archivo que no sirve
        para nada, y `insomnia-check` lo daba por bueno.
        """
        sin_cabecera = []
        for peticion in _peticiones():
            metodo, ruta = _metodo(peticion), _ruta_de(peticion)
            if (metodo, ruta) in SIN_TOKEN:
                continue
            cabeceras = peticion.get("headers") or []
            auth = next(
                (h for h in cabeceras if h.get("name") == "Authorization"), None
            )
            if auth is None:
                sin_cabecera.append(f"{metodo} {ruta}")
                continue
            if auth.get("value") != "Bearer {{ token }}":
                sin_cabecera.append(
                    f"{metodo} {ruta}  (valor: {auth.get('value')!r})"
                )
        assert not sin_cabecera, (
            f"{len(sin_cabecera)} peticiones sin `Authorization: Bearer {{ token }}`:\n  "
            + "\n  ".join(sin_cabecera[:12])
        )

    def test_las_dos_rutas_sin_token_no_lo_piden(self):
        """La excepcion es una excepcion: si se olvida, el login manda un token vacio.

        `Bearer ` sin nada es un 401 con un mensaje que habla de token mal, y el
        diagnostico real es que se puso la cabecera de mas.
        """
        por_ruta = {}
        for peticion in _peticiones():
            clave = (_metodo(peticion), _ruta_de(peticion))
            if clave not in SIN_TOKEN:
                continue
            cabeceras = peticion.get("headers") or []
            por_ruta[clave] = any(
                h.get("name") == "Authorization" for h in cabeceras
            )
        for clave, tiene in por_ruta.items():
            assert not tiene, f"{clave[0]} {clave[1]} no deberia llevar Authorization"

    def test_el_token_no_esta_escrito_en_el_archivo(self):
        """La cabecera dice `{{ token }}`, no un JWT.

        El archivo se versiona en git. Un token pegado ahi es un token publicado,
        y uno de estos dura 8 horas y da acceso a empresas ajenas: el API no
        tiene multi-tenancy y `get_current_user` no hace scoping por empresa.
        """
        texto = COLECCION.read_text(encoding="utf-8")
        # Un JWT son tres bloques base64 separados por dos puntos.
        import re

        jwt = re.search(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", texto)
        assert jwt is None, f"hay un JWT escrito en la coleccion: {jwt.group(0)[:24]}..."

    def test_el_token_del_environment_empieza_vacio(self):
        """`token` vacio en el environment, como `password`.

        Con un token escrito ahi, importar la coleccion en una maquina ajena
        entra con la sesion de otra.
        """
        datos = json.loads(COLECCION.read_text(encoding="utf-8"))
        entorno = next(
            r for r in datos["resources"] if r.get("_type") == "environment"
        )
        assert entorno["data"].get("token", "") == "", "el token viene con valor"
        assert entorno["data"].get("password", "") == "", "la contrasena viene con valor"

    def test_las_peticiones_con_contrasena_conservan_las_dos_cabeceras(self):
        """`X-Contrasena-Actual` y `Authorization` van las dos.

        Este es el cruce que se pierde cuando un header se anade antes del
        borrado de multipart, o cuando se anaden por encima en vez de por
        debajo: son las unicas dos rutas que exigen las dos cabeceras, y son
        precisamente las que comprometen a OTRO usuario.
        """
        por_ruta = {}
        for peticion in _peticiones():
            por_ruta[(_metodo(peticion), _ruta_de(peticion))] = {
                h.get("name") for h in (peticion.get("headers") or [])
            }
        # Se buscan por sufijo porque el path param puede venir con o sin `{}`.
        encontradas = 0
        for (metodo, ruta), cabeceras in por_ruta.items():
            if "/usuarios/" not in ruta:
                continue
            if metodo not in ("PATCH", "POST"):
                continue
            assert "Authorization" in cabeceras, f"{metodo} {ruta} perdio el token"
            assert "X-Contrasena-Actual" in cabeceras, (
                f"{metodo} {ruta} perdio X-Contrasena-Actual"
            )
            encontradas += 1
        assert encontradas >= 2, (
            f"solo se encontraron {encontradas} rutas de usuarios; la lista del "
            "generador cambio y este test ya no mira las que importan"
        )
