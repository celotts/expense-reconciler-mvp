"""El lockfile del front se versiona, y el build depende de eso.

Por que este archivo existe
---------------------------

`front/Dockerfile:5` y `front/Dockerfile.prod:11` instalan con `npm ci`. `npm ci`
no es `npm install` con otro nombre: **exige** un lockfile y aborta sin el. El
error real, reproducido sobre un clone limpio de este repo:

    npm error code EUSAGE
    npm error The `npm ci` command can only install with an existing
    package-lock.json or npm-shrinkwrap.json with lockfileVersion >= 1.

`make up` llama a `docker compose build`, asi que esto no es un problema de un
script suelto: es el comando de arranque del proyecto.

El `.gitignore` global de la maquina de este equipo trae `package-lock.json`
(`~/.gitignore_global:22`). Con esa regla, el archivo se puede tener en disco y
build sin problema, y aun asi **no estar en el repo**: el build pasaria en la
maquina donde alguien ya lo instalo y fallaria en cualquier clone limpio, en CI,
o en la maquina de otra persona. Un fallo que solo aparece donde no develops es
el peor tipo: no se reproduce y se atribuye a otra cosa.

La defensa es la negacion `!front/package-lock.json` del `.gitignore` de este
repo (linea 104), con el mismo criterio que ya usaba el repo para `db/**/*.sql`:
las reglas del repo ganan sobre las del global, asi que ahi se exenciona
explicitamente lo que el global se come.

Que protege
-----------

1. Que el lockfile no este ignorado de verdad. Preguntando a GIT, no al texto
   del `.gitignore` — el modo de fallo de este repo entero es documentacion que
   dice una cosa y git hace otra (ver `test_vscode_settings.py`).
2. Que este de verdad en el indice, y no solo "no ignorado". Un archivo
   unsubido con `git add -f` pasa el test 1 y se pierde en el siguiente
   `git add .`: es el peor caso, porque el repo afirma una cosa y git otra.
3. Que lockfile y `package.json` no se desincronicen. Desincronizados, `npm ci`
   tambien falla — pero de forma distinta y mas dificil de leer.
4. Que los Dockerfiles del front sigan instalando con `npm ci`. No para que el
   lockfile sea obligatorio (tambien es buena practica con `npm install`), sino
   para que el cambio sea una decision y no un descuido.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]

LOCKFILE = Path("front/package-lock.json")
DOCKERFILES_DEL_FRONT = sorted((RAIZ / "front").glob("Dockerfile*"))


class TestElLockfileSeVersiona:
    """Lo que hay que preguntarle a git, no al `.gitignore`."""

    def test_el_lockfile_no_esta_ignorado(self):
        # `--no-index` es lo que hace que este test valga. Sin el, `git
        # check-ignore` consulta el indice y un archivo YA versionado nunca se
        # reporta como ignorado: da igual que la `!` este o no en el
        # `.gitignore`. Se comprobo — la primera version de este test pasaba
        # con la negacion borrada, porque el lockfile ya estaba en el indice y
        # por lo tanto "no estaba ignorado" sin que nadie lo hubiera exencionado.
        # Con `--no-index` git responde a la pregunta que importa: ¿que regla
        # casa con este camino?
        r = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", str(LOCKFILE)],
            cwd=RAIZ, capture_output=True,
        )
        assert r.returncode != 0, (
            "front/package-lock.json esta en .gitignore de verdad, aunque el "
            ".gitignore de este repo diga que se versiona.\n"
            "Los Dockerfiles del front instalan con 'npm ci', que aborta sin "
            "lockfile: el build pasaria en esta maquina (donde el archivo esta "
            "en disco) y fallaria en cualquier clone limpio.\n"
            "Revisa ~/.gitignore_global: si trae 'package-lock.json', la "
            "exencion del repo tiene que reinstated con '!' despues de la "
            "regla que lo ignora."
        )

    def test_el_lockfile_esta_en_el_indice(self):
        """No basta con que no este ignorado: tiene que estar versionado.

        Un archivo con `git add -f` pasa el test de arriba y aun asi no
        sobrevive al siguiente `git add .`. Este test es el que distingue
        "versionado" de "presente".
        """
        r = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(LOCKFILE)],
            cwd=RAIZ, capture_output=True,
        )
        assert r.returncode == 0, (
            "front/package-lock.json NO esta versionado. Si esta en disco pero "
            "no en el indice, un clone limpio no lo tendra y 'npm ci' fallara. "
            "Agregalo con 'git add front/package-lock.json' (ya no hace falta -f "
            "ahora que el .gitignore lo exenciona)."
        )


class TestElLockfileEsUtil:
    """Un lockfile desincronizado rompe el build igual que uno ausente."""

    def test_no_se_desincroniza_de_package_json(self):
        pj = json.loads((RAIZ / "front/package.json").read_text(encoding="utf-8"))
        lf = json.loads((RAIZ / LOCKFILE).read_text(encoding="utf-8"))
        raiz_lock = lf.get("packages", {}).get("", {})

        for campo in ("dependencies", "devDependencies"):
            declarados = pj.get(campo, {})
            en_lock = raiz_lock.get(campo, {})
            # `npm ci` aborta si el lock menciona paquetes que package.json ya
            # no pide, o al reves. Solo comparamos los nombres: las versiones
            # las fija el lock a proposito, es lo que lo hace un lockfile.
            huerfanos = sorted(set(declarados) - set(en_lock))
            sobrantes = sorted(set(en_lock) - set(declarados))
            assert not huerfanos and not sobrantes, (
                f"package.json y package-lock.json no coinciden en {campo}.\n"
                f"  en package.json y ausentes del lock: {huerfanos}\n"
                f"  en el lock y ausentes de package.json: {sobrantes}\n"
                "'npm ci' aborta con 'lock file does not satisfy' ante esta "
                "divergencia. Se arregla con 'npm install' en front/ y "
                "commitear el lock que genera."
            )

    def test_el_lock_declara_una_version_compatible(self):
        lf = json.loads((RAIZ / LOCKFILE).read_text(encoding="utf-8"))
        version = lf.get("lockfileVersion", 0)
        assert version >= 2, (
            f"lockfileVersion={version}. 'npm ci' exige lockfileVersion >= 1, y "
            "las versiones 2 y 3 son las que admiten omitir dependencias de "
            "plataforma opcional (los opcionales de rollup en ARM64, que es "
            "justo lo que necesitan los Dockerfiles: `--include=optional`)."
        )


class TestElBuildDependeDelLockfile:
    """El porque de lo de arriba, para que no se mantenga por costumbre sin leer."""

    @pytest.mark.parametrize("dockerfile", DOCKERFILES_DEL_FRONT, ids=lambda p: p.name)
    def test_instala_con_npm_ci(self, dockerfile):
        texto = dockerfile.read_text(encoding="utf-8")
        lineas = [ln.strip() for ln in texto.splitlines()]
        # Solo las lineas que instalan: un `npm ci` mentioned en un comentario
        # no instala nada.
        instala = [ln for ln in lineas if ln.startswith("RUN npm")]
        assert instala, f"{dockerfile.name} no tiene ninguna linea 'RUN npm'"
        assert any("npm ci" in ln for ln in instala), (
            f"{dockerfile.name} instala con {[ln for ln in instala]}, no con "
            "'npm ci'. Sigue siendo valido (con 'npm install' el lockfile es "
            "recomendable, no obligatorio), pero entonces el lockfile versionado "
            "ya no es lo que fija las versiones y conviene saberlo a proposito."
        )

    @pytest.mark.parametrize("dockerfile", DOCKERFILES_DEL_FRONT, ids=lambda p: p.name)
    def test_copia_el_lock_al_contexto_de_build(self, dockerfile):
        """`COPY front/package*.json` incluye el lock; `COPY front/package.json` no.

        El COPY se evalua contra el contexto de build, no contra git. Si el
        lockfile no esta en el repo, el patron lo agarra cuando esta en disco y
        falla cuando no: un fallo que depende del estado de la maquina.
        """
        texto = dockerfile.read_text(encoding="utf-8")
        copia_package = [ln for ln in texto.splitlines() if "COPY" in ln and "package" in ln]
        assert copia_package, f"{dockerfile.name} no copia los package*.json"
        assert any("package*.json" in ln for ln in copia_package), (
            f"{dockerfile.name} copia {[ln.strip() for ln in copia_package]}, "
            "que no trae el lockfile. Con 'npm ci' eso es EUSAGE."
        )
