"""La ruta de la IA que SI corre no arrastra las deps de una que no corre.

Por que este archivo existe
---------------------------

`ai_client.py` es el modulo de la IA del proyecto, y esta en la cadena de vivos
de la API: `main.py` -> `api_router.py` -> `tickets.py` -> `ai_extractor.py` ->
`ai_client.py`. Cualquier import de nivel de modulo ahi se paga en **cada
arranque del contenedor**.

Medido en esta maquina antes del arreglo:

    import torch                      0.68s
    import sentence_transformers       3.83s
    import app.services.ai_client     4.44s

Es decir: de los 4.44 segundos, 3.83 eran sentence_transformers, para un
embedding local que **no tiene un solo caller**.

Por que no se borro y se hizo perezoso
--------------------------------------

Porque `torch` y `sentence_transformers` siguen siendo dependencias
declaradas, y el codigo que las usa tiene que seguir funcionando. Pasarlas a
import perezoso no cambia ninguna conducta: la propiedad `local_embedding_model`
sigue cargando el modelo si alguien la llama. Lo que cambia es **cuando** se paga.

Lo que NO se hizo, a proposito: borrar `create_embedding`,
`create_embeddings_batch`, `vector_search.py` ni la extension `vector` de
`db/init.sql:3`. Eso es el B2 del contrato (docs/contrato-producto.md §6, lineas
235-241) y pide medir RAM con `make stats` antes y despues, porque quitar una
dependencia de la imagen no es lo mismo que dejar de importarla en un modulo. Lo
que se hace aqui es la mitad reversible; la otra mitad espera la medicion.

Que protege
-----------

1. Que importar `ai_client` NO cargue torch ni sentence_transformers. Se mide
   ejecutando un interprete limpio y mirando `sys.modules`: no se lee el fuente,
   porque un source que "parece" perezoso y se_traga el coste no vale nada.
2. Que no vuelva a haber un import de estos dos a nivel de modulo. Esta es la
   que sobrevive a los refactors: el test 1 mide el efecto, este fija la causa.
3. Que `create_embedding` siga sin callers. Si alguien lo conecta, este test
   falla y dice lo que tiene que decidir tambien: la dependencia vuelve a estar
   en uso, y el B2 deja de ser "borrar andamiaje" para ser "decidir si se
   implementa embeddings de verdad".
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
AI_CLIENT = RAIZ / "app/services/ai_client.py"

# Los modulos que no se deben cargar al importar ai_client, con el nombre bajo
# el que aparecen en sys.modules.
DEPS_PESADAS = ("torch", "sentence_transformers")


class TestElImportNoSePagaAlArranque:
    """El comportamiento, medido en un interprete limpio."""

    def test_importar_ai_client_no_carga_torch(self):
        self._assert_no_carga("torch")

    def test_importar_ai_client_no_carga_sentence_transformers(self):
        self._assert_no_carga("sentence_transformers")

    def _assert_no_carga(self, modulo: str):
        codigo = (
            "import sys; import app.services.ai_client; "
            f"print({modulo!r} in sys.modules)"
        )
        r = subprocess.run(
            [sys.executable, "-c", codigo],
            cwd=RAIZ, capture_output=True, text=True,
        )
        assert r.returncode == 0, (
            f"importar app.services.ai_client fallo: {r.stderr[-500:]}"
        )
        assert r.stdout.strip() == "False", (
            f"importar app.services.ai_client carga {modulo}, que son "
            f"~4.5s y cientos de MB de RAM, para una ruta que no tiene "
            f"callers.\nEl import tiene que estar DENTRO de la propiedad que "
            f"lo usa (local_embedding_model), no en el tope del modulo.\n"
            f"Mide el antes y el despues antes de tocarlo; ver el docstring de "
            f"este archivo."
        )

    def test_la_ruta_viva_de_la_extraccion_sigue_importando(self):
        """Quitar el peso no puede romper la ruta que SI se usa.

        `tickets.py` es lo que monta la cascada de captura. Si el cambio de
        imports se llevo por delante algo que la extraccion necesita, este
        test lo dice al importar el router entero, no al fallar en produccion.
        """
        r = subprocess.run(
            [sys.executable, "-c", "import app.api.tickets"],
            cwd=RAIZ, capture_output=True, text=True,
        )
        assert r.returncode == 0, (
            f"app.api.tickets ya no importa: {r.stderr[-500:]}\n"
            "Ese modulo es la cadena de captura; si no entra, no entra la IA."
        )


class TestNoVuelveElImportDeNivelDeModulo:
    """La causa, fijada para que un refactor no la reintroduzca en silencio."""

    def test_no_hay_import_de_torch_ni_de_sentence_transformers_en_el_tope(self):
        arbol = ast.parse(AI_CLIENT.read_text(encoding="utf-8"))
        # Solo el cuerpo del modulo: los imports de nivel superior. Un import
        # dentro de una funcion o de un `if TYPE_CHECKING` NO se paga al
        # arrancar, y es exactamente lo que se busca.
        culpables = []
        for nodo in arbol.body:
            if isinstance(nodo, ast.Import):
                for alias in nodo.names:
                    if alias.name.split(".")[0] in DEPS_PESADAS:
                        culpables.append(f"linea {nodo.lineno}: import {alias.name}")
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                if nodo.module.split(".")[0] in DEPS_PESADAS:
                    culpables.append(f"linea {nodo.lineno}: from {nodo.module} import ...")
        assert not culpables, (
            "ai_client.py volvio a importar deps pesadas a nivel de modulo:\n  "
            + "\n  ".join(culpables)
            + "\nEse import se paga en cada arranque de la API (main.py -> "
            "api_router -> tickets -> ai_extractor -> ai_client). Muevelo "
            "dentro de la propiedad que lo usa."
        )

    def test_la_propiedad_del_embedding_sigue_existiendo(self):
        """Import perezoso no es "lo borre y ya".

        Si alguien quita la propiedad en vez de mover el import, el test de
        arriba pasa (no hay import pesado) y el B2 pierde el referente: ya no
        se puede ni medir. Se comprueba que la propiedad sigue ahi.
        """
        arbol = ast.parse(AI_CLIENT.read_text(encoding="utf-8"))
        clase = next(
            (n for n in arbol.body if isinstance(n, ast.ClassDef) and n.name == "AIClient"),
            None,
        )
        assert clase is not None, "no se encuentra la clase AIClient en ai_client.py"
        nombres = {n.name for n in clase.body if isinstance(n, ast.FunctionDef)}
        assert "local_embedding_model" in nombres, (
            "AIClient.local_embedding_model desaparecio. El import perezoso de "
            "torch/sentence_transformers se hizo para que esta propiedad "
            "siga funcionando sin arrastrarla al arranque; borrarla deja el "
            "andamiaje sin referente y el B2 sin poder medirse."
        )


class TestLosEmbeddingsSiguenSinCallers:
    """Guarda de la premisa que hace aceptable el trabajo pendiente."""

    def test_create_embedding_no_tiene_callers(self):
        """La premisa del B2, vigilada.

        Importar modulos no detecta una llamada por atributo, asi que aqui se
        busca el nombre en el codigo de `app/` y se filtra la definicion. Es
        un grep, no un test de comportamiento, y a proposito: lo que se quiere
        afirmar es una propiedad del arbol de llamadas, no del valor que
        devuelva la funcion.
        """
        r = subprocess.run(
            ["grep", "-rn", "create_embedding", str(RAIZ / "app"), "--include=*.py"],
            capture_output=True, text=True,
        )
        assert r.returncode in (0, 1), f"grep fallo: {r.stderr[-300:]}"
        llamadas = [
            ln for ln in r.stdout.splitlines()
            if "ai_client.py" not in ln          # la definicion
            and "def " not in ln
        ]
        assert not llamadas, (
            "create_embedding YA TIENE CALLERS:\n  " + "\n  ".join(llamadas) + "\n"
            "El B2 del contrato asumia que no los tenia. Si ahora si, la "
            "dependencia vuelve a estar en uso: hay que decidir si se "
            "implementa embeddings de verdad (y entonces el modelo de 470MB y "
            "la extension vector dejan de ser andamiaje) o si se quita el "
            "codigo. Lo que no procede es dejar las dos cosas a medias."
        )
