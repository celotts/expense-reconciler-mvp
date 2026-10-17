"""El arranque depende de lo que digan los healthchecks, y estos tests los fijan.

## POR QUE UN ARCHIVO DE TESTS SOBRE UN YAML

`docker-compose.yml` no es configuracion: es la **politica de arranque** de la
app, y la mayoria de sus lineas son decisiones que un contenedor caido no puede
defender por si solo. Un `depends_on` mal escrito no da error, da un sistema que
levanta "bien" y no sirve.

Los tres defectos que este archivo.exists para que no vuelvan son **medidos**,
no hipoteticos:

1. El healthcheck de Postgres era `pg_isready`, que responde "aceptando
   conexiones" en el segundo **1.3** mientras `init.sql` sigue corriendo; la
   tabla `tickets` aparecio en el **21**. La API declaraba healthy y no podia
   leer una sola fila.
2. `/health` respondia **200 con la base parada**, asi que el healthcheck de la
   API no podia notar la caida.
3. El front usaba `depends_on: - expense-api`, que es `service_started`: nginx
   levanta antes de que uvicorn escuche.

Cada test de aqui mira una de esas lineas. Un YAML no se puede probar con un
cliente HTTP, asi que se lee el archivo: es lo unico que hay.
"""

from pathlib import Path

import re
import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = RAIZ / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    """El YAML ya parseado.

    Se parsea con `yaml.safe_load` y no se busca texto: un `depends_on` puede
    escribirse de dos formas legitimas (`- api` y `api:` con `condition:`) y un
    `in` sobre el texto daria verde con cualquiera de las dos, que es
    justamente el defecto que se quiere cazar.
    """
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _servicio(compose: dict, nombre: str) -> dict:
    return compose["services"][nombre]


def _lineas_de_receta(objetivo: str) -> list[str]:
    """Las lineas de COMANDO de un objetivo del Makefile.

    Una receta son las lineas que empiezan con tabulador. Acaba en la primera
    linea sin tabulador que no sea un comentario, que es donde empieza el
    siguiente objetivo. Los comentarios con `#` se descartan, incluidos los que
    estan dentro de la receta.

    Existe por una razon medida: la primera version de `test_up_usa_wait` hacia
    `"--wait" in makefile` sobre el archivo entero, y daba **verde con el
    arreglo quitado**, porque `--wait` esta escrito en los comentarios que
    explican el arreglo. Un test que lee un comentario no prueba el comando.
    """
    lineas = (RAIZ / "Makefile").read_text(encoding="utf-8").splitlines()
    receta: list[str] = []
    dentro = False

    for linea in lineas:
        if not dentro:
            # Un objetivo empieza en la columna 0. `up: build` trae su
            # dependencia en la misma linea y cuenta como entrar.
            if linea.split(":")[0].strip() == objetivo and ":" in linea:
                dentro = True
            continue

        if linea.lstrip().startswith("#"):
            continue  # comentario: no es comando
        if linea.startswith("\t"):
            receta.append(linea)
            continue
        break  # dedented y no comentario: empieza otro objetivo

    return receta


def _condicion_de(depende: dict, servicio: str) -> str | None:
    """La condicion de `depends_on`, o `None` si esta en forma corta.

    Compose acepta dos formas y significan cosas distintas:

        depends_on:                       depends_on:
          - expense-api                     expense-api:
                                             condition: service_healthy

    La de la izquierda es `service_started`. Traducirla a `None` y deixar que
    sea el test el que lo diga evita que un `TypeError` de subscript a string
    sea lo unico que se lea cuando alguien deshace la defensa: un test que muere
    con `list indices must be integers` no dice que rompiste el arranque.
    """
    if isinstance(depende, dict):
        return depende.get(servicio, {}).get("condition")
    # Forma corta: es una lista de nombres, y lo que se pide ahi es `started`.
    if isinstance(depende, list) and servicio in depende:
        return "service_started"
    return None


