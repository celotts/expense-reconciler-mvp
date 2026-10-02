"""El arranque se niega a levantar con una clave debil o inventada.

Son reglas de `app/core/config.py`, y estan en un sitio raro: se ejecutan al
construir el objeto de settings, no cuando se usa. Por eso son especialmente
faciles de romper sin que nada se entere: nadie las ve, nadie las toca y nadie
las nota en la revision hasta que un despliegue arranca firmando tokens con una
clave que cambia en cada reinicio.

Tres reglas, con razones distintas:

- En produccion sin `SECRET_KEY` el arranque falla, en vez de inventar una.
- Una clave de menos de 32 caracteres se rechaza siempre.
- Fuera de produccion se puede arrancar sin definirla, pero avisando.

La ultima es la que mas sorprende cuando se topa con ella, y por eso lleva su
propio test del aviso: sin el, el "funciona en local sin configurar nada" se
paga con tokens que mueren en cada reinicio, y eso se descubre cuando la
pestana queda en blanco a media sesion.
"""

import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings

# Una clave que si pasa el minimum de longitud.
CLAVE_OK = "k" * 48
# Una clave que no: 31 caracteres, uno menos.
CLAVE_CORTA = "k" * 31

# Claves distintas para distinguir QUE ARCHIVO gano. Todas de 32 o mas, que es
# el minimo que `config.py` exige.
CLAVE_DE_DEV = "clave-de-dev-00000000000000000000"
CLAVE_DE_LOCAL = "clave-de-local-000000000000000000"
CLAVE_DEL_ENTORNO = "clave-del-entorno-0000000000000000"
assert min(len(c) for c in (CLAVE_DE_DEV, CLAVE_DE_LOCAL, CLAVE_DEL_ENTORNO)) >= 32

BASE = {"DATABASE_URL": "postgresql+asyncpg://x@localhost/x"}


def _settings(**extra):
    """Construye settings ignorando el entorno y el `.env.dev`.

    Sin esto, quien tenga `SECRET_KEY` en su `.env.dev` no puede probar la rama
    de "no esta definida": el test pasaria por el motivo equivocado, y solo en
    la maquina de quien si la tiene.

    `_env_file=None` apaga el `.env.dev`. Los valores que llegan como argumento
    tienen prioridad sobre las variables de entorno en `pydantic-settings`, asi
    que pasar los dos de forma explicita es lo que hace que el resultado no
    dependa de quien corra el test.

    Para "no definida" se pasa la cadena vacia, no `None`: el validator decide
    con un chequeo de falsedad, y `None` no es un `str` valido para el campo, asi
    que daria un error de validacion por la forma y no por la regla que se
    quiere probar.
    """

    base = {
        "DATABASE_URL": BASE["DATABASE_URL"],
        "SECRET_KEY": "",
        "ENVIRONMENT": "development",
    }
    base.update(extra)
    return Settings(_env_file=None, **base)


class TestSinClaveDefinida:
    def test_en_produccion_no_arranca(self):
        """El fallo mas caro de esta lista, y el mas silencioso si no se
        prueba.

        Una clave autogenerada firma tokens que valen hasta que reinicia el
        contenedor. Con una sola instancia, molesto. Con dos, cada una invalida
        los tokens de la otra y los usuarios ven la sesion caerse sin motivo, en
        produccion, sin error en ninguna parte.
        """

        with pytest.raises(ValidationError) as error:
            _settings(ENVIRONMENT="production")

        assert "SECRET_KEY" in str(error.value)

    def test_en_produccion_tampoco_con_clave_corta(self):
        with pytest.raises(ValidationError) as error:
            _settings(ENVIRONMENT="production", SECRET_KEY=CLAVE_CORTA)

        assert "SECRET_KEY" in str(error.value)

    def test_desarrollo_arranca_pero_avisa(self, caplog):
        """En local selevanta sin configurar nada, que es lo que hace util el
        proyecto. Pero avisa, y el aviso es la unica pista de que al reiniciar
        se cae la sesion de todos.
        """

        with caplog.at_level(logging.WARNING):
            resultado = _settings(ENVIRONMENT="development")

        assert resultado.SECRET_KEY, "no se genero ninguna clave"
        assert len(resultado.SECRET_KEY) >= 32
        assert any("SECRET_KEY" in r.message for r in caplog.records), (
            "arranco sin avisar de que la clave es de un solo uso"
        )

    def test_cada_proceso_genera_una_clave_distinta(self):
        """La clave autogenerada no esta en el codigo ni se reparte. Dos
        procesos de desarrollo tienen claves distintas, y por eso los tokens de
        uno no valen en el otro: es el comportamiento correcto, porque la
        alternativa seria un secreto por omision escrito en el repositorio."""

        assert _settings(ENVIRONMENT="development").SECRET_KEY != _settings(
            ENVIRONMENT="development"
        ).SECRET_KEY

    def test_desarrollo_tambien_avisa_sin_environ(self, caplog):
        """Sin `ENVIRONMENT` tampoco se avisa al mundo de que esto no es
        produccion. El default es `development` y tiene que avisar igual."""

        from app.core.config import Settings as S

        with caplog.at_level(logging.WARNING):
            resultado = S(
                _env_file=None,
                DATABASE_URL=BASE["DATABASE_URL"],
                SECRET_KEY="",
            )

        assert resultado.ENVIRONMENT == "development"
        assert any("SECRET_KEY" in r.message for r in caplog.records)


