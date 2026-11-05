"""Que `make up` instale los modelos, y que no los baje dos veces.

MEDIDO el 2026-10-15: la `.env` declaraba `OLLAMA_VISION_MODEL=qwen2.5vl:3b` y el
volumen de Ollama **no tenia ningun modelo**. El sistema escaneaba solo con
Tesseract, su techo de exactitud era el del OCR, y no habia forma de enterarse:
`/scan/ia` reportaba `faltan: []` y `disponible: true` con cero modelos, porque
para Ollama el calculo de `faltan` estaba cortocircuitado con un `pass`.

Descargar a mano en cada maquina es un paso que se olvida y que nadie nota que
falta. `scripts/instalar_modelos.sh` lo hace parte de `make up`, y estos tests son
sobre **el script y el servicio**, que es donde estuvo cada uno de los tres
defectos de abajo.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = RAIZ / "docker-compose.yml"
SCRIPT = RAIZ / "scripts" / "instalar_modelos.sh"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# El servicio de Compose
# ---------------------------------------------------------------------------


class TestElServicioDeModelos:
    def test_existe(self, compose):
        assert "modelos" in compose["services"], (
            "los modelos tienen que bajar solos. El sintoma de que faltan es "
            "que el sistema escanea solo con OCR y su techo de exactitud baja "
            "sin que nada lo diga."
        )

    def test_esta_en_la_red_de_los_demas(self, compose):
        """MEDIDO el 2026-10-15: sin `networks`, este servicio no resolvia `ollama`.

        Compose mete un servicio sin `networks` en la red **por defecto** del
        proyecto, que no es `expense_network`. El sintoma es el peor posible para
        diagnosticar: el script dice "Ollama no respondio" y la respuesta es que
        si responde — lo que no responde esta cosa es que no esta en su red.
        """
        redes = compose["services"]["modelos"].get("networks")
        assert redes and "expense_network" in redes, (
            "el servicio de modelos tiene que estar en `expense_network`: si no, "
            "no resuelve `ollama` y el script se queda esperando sin explicarlo"
        )

    def test_depende_de_ollama(self, compose):
        depende = compose["services"]["modelos"]["depends_on"]
        assert "ollama" in depende, "descargar un modelo exige que ollama este de pie"

    def test_el_api_no_levanta_hasta_que_termine(self, compose):
        """`service_completed_successfully`, no `service_started`.

        Aqui no vale "empezo a trabajar", vale "termino de bajar lo que estaba
        declarado". El script sale con 0 aunque no pueda descargar —la app sin
        modelo escanea igual con OCR—, asi que esta espera no puede dejar el
        sistema sin levantar por una descarga fallida.
        """
        depende = compose["services"]["expense-api"]["depends_on"]
        condicion = (depende.get("modelos") or {}).get("condition")
        assert condicion == "service_completed_successfully", (
            f"el API depende de 'modelos' con '{condicion}': tiene que ser "
            "'service_completed_successfully'"
        )

    def test_trae_los_modelos_por_env_file_y_no_por_interpolacion(self, compose):
        """MEDIDO el 2026-10-15: con `${OLLAMA_MODEL}` descargo `moondream` mientras
        la app usaba `qwen2.5:3b`.

        Compose resuelve `${VAR}` **primero del entorno del shell y despues de la
        `.env`**, y en esta maquina el shell tiene `OLLAMA_MODEL=moondream`
        exportado. El `.env` de la app no pasa por interpolacion: lo lee
        `env_file`. Resultado: dos servicios leyendo la "misma" variable con
        reglas de precedencia distintas, y el que no gana es invisible.
        """
        servicio = compose["services"]["modelos"]
        # `environment` puede ser lista (`- K=V`) o mapa, y Compose acepta las
        # dos. Un test que solo entienda una se pasa con la otra forma puesta.
        bruto = servicio.get("environment") or {}
        if isinstance(bruto, list):
            entorno = " ".join(str(v) for v in bruto)
        else:
            entorno = " ".join(f"{k}={v}" for k, v in bruto.items())

        assert "OLLAMA_MODEL" not in entorno, (
            "el nombre del modelo no puede llegar por `environment:` con "
            f"interpolacion: se resuelve desde el shell antes que desde la `.env`, "
            f"y descarga un modelo que la app no usa. Se lee '{entorno}'"
        )
        assert servicio.get("env_file"), (
            "los modelos tienen que llegar por `env_file`, el mismo mecanismo y en "
            "el mismo orden que usa `expense-api`"
        )


# ---------------------------------------------------------------------------
# El script
# ---------------------------------------------------------------------------


def _ollama_falso(directorio: Path, instalados: list[str]) -> Path:
    """Un `ollama` de mentira en el PATH.

    Es lo que permite probar el script entero —espera, decision, `pull`— sin red
    ni modelo de 3 GB. El script no sabe que es falso: solo llama al binario.
    """
    Fake = directorio / "ollama"
    Fake.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        f"  list) printf 'NAME\\tID\\tSIZE\\n'; for m in {' '.join(instalados)}; do\n"
        "           printf '%s\\t123\\t1.0 GB\\n' \"$m\"; done ;;\n"
        "  pull) echo \"PULL $2\"; echo \"  (falso, no se descarga nada)\"; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    Fake.chmod(Fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return Fake


def _correr(instalados: list[str], **entorno: str) -> str:
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"modelos-test-{os.getpid()}-{len(instalados)}"
    tmp.mkdir(exist_ok=True)
    _ollama_falso(tmp, instalados)
    try:
        proc = subprocess.run(
            ["/bin/sh", str(SCRIPT)],
            env={
                "PATH": f"{tmp}:/usr/bin:/bin",
                "HOME": str(tmp),
                # Los dos modelos declarados, como los recibe el servicio real.
                "OLLAMA_MODEL": "qwen2.5:3b",
                "OLLAMA_VISION_MODEL": "qwen2.5vl:3b",
                **entorno,
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc.stdout + proc.stderr
    finally:
        for f in tmp.iterdir():
            f.unlink()
        tmp.rmdir()


class TestElScriptNoVuelveABajarLoQueYaEsta:
    """MEDIDO el 2026-10-15: `make up` bajaba 3.2 GB en cada corrida.

    Y se escribio mal dos veces con el mismo sintoma, que es lo que lo hace un
    bug de comprobacion y no del `ollama pull`:

    1. `[ -z "$x" ] && continue` dentro de un `while` en una tuberia, con
       `set -e`: cada `&&` que evalua a falso devuelve 1 y `set -e` sale del
       subshell en la primera linea, antes de comparar nada.
    2. La logica al reves: se devolvia 1 cuando el modelo SI estaba instalado, y
       el `if` lo leia como "falta".

    Las dos se ven igual desde fuera —"descarga otra vez"— y en las dos lo que
    estaba roto era la pregunta, no la descarga.
    """

    def test_no_descarga_lo_que_ya_esta(self):
        salida = _correr(["qwen2.5:3b", "qwen2.5vl:3b"])

        assert "PULL" not in salida, (
            "los dos modelos ya estaban instalados y aun asi los descarga: la "
            f"comprobacion de 'ya esta' no funciona. Salida:\n{salida}"
        )
        assert "qwen2.5:3b ya esta" in salida
        assert "qwen2.5vl:3b ya esta" in salida

    def test_compara_sin_etiqueta(self):
        """`ollama list` devuelve `moondream:latest` y la `.env` dice `moondream`.

        Comparar el texto entero haria que un modelo instalado pareciera ausente,
        que es el mismo bug de emparejar etiquetas que se corrigio en la medicion
        de exactitud.
        """
        salida = _correr(["qwen2.5:3b:latest", "qwen2.5vl:3b:latest"])

        assert "PULL" not in salida, (
            f"un modelo con etiqueta fue reportado como ausente:\n{salida}"
        )

    def test_descarga_el_que_falta_de_verdad(self):
        """El otro lado: si falta uno, tiene que bajarlo.

        Sin esto, el "no descarga lo que ya esta" se cumple siempre, tambien
        cuando no deberia, y el servicio es decorativo.
        """
        salida = _correr(["qwen2.5:3b"])

        assert "PULL qwen2.5vl:3b" in salida, (
            f"faltaba el modelo de vision y no lo bajo:\n{salida}"
        )
        assert "PULL qwen2.5:3b" not in salida, "el de texto ya estaba"
        assert "qwen2.5vl:3b instalado" in salida

    def test_sin_ningun_modelo_descarga_los_dos(self):
        salida = _correr([])

        assert "PULL qwen2.5:3b" in salida
        assert "PULL qwen2.5vl:3b" in salida

    def test_un_modelo_que_falta_no_impide_al_otro(self):
        """MEDIDO el 2026-10-15: la app sin modelo escanea igual con OCR.

        Un `pull` que falla por red no puede dejar sin sistema a alguien que solo
        quiere escanear sus comprobantes. El script avisa y sale con 0.
        """
        salida = _correr(["qwen2.5:3b"], OLLAMA_VISION_MODEL="modelo/que:no-existe")

        assert "Modelos listos" in salida or "AVISO" in salida, (
            f"con un modelo que no se puede bajar, el script tiene que terminar "
            f"de todos modos:\n{salida}"
        )