def _test_de_healthcheck(servicio: dict) -> str:
    """El comando del healthcheck, en texto.

    Compose acepta el healthcheck como lista o como mapping, asi que se
    normaliza aqui. Sin esto, el test pasaria con una forma y fallaria con la
    otra sin que cambiara el comportamiento.
    """
    bruto = servicio["healthcheck"]["test"]
    if isinstance(bruto, dict):  # forma mapping
        bruto = bruto.get("CMD-SHELL") or bruto.get("CMD") or ""
    if isinstance(bruto, list):
        # Se ignoran "CMD-SHELL" / "CMD" y se junta el resto.
        bruto = " ".join(str(p) for p in bruto[1:]) if len(bruto) > 1 else ""
    return bruto


def _sin_variables(linea: str) -> str:
    """La linea con `$(...)` y `${...}` Developmentgaps, en un solo espacio.

    MEDIDO: `make up` grew `$(COMPOSE_FILES)` —para los perfiles de multi-entorno
    de `6f486f5`— y los dos tests de abajo dejaron de ver el `--wait` que sigue
    ahi. La receta es `docker compose $(COMPOSE_FILES) up -d --wait`, y el test
    buscaba la cadena `"docker compose up"`: sin la interpolacion esa cadena no
    aparece nunca.

    Que un test se ponga rojo por una refactorizacion que **no toco la defensa**
    es la mitad del problema; lo otro es que el arreglo no puede ser relajar la
    comprobacion (`"--wait" in linea` daria verde con un `--wait` en un
    comentario). Por eso se quitan las variables y se sigue exigiendo la
    forma completa del comando.
    """
    sin_vars = re.sub(r"\$\([^)]*\)", " ", linea)
    sin_vars = re.sub(r"\$\{[^}]*\}", " ", sin_vars)
    return re.sub(r"\s+", " ", sin_vars).strip()


class TestElHealthcheckDePostgresPreguntaPorElEsquema:

    def test_no_es_solo_pg_isready(self, compose):
        """LA DEFENSA 1. `pg_isready` solo no dice que la base esté lista.

        MEDIDO: con un `sleep 20` inyectado en el init, `pg_isready` respondio
        "accepting connections" en t=1.3s y la tabla `tickets` no existio hasta
        t=21s. Con el healthcheck de solo `pg_isready`, `expense-api` levanta en
        t=1.3s y su primera consulta dice `relation "tickets" does not exist`.
        """
        test = _test_de_healthcheck(_servicio(compose, "postgres-reconciler"))

        assert "pg_isready" in test, (
            "el healthcheck tiene que seguir exigiendo que Postgres responda"
        )
        assert "to_regclass" in test, (
            "el healthcheck de Postgres tiene que preguntar por el ESQUEMA "
            "(`to_regclass`), no solo por el socket: durante el init la "
            "conexion responde antes de que existan las tablas. MEDIDO: 1.3s "
            "de 'aceptando conexiones' contra 21s hasta que existe `tickets`"
        )

    def test_pregunta_por_una_tabla_que_existe_siempre(self, compose):
        """`tickets` y no una tabla cualquiera.

        `tickets` es la primera que crea `init.sql` y la que todos los endpoints
        tocan. Preguntar por una tabla que solo existe en una migracion tardia
        haria que una base vieja no pueda considerarse sana nunca.
        """
        test = _test_de_healthcheck(_servicio(compose, "postgres-reconciler"))

        assert "public.tickets" in test, (
            "el healthcheck tiene que preguntar por `public.tickets`, que "
            "existe desde `init.sql` y en toda base migrada"
        )

    def test_tiene_start_period_para_el_init(self, compose):
        """Sin `start_period`, los reintentos se gastan durante `init.sql`.

        `interval: 5s` con `retries: 5` son 25 segundos. El init de una base
        nueva cae dentro de esa ventana, y si los fallos cuentan, el contenedor
        pasa a `unhealthy` mientras `depends_on: service_healthy` lo espera: un
        deadlock silencioso que solo aparece en la instalacion nueva, que es
        justo cuando no hay datos que perder y mas sitio para probarlo.
        """
        servicio = _servicio(compose, "postgres-reconciler")

        assert servicio["healthcheck"].get("start_period"), (
            "el healthcheck de Postgres necesita `start_period`: sin el, los "
            "5 reintentos de 5s se gastan corriendo `init.sql` y el contenedor "
            "queda unhealthy con `depends_on: service_healthy` esperandolo"
        )


