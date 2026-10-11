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