class TestLaLongitudDeLaClave:
    @pytest.mark.parametrize("corta", ["c", "corta", "k" * 16, "k" * 31])
    def test_corta_se_rechaza_en_cualquier_entorno(self, corta):
        """En produccion Y en desarrollo. Una clave corta es adivinable, y en
        desarrollo tambien: el `.env.dev` esta en el repositorio de todo el
        equipo, y un despliegue de prueba en una maquina de alguien queda
        expuesta igual que uno de produccion.

        La cadena vacia NO esta en esta lista, y a proposito: vacia es "no
        definida", que es la otra rama del validator. En desarrollo genera una
        clave y avisa; en produccion aborta. Tratarla como "corta" haria que el
        test midiera la rama equivocada.
        """

        for entorno in ("development", "production"):
            with pytest.raises(ValidationError) as error:
                _settings(ENVIRONMENT=entorno, SECRET_KEY=corta)
            assert "32" in str(error.value), entorno

    def test_vacia_es_no_definida_y_no_clave_corta(self):
        """La distincion que separa las dos ramas, comprobada por los dos
        lados: la misma cadena vacia que aborta en produccion genera una clave
        en desarrollo. Si las dos rutas se mezclaran, alguien con
        `SECRET_KEY=` a medio escribir en su `.env` no tendria forma de saber si
        lo que arranco es el suyo o uno recien generado."""

        with pytest.raises(ValidationError):
            _settings(ENVIRONMENT="production", SECRET_KEY="")

        resultado = _settings(ENVIRONMENT="development", SECRET_KEY="")
        assert resultado.SECRET_KEY

    def test_justo_en_el_minimo_pasa(self):
        """El limite es 32 y el valor de 32 tiene que entrar. Un
        `> 32` en vez de `< 32` rechazaria el valor valido, que es el fallo
        dificil de ver: nadie complains hasta que un despliegue no arranca."""

        assert _settings(SECRET_KEY="k" * 32).SECRET_KEY == "k" * 32

    def test_una_clave_larga_no_se_toca(self):
        """Si la clave viene del entorno, se usa tal cual. Recortarla o
        completarla seria peor: cualquier transformacion de la clave rompe los
        tokens firmados con la version anterior de la transformacion."""

        assert _settings(SECRET_KEY=CLAVE_OK).SECRET_KEY == CLAVE_OK

    def test_una_clave_con_espacios_no_se_recorta(self):
        """`str.strip()` en el validator seria una trampa: la clave de un
        `.env` con espacio alrededor es una clave distinta, y con el recorte
        pasaria a firmar con una clave que el despliegue anterior nunca uso."""

        con_espacios = f"  {CLAVE_OK}  "
        assert _settings(SECRET_KEY=con_espacios).SECRET_KEY == con_espacios


class TestElCorsNoPuedeSerComodin:
    """`*` con credenciales es un fallo de seguridad, no una configuracion floja.

    El test de arranque, y despues una llamada HTTP real: la razon de que el
    primero no basta es que un validator se puede mutar a algo que sigue
    arrancando. Lo que importa es lo que responde el servidor.
    """

    def test_un_origen_comodin_impide_arrancar(self):
        with pytest.raises(ValidationError) as error:
            _settings(CORS_ORIGINS=["*"])

        texto = str(error.value)
        assert "CORS_ORIGINS" in texto
        assert "allow_credentials" in texto

    def test_un_comodin_junto_a_otros_tambien_impide_arrancar(self):
        """`*` mezclado con origenes concretos sigue siendo un comodin.

        Es el caso que se cuela: alguien anade su localhost nuevo y pega el `*`
        "por si acaso" junto. Con la lista mixta, `is_allowed_origin` de Starlette
        sigue devolviendo True para cualquiera.
        """
        with pytest.raises(ValidationError):
            _settings(CORS_ORIGINS=["http://localhost:3000", "*"])

    def test_una_lista_de_origenes_concretos_arranca(self):
        """El caso normal: los dos localhost de desarrollo."""
        resultado = _settings(
            CORS_ORIGINS=["http://localhost:3000", "http://localhost:5173"]
        )
        assert resultado.CORS_ORIGINS == [
            "http://localhost:3000",
            "http://localhost:5173",
        ]