class TestElHealthcheckDeLaApiConsultaLaBase:

    def test_el_healthcheck_de_la_api_no_es_solo_el_proceso(self, compose):
        """LA DEFENSA 2. La API tiene que poder notar que la base cayo.

        MEDIDO: con `expense_pgvector` parado, `/health` respondia 200 y el
        contenedor se declaraba `healthy` sin poder servir ninguna ruta. Este
        healthcheck es la condicion de `depends_on: service_healthy` del front,
        asi que la mentira decidia el arranque del front.
        """
        test = _test_de_healthcheck(_servicio(compose, "expense-api"))

        assert "/health" in test, (
            "el healthcheck de la API debe seguir pasando por /health"
        )

    def test_usa_fail_para_no_aceptar_un_503(self, compose):
        """`curl -fsS` y no `curl -s`.

        Sin `--fail`, curl sale con 0 aunque la respuesta sea 503: el TCP
        funciono, que es todo lo que curl mira. La defensa de que `/health`
        consulte la base estaria en el codigo, pero el healthcheck de Docker
        aceptaria el 503 como si fuera exito — o sea, la defensa sin el codigo
        que la hace cumplir.
        """
        test = _test_de_healthcheck(_servicio(compose, "expense-api"))

        assert "-f" in test or "--fail" in test, (
            "el healthcheck de la API necesita `curl -f`: sin el, curl sale con "
            "0 ante un 503 y el contenedor se declara healthy con la base caida"
        )

    def test_tiene_timeout_para_no_colgarse(self, compose):
        """Sin `--max-time`, un `/health` colgado deja el healthcheck esperando
        hasta el `timeout` del healthcheck y Docker lo mata como fallo, que no
        es lo que pasa: la API esta viva y tardando. Con el timeout de curl, el
        comando decide."""
        test = _test_de_healthcheck(_servicio(compose, "expense-api"))

        assert "--max-time" in test, (
            "el healthcheck de la API necesita `--max-time`: curl sin timeout "
            "puede colgarse y el fallo se reporta como API caida"
        )


class TestElOrdenDeArranque:

    def test_el_front_espera_a_que_la_api_este_sana(self, compose):
        """LA DEFENSA 3. `service_healthy`, no la forma corta.

        MEDIDO: con `depends_on: - expense-api` (que es `service_started`),
        nginx levanta en cuanto el contenedor de la API EXISTE, antes de que
        uvicorn escuche. El front respondia 200 en `/` y 502 en todo `/api/`,
        y el sintoma no decia nada del orden de arranque.
        """
        depends = _servicio(compose, "expense-front")["depends_on"]
        condicion = _condicion_de(depends, "expense-api")

        assert condicion == "service_healthy", (
            f"el front espera '{condicion}': tiene que ser 'service_healthy'. "
            "Con 'service_started' (que es lo que significa la forma corta, "
            "`depends_on: - expense-api`) nginx levanta antes de que uvicorn "
            "escuche y el front queda 200 en `/` y 502 en todo `/api/`"
        )

    def test_la_api_espera_a_que_postgres_este_sano(self, compose):
        """La cadena completa: front -> api -> postgres.

        Sin esta pieza, `expense-api` puede levantar contra una base que
        todavia no tiene tablas (ver `TestElHealthcheckDePostgres...`).
        """
        depends = _servicio(compose, "expense-api")["depends_on"]

        assert _condicion_de(depends, "postgres-reconciler") == "service_healthy", (
            "la API tiene que esperar a que Postgres este healthy, no solo a que "
            "el contenedor exista"
        )

    def test_el_front_tiene_healthcheck_propio(self, compose):
        """Sin healthcheck en el front, `--wait` no tiene nada que esperarle y lo
        daria por listo en cuanto el proceso exista — el mismo defecto un nivel
        mas abajo."""
        servicio = _servicio(compose, "expense-front")

        assert servicio.get("healthcheck"), (
            "el front necesita healthcheck: sin el, `docker compose up --wait` "
            "no espera nada en el y lo declara listo al arrancar el proceso"
        )

    def test_el_front_no_espera_a_ollama_para_servir(self, compose):
        """La IA no es una condicion de arranque, y no debe serlo.

        Ollama tarda en responder (carga modelos) y la app degrada a lectura por
        PDF/OCR sin el. Si el front dependiera de `ollama: service_healthy`,
        hacer `make up` sin los modelos descargados dejaria el front sin
        levantar, y la lectura determinista —la que no usa IA— dejaria de
        funcionar. `ollama` se queda en `service_started`.
        """
        depends = _servicio(compose, "expense-api")["depends_on"]

        assert _condicion_de(depends, "ollama") == "service_started", (
            "la API no debe esperar a que ollama este healthy: la app degrada "
            "sin IA y el escaner determinista tiene que seguir funcionando"
        )


