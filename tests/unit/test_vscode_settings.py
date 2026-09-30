"""El `.vscode/` se versiona, y eso tiene un precio que hay que pagar vigilando.

Por que este archivo existe
---------------------------

Un `.vscode/settings.json` versionado se vuelve de CONFIANZA por el hecho de
estar ahi: si ya venia en el repo, el que lo revisa supone que es inocuo. Y en
VS Code un archivo de settings **ejecuta cosas**. No scripts: declaraciones. Que
es justo lo que hace que un review las pase por alto, porque un commit que anade
`"terminal.integrated.profiles"` se ve igual que uno que corrige un typo.

Los vectores concretos estan escritos en el propio `settings.json`, para que el
motivo viaje con el archivo y no con la memoria de alguien. Aqui solo se
comprueban.

Que protege
-----------

1. Que `settings.json` solo tenga claves de la lista blanca. Una clave nueva
   tiene que ser una decision, no una consecuencia de instalar una extension.
2. Que ninguna clave de ejecucion aparezca, ni anidada. `terminal.integrated
   .env.LD_PRELOAD` cuenta igual que `terminal.integrated.env`.
3. Que la confianza del workspace no este apagada.
4. Que **toda regla silenciada tenga su justificacion en un comentario del mismo
   archivo**. Este es el que importa: sin el, anadir `"reportUndefinedVariable":
   "none"` es un cambio de una linea que nadie va a questionar, y con el es un
   cambio que obliga a escribir por que.
5. Que `.gitignore` siga restringiendo `.vscode/` a los dos archivos. Si alguien
   le quita la excepcion, `git add .` empieza a arrastrar `launch.json`.
6. Que `tasks.json` solo ejecute `make`. Un `launch.json` o una tarea con
   `rm -rf` no es un ajuste de editor.

Verificado por mutacion: estos tests mueren si se anade
`"terminal.integrated.profiles"`, si se silencia una regla sin comentario que la
justifique, o si se le quita el `!.vscode/settings.json` al `.gitignore`.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SETTINGS = RAIZ / ".vscode" / "settings.json"
TASKS = RAIZ / ".vscode" / "tasks.json"
GITIGNORE = RAIZ / ".gitignore"

# Solo esto se acepta en la raiz del JSON. Anadir una clave de mas es una
# decision que alguien tiene que escribir en el comentario del archivo.
CLAVES_PERMITIDAS = {
    "python.analysis.typeCheckingMode",
    "python.analysis.diagnosticSeverityOverrides",
    "files.exclude",
    "editor.rulers",
    "editor.formatOnSave",
    "[python]",
    "[typescript]",
}

# Claves que ejecutan codigo, cambian donde se resuelve un modulo o desvian el
# trafico. Se comparan por prefijo: `a.b` cubre `a.b.c` y `a.b.d`.
CLAVES_PELIGROSAS = (
    "terminal.integrated.profiles",
    "terminal.integrated.defaultProfile",
    "terminal.integrated.env",
    "terminal.integrated.cwd",
    "terminal.integrated.automationProfile",
    "python.analysis.extraPaths",
    "python.analysis.stubPath",
    "python.analysis.autoSearchPaths",
    "python.pythonPath",
    "python.defaultInterpreterPath",
    "http.proxy",
    "http.proxyStrictSSL",
    "http.proxyAuthorization",
    "git.path",
    "git.enabled",
    "mergeTool",
    "diffEditor.commands",
    "codeActionsOnSave",
    "editor.codeActionsOnSave",
    "security.workspace.trust.enabled",
    "security.allowUnsafePaths",
    "security.workspace.trust.banner",
    "remote.SSH.configFile",
    "remote.extensionKind",
    "debug.javascript",
    "debug.console",
    "files.associations",
    "files.watcherExclude",
)


def _leer_jsonc(ruta: Path) -> dict:
    """El `.vscode/settings.json` admite comentarios: es JSONC, no JSON.

    Se quitan los comentarios de linea completa. Los de fin de linea se dejan:
    un valor como `"none", // por que` no es JSON valido y hay que conservarlo
    para que el test de justificacion pueda leer el texto que lo acompaña.
    """
    crudo = ruta.read_text(encoding="utf-8")
    sin_comentarios = re.sub(r"^\s*//.*$", "", crudo, flags=re.MULTILINE)
    return json.loads(sin_comentarios)


def _todas_las_claves(d: dict, prefijo: str = "") -> list[str]:
    """Aplana el JSON a claves con ruta completa.

    Sin esto, `{"terminal": {"integrated": {"env": {...}}}}` se revisaria como
    tres claves sueltas y el chequeo pasaria de largo. El prefijo es lo que
    hace que el patron de prefijo de las claves peligrosas sirva.
    """
    halladas: list[str] = []
    for clave, valor in d.items():
        ruta = f"{prefijo}{clave}"
        halladas.append(ruta)
        if isinstance(valor, dict):
            halladas.extend(_todas_las_claves(valor, f"{ruta}."))
    return halladas


def _es_prefijo_de(clave: str, peligrosa: str) -> bool:
    """`a.b` cubre `a.b` y `a.b.c`, pero no `a.bc` (que es otra clave)."""
    return clave == peligrosa or clave.startswith(peligrosa + ".")


class TestSoloClavesConocidas:
    def test_el_archivo_existe(self):
        assert SETTINGS.exists(), (
            "Falta .vscode/settings.json. Si se borro, el panel de Problemas "
            "vuelve a llenar de ruido y nadie sabra por que."
        )

    def test_es_jsonc_valido(self):
        # Un error de sintaxis hace que VS Code ignore el archivo entero, en
        # silencio. Es el fallo mas caro de este archivo y el menos visible.
        _leer_jsonc(SETTINGS)

    def test_no_hay_claves_raiz_desconocidas(self):
        raices = set(_leer_jsonc(SETTINGS))
        desconocidas = raices - CLAVES_PERMITIDAS
        assert not desconocidas, (
            f"Claves nuevas en settings.json: {sorted(desconocidas)}. "
            "Si es legitima, anadela a CLAVES_PERMITIDAS en este test y "
            "explicala en el comentario del archivo."
        )


class TestSinVectoresDeEjecucion:
    @pytest.mark.parametrize("archivo", [SETTINGS, TASKS])
    def test_ninguna_clave_peligrosa(self, archivo):
        if not archivo.exists():
            pytest.skip(f"{archivo.name} no esta en el repo")
        halladas = _todas_las_claves(_leer_jsonc(archivo))
        malas = [
            c for c in halladas
            if any(_es_prefijo_de(c, p) for p in CLAVES_PELIGROSAS)
        ]
        assert not malas, (
            f"{archivo.name} tiene claves que ejecutan codigo o desvian datos: "
            f"{malas}. Un .vscode/ versionado se carga al abrir la carpeta y "
            "VS Code le cree sin preguntar. Si necesitas alguna, va en tu "
            "configuracion de usuario, no en el repo."
        )

    def test_la_confianza_del_workspace_no_esta_apagada(self):
        # Apagarla hace que VS Code ejecute lo que encuentre en la carpeta sin
        # preguntar. Es la diferencia entre "reviso antes de abrir" y "lo que
        # venga, se ejecuta".
        #
        # Se mira el JSON parseado y no el texto crudo: el archivo de settings
        # NOMBRA esta clave en el comentario que explica por que es un vector.
        # Un grep sobre el texto daria positivo con el comentario, que es justo
        # lo contrario de lo que se quiere comprobar.
        d = _leer_jsonc(SETTINGS)
        seguridad = d.get("security", {})
        confianza = seguridad.get("workspace", {}).get("trust", {})

        assert not (seguridad.get("workspace") or {}).get("trust", {}).get("enabled") is False, (
            "No se desactiva la confianza del workspace desde el repo: eso hace "
            "que VS Code ejecute lo que encuentre en la carpeta sin preguntar."
        )
        assert "untrustedFiles" not in confianza, (
            "No se declara 'untrustedFiles' desde el repo: decide que clase de "
            "archivos se abren sin preguntar."
        )


class TestTodaReglaCalladaEstaJustificada:
    """El test que evita que el archivo crezca a base de silencios."""

    def test_cada_regla_silenciada_tiene_comentario(self):
        """El comentario tiene que estar PEGADO a la regla, no en el archivo.

        Una version anterior de este test buscaba `//` en los 400 caracteres
        siguientes a la regla y daba por bueno un silencio sin comentario: la
        ventana llegaba hasta el bloque explicativo del final del archivo y
        encontraba un `//` que no tenia nada que ver. Se vio por mutacion, que
        es como se sabe que un test sirve y no solo que pasa.

        Aqui la ventana se corta en cuanto aparece la siguiente clave o el
        cierre del bloque. Si el comentario esta en la linea de la regla o en
        las inmediatamente siguientes, cuenta; si no, no.
        """
        crudo = SETTINGS.read_text(encoding="utf-8")
        reglas = _leer_jsonc(SETTINGS).get(
            "python.analysis.diagnosticSeverityOverrides", {}
        )
        assert reglas, "Se perdio el bloque de reglas silenciadas"

        lineas = crudo.splitlines()
        patron_clave = re.compile(r'"([A-Za-z]+)"\s*:\s*"[^"]*"')
        patron_otro = re.compile(r'^\s*"[^"]+"\s*:')
        patron_cierre = re.compile(r"^\s*[}\]]")

        for regla in reglas:
            encontrada = False
            for i, linea in enumerate(lineas):
                m = patron_clave.search(linea)
                if not m or m.group(1) != regla:
                    continue
                encontrada = True

                # Hacia abajo: el resto de su linea y las siguientes, hasta la
                # proxima clave o el cierre del bloque.
                ventana = [linea[m.end():]]
                for j in range(i + 1, len(lineas)):
                    if patron_cierre.match(lineas[j]) or patron_otro.match(lineas[j]):
                        break
                    ventana.append(lineas[j])

                # Hacia arriba: las lineas de comentario inmediatamente
                # anteriores, hasta la clave previa o el inicio del bloque. El
                # estilo "el comentario va encima de la regla" es el que se usa
                # cuando la justificacion ocupa varios parrafos, y es el que
                # esta en el archivo hoy.
                for j in range(i - 1, -1, -1):
                    if patron_otro.match(lineas[j]) or patron_cierre.match(lineas[j]):
                        break
                    ventana.append(lineas[j])

                assert any("//" in v for v in ventana), (
                    f"La regla '{regla}' se silencia sin comentario que la "
                    "justifique. Va encima o debajo de la regla, no en otro "
                    "punto del archivo."
                )
                break
            assert encontrada, (
                f"La regla '{regla}' esta en el JSON parseado pero no aparece "
                "como clave en el texto. Revisa como esta escrita."
            )

    def test_lo_silenciado_son_solo_las_dos_verificadas(self):
        """Guarda contra acumular silencias 'temporales' que nunca se quitan.

        Las dos que hay están justificadas una por una: `reportMissingImports`
        porque las dependencias viven en la imagen de Docker, y
        `reportArgumentType` porque los 164 avisos son el artefacto de
        `Column[X]` de SQLAlchemy 2.0. Cualquier otra necesita pasar por una
        medicion, no por una buena intencion.
        """
        reglas = set(
            _leer_jsonc(SETTINGS).get(
                "python.analysis.diagnosticSeverityOverrides", {}
            )
        )
        permitidas = {"reportMissingImports", "reportArgumentType"}
        nuevas = reglas - permitidas
        assert not nuevas, (
            f"Reglas silenciadas sin verificar: {sorted(nuevas)}. "
            "Cuenta cuantos avisos quita cada una y comprueba que no esta "
            "tapando un defecto real ANTES de anadirla a la lista de "
            "CLAVES_PERMITIDAS de arriba."
        )

    def test_las_reglas_que_encuentran_bugs_no_estan_calladas(self):
        """Estas cuatro SI encuentran defectos en este codigo.

        No es hipotesis: `reportUndefinedVariable` y `reportAttributeAccessIssue`
        sacaron el `SourceType.AUTO` de `app/modules/expenses/crud.py:136` y el
        `select`/`and_` sin importar de `pipeline.py:224-225`. Callarlas seria
        apagar la unica defensa que aviso de codigo muerto.
        """
        reglas = _leer_jsonc(SETTINGS).get(
            "python.analysis.diagnosticSeverityOverrides", {}
        )
        decisivas = {
            "reportUndefinedVariable",
            "reportAttributeAccessIssue",
            "reportOptionalMemberAccess",
            "reportCallIssue",
        }
        assert not (decisivas & set(reglas)), (
            f"Esta callada una regla que si encuentra bugs aqui: "
            f"{sorted(decisivas & set(reglas))}"
        )


class TestGitignoreAcotaLaCarpeta:
    def test_vscode_no_esta_ignorado_como_carpeta_entera(self):
        texto = GITIGNORE.read_text(encoding="utf-8")
        assert ".vscode/*" in texto, (
            "La carpeta .vscode deberia ignorarse con comodin, no entera. Sin "
            "eso, un .vscode/settings.local.json con tus rutas sale en un "
            "'git add .' por accidente."
        )

    def test_los_dos_archivos_si_estan_exentos(self):
        texto = GITIGNORE.read_text(encoding="utf-8")
        for nombre in (".vscode/settings.json", ".vscode/tasks.json"):
            assert f"!{nombre}" in texto, f"{nombre} deberia versionarse"

    def test_ningun_otro_vscode_esta_exento(self):
        texto = GITIGNORE.read_text(encoding="utf-8")
        exentas = re.findall(r"!(\.vscode/[^\s#]+)", texto)
        assert set(exentas) == {".vscode/settings.json", ".vscode/tasks.json"}, (
            f"Se exenta algo mas: {sorted(set(exentas) - {'.vscode/settings.json', '.vscode/tasks.json'})}. "
            "Cada archivo exento es una superficie de confianza nueva."
        )


class TestLosArchivosRealmenteSeVersionan:
    """El test que casi no hace falta, y es el mas importante de los dos.

    Los tests de arriba leen el texto del `.gitignore` y pueden pasar mientras los
    archivos siguen SIN versionarse. Ya paso: el `.gitignore` del repo decia
    `!.vscode/settings.json`, los tests de texto daban verde, y `git status` no
    mostraba el archivo. La causa era `~/.gitignore_global` con `.vscode/` — la
    CARPETA, no su contenido. Git no baja a un directorio excluido, asi que la
    exencion del repo era letra muerta: solo funcionaba con `git add -f`, y el
    siguiente `git add .` no lo recogia.

    Ese es el modo de fallo de este repo entero: documentacion que dice una cosa
    y el codigo otra. Por eso este test pregunta a GIT, no al archivo.

    Si falla, la causa es casi siempre el `.gitignore` global de la maquina, no
    este repo. Se arregla ahi: `.vscode/` tiene que ser `.vscode/*`.
    """

    @pytest.mark.parametrize("nombre", [".vscode/settings.json", ".vscode/tasks.json"])
    def test_git_no_lo_ignora(self, nombre):
        r = subprocess.run(
            ["git", "check-ignore", "-q", nombre],
            cwd=RAIZ, capture_output=True,
        )
        assert r.returncode != 0, (
            f"{nombre} esta en .gitignore de verdad, aunque el .gitignore de "
            "este repo diga que se versiona. Un archivo versionado a la fuerza "
            "con 'git add -f' es el peor caso: el repo afirma una cosa, git "
            "hace otra, y el siguiente 'git add .' pierde los cambios.\n"
            "Revisa ~/.gitignore_global: si tiene '.vscode/' (la carpeta), "
            "cambiala a '.vscode/*' (el contenido). Con la carpeta excluida, "
            "git no baja a ella y ninguna '!' del repo funciona."
        )

    def test_un_tercer_archivo_seria_ignorado(self):
        """La exencion no debe abrir la carpeta entera.

        Se pregunta a git por un archivo que NO existe, con `git check-ignore`,
        en vez de mirar si algo sale en `git status`. La diferencia importa: este
        test se puede poner en rojo a proposito, mientras que el de `git status`
        dependia de si un archivo temporal habia quedado cacheado o no, y nunca
        mueria. Un test que no puede fallar no es un test.
        """
        r = subprocess.run(
            ["git", "check-ignore", "-q", ".vscode/launch.json"],
            cwd=RAIZ, capture_output=True,
        )
        assert r.returncode == 0, (
            ".vscode/launch.json NO esta ignorado. Con las reglas actuales, un "
            "launch.json con una configuracion de depuracion seARIA versionado "
            "por un 'git add .'. Las tareas de depuracion ejecutan comandos."
        )

    def test_un_settings_local_seria_ignorado(self):
        """El archivo personal del editor nunca debe versionarse."""
        r = subprocess.run(
            ["git", "check-ignore", "-q", ".vscode/settings.local.json"],
            cwd=RAIZ, capture_output=True,
        )
        assert r.returncode == 0, (
            ".vscode/settings.local.json deberia estar ignorado: es la "
            "configuracion personal de cada maquina (rutas, terminal, "
            "interpretador) y no tiene nada que ver con el proyecto."
        )


class TestTasksSoloEjecutanMake:
    def test_los_comandos_son_de_make(self):
        if not TASKS.exists():
            pytest.skip("tasks.json no esta en el repo")
        tareas = _leer_jsonc(TASKS).get("tasks", [])
        assert tareas, "tasks.json se quedo sin tareas"
        for tarea in tareas:
            comando = tarea.get("command", "")
            assert re.match(r"^\s*make\s+[a-z-]+(\s*&&\s*make\s+[a-z-]+)*\s*$", comando), (
                f"La tarea '{tarea.get('label')}' ejecuta: {comando!r}. "
                "Solo se admiten objetivos de make. Un comando arbitrario en "
                "una tarea versionada se ejecuta con un Ctrl+Shift+B de quien "
                " abra el repo."
            )

    def test_ninguna_tarea_toma_argumentos_del_usuario(self):
        if not TASKS.exists():
            pytest.skip("tasks.json no esta en el repo")
        for tarea in _leer_jsonc(TASKS).get("tasks", []):
            assert "${" not in tarea.get("command", ""), (
                f"La tarea '{tarea.get('label')}' interpola una variable. "
                "Con `${input:...}` o `${env:...}` el comando cambia segun quien "
                "lo pulse."
            )