class TestElOrdenDeLosArchivosDeEntorno:
    """`.env.dev` y luego `.env.local`, y el ultimo manda.

    No es cosmetico. `docker-compose.yml` carga los dos en ese orden, asi que si
    `config.py` los leiera al reves, correr `uvicorn` en la maquina firmaria con
    la clave de `.env.dev` mientras el contenedor firma con la de `.env.local`:
    dos juegos de tokens y un "mi sesion se cae" que solo se reproduce donde se
    reproduce. Con `--reload`, ademas, cada cambio en un `.py` genera una clave
    nueva y echa a todos dentro.
    """

    def test_el_archivo_correcto_es_el_que_gana(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.dev").write_text(f"SECRET_KEY={CLAVE_DE_DEV}\n")
        (tmp_path / ".env.local").write_text(f"SECRET_KEY={CLAVE_DE_LOCAL}\n")

        resultado = Settings(
            _env_file=(".env.dev", ".env.local"),
            DATABASE_URL=BASE["DATABASE_URL"],
        )

        assert resultado.SECRET_KEY == CLAVE_DE_LOCAL

    def test_sin_local_se_lee_dev(self, tmp_path, monkeypatch):
        """Clone nuevo: no hay `.env.local` y la app tiene que levantar igual."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.dev").write_text(f"SECRET_KEY={CLAVE_DE_DEV}\n")

        resultado = Settings(
            _env_file=(".env.dev", ".env.local"),
            DATABASE_URL=BASE["DATABASE_URL"],
        )

        assert resultado.SECRET_KEY == CLAVE_DE_DEV

    def test_la_variable_de_entorno_gana_sobre_los_dos(self, tmp_path, monkeypatch):
        """Lo que usa Compose: pone `DATABASE_URL` en `environment:`, y eso tiene
        que pisar a los dos archivos. Si no, la ruta de la BD del host ganaria
        dentro del contenedor, donde no existe."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.dev").write_text(f"SECRET_KEY={CLAVE_DE_DEV}\n")
        (tmp_path / ".env.local").write_text(f"SECRET_KEY={CLAVE_DE_LOCAL}\n")
        monkeypatch.setenv("SECRET_KEY", CLAVE_DEL_ENTORNO)

        resultado = Settings(
            _env_file=(".env.dev", ".env.local"),
            DATABASE_URL=BASE["DATABASE_URL"],
        )

        assert resultado.SECRET_KEY == CLAVE_DEL_ENTORNO


class TestLaPlantillaDeLosEnv:
    """Los archivos del repo tienen que seguir siendo secretos-free y claros.

    Es la unica defensa contra volver a poner una clave real en `.env.dev`. El
    archivo esta en `.gitignore`, asi que `git status` no lo delata ni cuando se
    rompe la regla; y `git diff` tampoco lo muestra.
    """

    def _leer(self, nombre: str) -> str:
        from pathlib import Path

        raiz = Path(__file__).resolve().parents[2]
        return (raiz / nombre).read_text(encoding="utf-8")

    def test_env_dev_no_tiene_contrasena_de_la_base(self):
        """La `DATABASE_URL` de `.env.dev` no lleva contrasena.

        Dentro de Docker la sobrescribe `docker-compose.yml`, asi que la que este
        aqui no se usa: es un segundo lugar donde vive el mismo secreto, con
        permisos de lectura para el grupo. La plantilla `.env.example` ya lo
        explica; este test lo hace cumplir.
        """
        import re

        for linea in self._leer(".env.dev").splitlines():
            if linea.startswith("DATABASE_URL="):
                usuario_y_host, _, _ = linea.partition("@")
                # `usuario:password@host` -> la parte antes de la arroba no lleva
                # dos puntos, que es donde iria la contrasena.
                assert usuario_y_host.count(":") <= 1, linea

    def test_env_local_no_declara_ninguna_de_la_base(self):
        """`.env.local` es el archivo del secreto de firma y nada mas.

        Si aparece `POSTGRES_PASSWORD` ahi, el secreto se multiplica otra vez, en
        un archivo cuya copia local nadie revisa.
        """
        texto = self._leer(".env.local")
        assert "POSTGRES_PASSWORD" not in texto
        assert "DATABASE_URL" not in texto

    def test_la_plantilla_explica_cuantos_archivos_hay(self):
        """La plantilla dice para que es cada archivo.

        Cuatro archivos `.env` sin explicar cual es cual es como acaba la clave
        en dos sitios y la contrasena en tres: no por descuido, sino porque nadie
        sabe quien gana. Este test no mira el texto completo, solo que nombrelos.
        """
        texto = self._leer(".env.example")
        for nombre in (".env.dev", ".env.local"):
            assert nombre in texto, f"{nombre} no se menciona en .env.example"

    def test_la_plantilla_avisa_de_lo_del_cors(self):
        """El comentario de `CORS_ORIGINS` tiene que estar junto a la variable.

        Es la unica documentacion que vera alguien que copie el archivo. Y la
        trampa es real: `*` es lo que se escribe cuando se quiere "que no haya
        problemas de CORS".
        """
        texto = self._leer(".env.example")
        linea_cors = next(
            l for l in texto.splitlines() if l.startswith("CORS_ORIGINS=")
        )
        contexto = texto[max(0, texto.index(linea_cors) - 700) : texto.index(linea_cors)]
        assert "*" in contexto, "el aviso del origen comodin no esta junto a la variable"