class TestMakeUpEspera:

    def test_up_usa_wait(self):
        """`make up` tiene que esperar, no devolver el control antes de servir.

        `docker compose up -d` devuelve en cuanto los contenedores EXISTEN. Sin
        `--wait`, quien lo corre abre `localhost:3000` en el segundo 2 y ve el
        front sin API — que es el sintoma que este trabajo arregla.

        ## POR QUE SE MIRA LA RECETA Y NO EL ARCHIVO ENTERO

        La primera version de este test hacia `"--wait" in makefile`, y eso
        daba **verde con el arreglo quitado**: la palabra `--wait` esta escrita
        en los comentarios de arriba, que explican por que hace falta. Un test
        que lee un comentario no esta probando el comando, esta probando que
        alguien escribio sobre el comando — y el comentario sobrevive a la
        mutacion.

        MEDIDO: borrando `--wait` de la linea de `docker compose up` y dejando
        los comentarios intactos, el test paso 12/12. Por eso se lee la
        **receta** de `up`, que es lo que se ejecuta, y no el texto.

        Y la comparacion es sobre la linea con `$(...)` interpolado a un
        espacio (`_sin_variables`), no sobre el texto crudo: `make up` ahora
        lleva `$(COMPOSE_FILES)` y el `"docker compose up"` literal no aparece.
        """
        receta = _lineas_de_receta("up")

        assert any(
            "docker compose up" in _sin_variables(l) and "--wait" in l for l in receta
        ), (
            "la receta de `up` no lleva `--wait` en su `docker compose up`: "
            "devuelve el control antes de que la app este servible. (Ojo: este "
            "test mira la RECETA, no el archivo entero — `--wait` si aparece en "
            "los comentarios, y un test que lee comentarios no prueba el "
            "comando.)"
        )

    def test_up_force_usa_wait_tambien(self):
        """`up-force` reconstruye sin cache y tarda mas, asi que su `--wait` es
        el mismo: mas lento no significa mas listo."""
        receta = _lineas_de_receta("up-force")

        assert any(
            "docker compose up" in _sin_variables(l) and "--wait" in l for l in receta
        ), (
            "`up-force` tambien necesita `--wait`: `--no-cache` tarda mas, no "
            "significa que la app quede mas lista"
        )

    def test_up_sigue_dependiendo_de_build(self):
        """`up: build` y no solo `up`.

        `build` es una **dependencia** (la palabra antes de `:`), no una linea
        de la receta, asi que `_lineas_de_receta` no la ve. Sin esto, `make up`
        levanta con imagenes viejas —que es justo el motivo por el que `up`
        existe separado de `docker compose up`— y `requirements.txt` con un
        paquete nuevo deja la API con imports rotos sin que nada lo diga.

        El fallo que caza es de los que no se ven: no hay error, hay una imagen
        de hace tres dias sirviendo.
        """
        makefile = (RAIZ / "Makefile").read_text(encoding="utf-8")

        lineas_de_up = [
            l for l in makefile.splitlines()
            if l.startswith("up:") and not l.startswith("up-")
        ]
        assert lineas_de_up, "no existe el objetivo `up` en el Makefile"
        assert "build" in lineas_de_up[0], (
            f"`{lineas_de_up[0].strip()}` no declara `build`: `make up` levanta "
            "con la imagen anterior y un `requirements.txt` nuevo deja la API "
            "con imports rotos, sin error visible"
        )