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

    def test_conecta_con_el_usuario_que_existe(self, compose):
        """LA DEFENSA 4, y la que estaba rota.

        MEDIDO el 2026-11-04: `environment:` declara `POSTGRES_USER: admin` y el
        healthcheck hacia `psql -U postgres`. Ese rol no existe en esta base —el
        entrypoint crea solo `POSTGRES_USER`—, asi que el `psql` moria con
        `FATAL: role "postgres" does not exist`, el contenedor quedaba
        `unhealthy` para siempre y `make up` acababa en `dependency
        postgres-reconciler failed to start`, con la API sin arrancar.

        Lo que hacia esto invisible es que `pg_isready -U postgres` **no
        autentica**: devuelve 0 aunque el rol no exista. La mitad que delata el
        error es el `psql`, y por eso el healthcheck parecia correcto.

        Se exige `$$POSTGRES_USER` y no el literal `admin` a proposito: el
        usuario ya esta declarado en `environment:`, y un segundo sitio donde
        escribirlo es un sitio mas donde puede dejar de coincidir.
        """
        servicio = _servicio(compose, "postgres-reconciler")
        test = _test_de_healthcheck(servicio)

        assert "-U postgres" not in test, (
            "el healthcheck no puede conectar como `postgres`: el rol lo crea "
            "`POSTGRES_USER` (admin), no el entrypoint. MEDIDO: `FATAL: role "
            '"postgres" does not exist` -> unhealthy para siempre'
        )
        assert test.count("$$POSTGRES_USER") == 2, (
            "las DOS mitades del healthcheck tienen que usar el usuario de "
            "`environment:`; si una lo escribe a mano, vuelve a divergir"
        )
        assert "POSTGRES_USER" in servicio["environment"], (
            "`$$POSTGRES_USER` se expande DENTRO del contenedor: si el servicio "
            "deja de declararla, el healthcheck se queda sin usuario y el "
            "`psql` falla con un error que no menciona el healthcheck"
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

    @staticmethod
    def _es_up(linea: str) -> bool:
        # Los `--env-file` van entre `compose` y `up`, asi que el literal
        # "docker compose up" ya no aparece. Se comprueban las dos palabras
        # separadas: quitar `--wait` sigue matando el test.
        normal = _sin_variables(linea)
        return "docker compose" in normal and " up " in f" {normal} "

    @staticmethod
    def _tiene_wait(linea: str) -> bool:
        # `--wait` como TOKEN, no como substring: `--wait-timeout 120` contiene
        # la cadena "--wait" y daba verde con el `--wait` quitado. Una defensa
        # que no puede cambiar el veredicto no esta defendiendo.
        return bool(re.search(r"(?:^|\s)--wait(?:\s|$)", linea))

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

        assert any(self._es_up(l) and self._tiene_wait(l) for l in receta), (
            "la receta de `up` no lleva `--wait` en su `docker compose up`: "
            "devuelve el control antes de que la app este servible. (Ojo: este "
            "test mira la RECETA, no el archivo entero — `--wait` si aparece en "
            "los comentarios, y un test que lee comentarios no prueba el "
            "comando. Tampoco cuenta `--wait-timeout`: es otra bandera.)"
        )

    def test_up_force_usa_wait_tambien(self):
        """`up-force` reconstruye sin cache y tarda mas, asi que su `--wait` es
        el mismo: mas lento no significa mas listo."""
        receta = _lineas_de_receta("up-force")

        assert any(self._es_up(l) and self._tiene_wait(l) for l in receta), (
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


class TestLosEnvFileDelArranque:
    """Compose no interpola desde `env_file:`, solo desde `--env-file`.

    El secreto esta escrito una sola vez, en `.env.local`. Si una sola receta
    olvida pasarlo, Compose falla al interpolarten antes de crear el
    contenedor — o peor: si el shell tiene la variable exportada, arranca aqui
    y falla en la maquina de quien no la tiene. Por eso se mira CADA comando de
    la receta, no si la cadena aparece en algun comentario del Makefile.
    """

    @staticmethod
    def _comandos(objetivo: str) -> list[str]:
        return [
            l for l in _lineas_de_receta(objetivo)
            if "docker compose" in _sin_variables(l)
        ]

    def test_up_pasa_los_dos_env_file_en_cada_comando(self):
        for objetivo in ("up", "up-force"):
            comandos = self._comandos(objetivo)
            assert comandos, f"`{objetivo}` no tiene ningun comando de docker compose"
            for comando in comandos:
                normal = _sin_variables(comando)
                assert "--env-file .env --env-file .env.local" in normal, (
                    f"`{objetivo}` tiene un `docker compose` sin los dos --env-file: "
                    f"{normal}"
                )

    def test_ninguna_receta_que_parsea_compose_olvida_los_env_file(self):
        for objetivo in ("build", "up", "up-force", "down", "ps", "logs", "clean"):
            comandos = self._comandos(objetivo)
            assert comandos, f"`{objetivo}` no tiene ningun comando de docker compose"
            for comando in comandos:
                normal = _sin_variables(comando)
                assert "--env-file .env --env-file .env.local" in normal, (
                    f"`{objetivo}` abre Compose sin los dos --env-file: {normal}"
                )

    @staticmethod
    def _entorno(compose: dict, nombre: str) -> str:
        # `postgres-reconciler` declara el entorno como mapping; los otros,
        # como lista. Mirar solo las claves daria verde con la interpolacion
        # quitada, porque la clave POSTGRES_PASSWORD sigue existiendo.
        entorno = _servicio(compose, nombre).get("environment", [])
        valores = entorno.values() if isinstance(entorno, dict) else entorno
        return " ".join(str(v) for v in valores)

    def test_la_api_sigue_recibiendo_el_secret_key_por_env_file(self, compose):
        """`--env-file` solo interpola `${...}`; NO inyecta variables en el contenedor.

        Quitar `.env.local` de `env_file:` deja a `config.py` sin `SECRET_KEY`
        dentro del contenedor: genera una clave efimera y cada `--reload` cierra
        las sesiones. El sintoma es "la sesion se cae de vez en cuando", que no
        apunta al compose. Los dos mecanismos tienen que seguir vivos.
        """
        entorno = _servicio(compose, "expense-api")["env_file"]
        rutas = [e["path"] if isinstance(e, dict) else e for e in entorno]
        assert ".env" in rutas and ".env.local" in rutas, (
            f"la API no recibe los dos archivos por env_file: {rutas}"
        )

    def test_compose_interpola_postgres_password_y_no_password_inicial(self, compose):
        texto = COMPOSE.read_text(encoding="utf-8")
        assert "${password_inicial" not in texto, (
            "`password_inicial` volvio a ser la fuente del secreto: debe ser un "
            "alias de `${POSTGRES_PASSWORD}`, no la variable que se interpolated"
        )
        for nombre in ("postgres-reconciler", "migraciones", "expense-api"):
            entorno = self._entorno(compose, nombre)
            assert "${POSTGRES_PASSWORD:?" in entorno, (
                f"{nombre} no toma la contrasena de POSTGRES_PASSWORD: sin el :? "
                "no hay mensaje accionable cuando falta .env.local"
            )
        entorno_api = self._entorno(compose, "expense-api")
        assert "password_inicial=${POSTGRES_PASSWORD:?" in entorno_api, (
            "app/main.py siembra el admin leyendo `password_inicial`: compose "
            "tiene que inyectarlo como alias, sin escribir el secreto dos veces"
        )

    def test_el_wrapper_del_host_lee_config_de_env_y_pasa_los_env_file(self):
        script = (RAIZ / "scripts/migrar.sh").read_text(encoding="utf-8")

        assert "s/^POSTGRES_USER=//p' .env" in script
        assert "s/^POSTGRES_DB=//p' .env" in script
        assert "s/^POSTGRES_USER=//p' .env.local" not in script, (
            "usuario y base son configuracion: leerlos de .env.local los hace "
            "caer al default sin avisar"
        )
        assert 'COMPOSE_ENV=(--env-file .env --env-file .env.local)' in script
        assert 'docker compose "${COMPOSE_ENV[@]}" exec' in script
        assert 'docker compose "${COMPOSE_ENV[@]}" run' in script
        assert "Falta POSTGRES_PASSWORD en .env.local" in script, (
            "sin este preflight, un fallo de interpolacion se reportaba como "
            "'La base no respondio' y mandaba a mirar el contenedor"
        )


class TestLasMigracionesSeAplicanAlArrancar:
    """`init.sql` solo corre con el volumen VACIO.

    MEDIDO el 2026-11-20: la base en vivo venia de otra rama, tenia registradas
    `0019_lotes.sql`, `0020` y `0021`, y no tenia las 11 columnas que anade
    `0019_extraccion_multipaís_y_controles.sql`. `GET /scan/files` daba 500 con
    `column tickets.pais does not exist` y el Escáner mostraba "Error
    desconocido". Aplicar la migracion con `psql` a mano arreglaba el
    contenedor, no el proyecto: al recrearlo volveria a estar roto.

    La defensa es un servicio de una pasada del que depende la API, con el
    mismo patron que `modelos`. Si se quita, la API arranca contra una base
    desactualizada y vuelve el 500.
    """

    def test_existe_el_servicio_de_migraciones(self, compose):
        assert "migraciones" in compose["services"], (
            "tiene que existir un servicio `migraciones`: sin el, `init.sql` "
            "solo corre en una base nueva y una base existente se queda sin "
            "las columnas de las migraciones nuevas"
        )
        servicio = _servicio(compose, "migraciones")

        assert _condicion_de(servicio["depends_on"], "postgres-reconciler") == "service_healthy", (
            "el servicio de migraciones no puede correr antes de que Postgres "
            "este sano: `psql` fallaria con connection refused"
        )
        assert servicio["restart"] == "no", (
            "es un trabajo de una pasada: con `restart: always` una migracion "
            "fallida se reintentaria en bucle"
        )

        montajes = [str(m) for m in servicio["volumes"]]
        assert any("./db:" in m for m in montajes), (
            "tiene que montar `./db` para ver los .sql de db/migrations/"
        )
        assert any("migrar_auto.sh" in m for m in montajes), (
            "tiene que montar el script que aplica, no copiarlo dentro de la imagen"
        )
        entrypoint = " ".join(str(p) for p in servicio["entrypoint"])
        assert "migrar_auto.sh" in entrypoint, (
            "el entrypoint tiene que correr `migrar_auto.sh`: si apunta a otro "
            "script, el que se comprueba aqui no es el que se ejecuta"
        )

    def test_la_api_no_arranca_hasta_que_las_migraciones_terminan(self, compose):
        depends = _servicio(compose, "expense-api")["depends_on"]

        assert _condicion_de(depends, "migraciones") == "service_completed_successfully", (
            "la API tiene que esperar a `migraciones` con "
            "`service_completed_successfully`. El healthcheck de Postgres solo "
            "pregunta por `tickets`, que existe desde `init.sql`, NO por las "
            "columnas nuevas: sin esta espera el arranque daba 500 en "
            "/scan/files aunque Compose dijera que todo estaba healthy"
        )

    def test_el_servicio_toma_el_password_y_el_usuario_de_la_misma_configuracion(self, compose):
        servicio = _servicio(compose, "migraciones")
        environment = " ".join(str(v) for v in servicio.get("environment", []))

        assert any(".env" in str(f) for f in servicio.get("env_file", [])), (
            "el servicio tiene que leer `.env`: ahi estan POSTGRES_USER y "
            "POSTGRES_DB. sin el, el script caeria a sus defaults"
        )
        assert "PGPASSWORD" in environment, (
            "el `psql` del script necesita la contraseña: sin PGPASSWORD intenta "
            "pedirla por stdin y el contenedor se queda esperando"
        )

    def test_el_wrapper_del_host_usa_el_usuario_que_existe(self):
        script = (RAIZ / "scripts/migrar.sh").read_text(encoding="utf-8")

        assert 'USUARIO="postgres"' not in script, (
            "`postgres` no es un rol de esta base: el entrypoint crea solo "
            "`POSTGRES_USER` (admin). MEDIDO: `FATAL: role \"postgres\" does "
            'not exist` -> "La base no respondio" aunque estuviera sana'
        )
        assert "POSTGRES_USER" in script and "POSTGRES_DB" in script, (
            "el wrapper tiene que leer usuario y base de `.env`, igual que "
            "Compose; un segundo sitio con el rol escrito a mano puede "
            "dejar de coincidir"
        )

    def test_no_hay_dos_bucles_que_apliquen_las_migraciones(self):
        wrapper = (RAIZ / "scripts/migrar.sh").read_text(encoding="utf-8")
        auto = (RAIZ / "scripts/migrar_auto.sh").read_text(encoding="utf-8")

        assert 'docker compose "${COMPOSE_ENV[@]}" run --rm --no-deps migraciones' in wrapper, (
            "`make migrar` tiene que delegar al MISMO servicio que corre en el "
            "arranque, no aplicar por su cuenta. El array de --env-file va antes: "
            "sin el, Compose no interpola y el wrapper muere en un shell limpio"
        )
        assert "-f \"$CONTENEDOR_DB" not in wrapper, (
            "el bucle viejo con `docker compose exec ... psql -f` sigue en el "
            "wrapper: dos implementaciones divergen y la del arranque es la "
            "que nadie mira"
        )
        assert "CREATE TABLE IF NOT EXISTS schema_migrations" in auto, (
            "el script del contenedor tiene que crear el registro si la base "
            "es anterior a el; las migraciones son idempotentes"
        )

    def test_el_registro_se_escribe_despues_de_aplicar(self):
        auto = (RAIZ / "scripts/migrar_auto.sh").read_text(encoding="utf-8")

        aplica = auto.find('-f "$archivo"')
        registra = auto.find("INSERT INTO schema_migrations")

        assert aplica >= 0 and registra >= 0, (
            "el script tiene que aplicar el archivo y registrar que lo aplico"
        )
        assert aplica < registra, (
            "registrar antes del `psql -f` marcaría como al dia una base a la "
            "que no le corrio el DDL: el proximo arranque saltaria la migracion"
        )
        assert "ON_ERROR_STOP=1" in auto, (
            "sin `ON_ERROR_STOP=1`, un error a mitad del archivo no detiene "
            "`psql` y la migracion se daria por aplicada"
        )

    def test_la_validacion_acepta_el_nombre_real_y_sigue_cazando_inyeccion(self):
        auto = (RAIZ / "scripts/migrar_auto.sh").read_text(encoding="utf-8")
        real = next((RAIZ / "db/migrations").glob("0019_extraccion*.sql"), None)

        assert real is not None, "no existe la migracion 0019 de extraccion"
        assert "í" in real.name, (
            "el nombre real lleva acento: si un dia se renombra sin el, este "
            "test tiene que cambiar con el archivo"
        )
        assert "[!A-Za-z0-9_.-]" not in auto, (
            "la validacion ASCII rechazo `0019_extraccion_multipaís...` en la "
            "primera corrida: el servicio salio con 1, la API no arranco y "
            "`make up` fallo. El nombre se pega al SQL, pero los acentos son "
            "legitimos; lo que se prohibe son las comillas y el punto y coma"
        )
        assert "*\"'\"*" in auto and "*';'*" in auto, (
            "se pueden aceptar acentos, pero NO se puede soltar la defensa "
            "contra una comilla simple o un punto y coma en el nombre: se pega "
            "al INSERT y seria DDL arbitrario"
        )

    def test_el_seed_registra_cada_archivo_con_su_nombre_exacto(self):
        """La base nueva nace con el libro de migraciones bien llevado.

        MEDIDO el 2026-11-26: el seed tenia `0020_auditoria_segura` sin el
        sufijo `.sql` y no tenia `0027_costo_del_kardex.sql`. Las dos migraciones
        son idempotentes, asi que el arranque no reventaba: el servicio volvia
        a aplicar 0020 y 0027 y anadia una fila que no corresponde a ningun
        archivo. No era un fallo visible, era un registro que mentia sobre que
        se aplico. Aqui se compara el nombre EXACTO, no el numero: recortar el
        sufijo es lo que hacia que el bucle no encontrara la fila.
        """
        ini = (RAIZ / "db/init.sql").read_text(encoding="utf-8")
        inicio = ini.index("INSERT INTO schema_migrations (nombre) VALUES")
        fin = ini.index("ON CONFLICT (nombre) DO NOTHING;", inicio)
        registradas = set(re.findall(r"\('([^']+)'\)", ini[inicio:fin]))
        archivos = {p.name for p in (RAIZ / "db/migrations").glob("*.sql")}

        assert not (archivos - registradas), (
            "migraciones sin registrar en el init: "
            + ", ".join(sorted(archivos - registradas))
            + ". El servicio las aplicara de nuevo en cada base nueva"
        )
        assert not (registradas - archivos), (
            "nombres registrados que no son archivos: "
            + ", ".join(sorted(registradas - archivos))
            + ". El libro dice que se aplico algo que no existe"
        )
