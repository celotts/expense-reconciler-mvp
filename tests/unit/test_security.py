"""Las primitivas de la sesion, probadas sin HTTP.

La razon de separarlas de `test_auth_api.py`: hay cosas que por HTTP no se
pueden comprobar. Un hash con el parametro N a la mitad, o un token con `exp`
en el pasado exacto, se prueban aqui, en el mismo punto donde se construyen.

Igual que en el resto del proyecto, un test que pasa no prueba nada: lo que
dice algo es `scripts/verify_auth_mutations.py`.
"""

import json
import time
from base64 import urlsafe_b64decode
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.security import (
    ErrorDeToken,
    TokenCaducado,
    _VERSION_HASH,
    _b64e,
    crear_token,
    hash_de_trampa,
    hashear_contrasena,
    leer_token,
    verificar_contrasena,
)


def _firmar(claims: dict, cabecera: dict | None = None) -> str:
    """Un token firmado DE VERDAD con el contenido que se le pida.

    Hace falta porque un token con la firma rota no prueba nada sobre las
    comprobaciones que vienen despues de la firma: `leer_token` sale en la
    primera linea y nunca llega a mirar el algoritmo, el tipo, el `exp` ni el
    `sub`. Para probar esas reglas hay que firmar con la clave del servidor y
    que lo que este mal sea lo que se quiere que este mal.

    Ahi esta casi todo el valor de estos tests: un atacante que consigue la
    clave puede emitir tokens firmados, y todas las reglas que van despues de
    la firma son las que lo detienen.
    """

    import hashlib
    import hmac

    from app.core import security

    partes = [
        _b64e(json.dumps(cabecera or {"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()),
        _b64e(json.dumps(claims, separators=(",", ":")).encode()),
    ]
    firma = hmac.new(
        security.settings.SECRET_KEY.encode("utf-8"),
        ".".join(partes).encode("ascii"),
        hashlib.sha256,
    ).digest()
    return ".".join([*partes, _b64e(firma)])


def _claims(**extra) -> dict:
    base = {
        "sub": str(uuid4()),
        "email": "ana@empresa.mx",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        "typ": "access",
    }
    base.update(extra)
    return base


class TestElHashDeContrasena:
    def test_una_contrasena_da_un_hash_distinto_cada_vez(self):
        """Dos personas con la misma contrasena tienen hashes distintos, porque
        la sal es por usuario. Sin sal, el archivo de hashes es una tabla
        precalculada: un ataque de diccionario se resuelve de una."""

        primero = hashear_contrasena("la-misma-contrasena")
        segundo = hashear_contrasena("la-misma-contrasena")

        assert primero != segundo

    def test_el_hash_no_contiene_la_contrasena(self):
        """Ni cifrada. Por eso se llama hash y no cifrado: no hay forma de
        recuperar la contrasena desde la base, ni con acceso a ella."""

        assert "la-misma-contrasena" not in hashear_contrasena("la-misma-contrasena")

    def test_el_formato_lleva_sus_propios_parametros(self):
        """El hash dice con que N, r y p se calculo. Si manana suben el coste,
        los hashes viejos se siguen validando con los parametros con los que
        se hicieron y se pueden migrar uno por uno. Si el formato no los
        guardara, subir el coste invalidaria a todos los usuarios de golpe."""

        partes = hashear_contrasena("x").split("$")

        assert partes[0] == _VERSION_HASH
        assert partes[1] == str(2**14)
        assert partes[2] == "8"
        assert partes[3] == "1"

    def test_la_contrasena_correcta_pasa(self):
        assert verificar_contrasena("secreta", hashear_contrasena("secreta")) is True

    @pytest.mark.parametrize(
        "mal",
        ["otra", "secreta ", "Secreta", "", "s" * 257],
    )
    def test_cualquier_otra_cosa_falla(self, mal):
        assert verificar_contrasena(mal, hashear_contrasena("secreta")) is False

    @pytest.mark.parametrize(
        "basura",
        [
            "",
            "no-es-un-hash",
            "scrypt-v1$16384$8$1$solomitsolomitsolomitsolomitsolomitsolomitsolomiti$AAAA",
            "scrypt-v2$16384$8$1$c2FsdA$aGFzaA",
            "$16384$8$1$c2FsdA$aGFzaA",
            "scrypt-v1$no-es-un-numero$8$1$c2FsdA$aGFzaA",
        ],
    )
    def test_un_hash_basura_no_revienta(self, basura):
        """Un valor corrupto en la base responde "no verifica" y no una
        excepcion. Si aqui tronara, un solo registro danado tumba el login
        entero con un 500 en vez de rejecting ese intento."""

        assert verificar_contrasena("secreta", basura) is False

    def test_el_hash_de_trampa_no_verifica_nada(self):
        """Se usa cuando el correo no existe, para gastar el mismo tiempo. Si
        el hash de trampa fuera verificable contra alguna contrasena, seria
        una puerta trasera."""

        trampa = hash_de_trampa()

        assert verificar_contrasena("secreta", trampa) is False
        assert verificar_contrasena("", trampa) is False

    def test_el_hash_de_trampa_se_cachea(self):
        """Calcularlo cuesta lo mismo que una verificacion real. Sin cache,
        cada intento con correo inexistente multiplica por dos el coste de
        scrypt, y eso es un amplificador de denegacion de servicio."""

        assert hash_de_trampa() is hash_de_trampa()

    def test_un_hash_de_otra_version_no_verifica(self):
        """El prefijo del formato es una promesa sobre con que parametros se
        calculo el hash.

        Aqui la promesa es falsa: el cuerpo dice `scrypt-v2` pero trae los
        parametros y la sal correctos, asi que un verificador que ignorara el
        prefijo lo daria por bueno. Cuando se suban N o r, esto es lo que
        distingue "un hash de antes, hay que migrarlo" de "una contrasena que
        alguien eligio para que no se note".
        """

        bueno = hashear_contrasena("secreta")
        _version, n, r, p, sal, resumen = bueno.split("$")

        mintiendo = f"scrypt-v2${n}${r}${p}${sal}${resumen}"
        otro_genero = f"scrypt-v9${n}${r}${p}${sal}${resumen}"
        sin_version = f"${n}${r}${p}${sal}${resumen}"

        for hash_mentiroso in (mintiendo, otro_genero, sin_version):
            assert verificar_contrasena("secreta", hash_mentiroso) is False, (
                f"un hash con la version {hash_mentiroso.split('$')[0]!r} paso"
            )


class TestElToken:
    def test_los_claims_dicen_lo_que_deben(self):
        usuario_id = str(uuid4())

        token, segundos = crear_token(usuario_id, "ana@empresa.mx", expira_en_minutos=10)

        claims = leer_token(token)
        assert claims["sub"] == usuario_id
        assert claims["email"] == "ana@empresa.mx"
        assert claims["typ"] == "access"
        assert segundos == pytest.approx(600, abs=2)
        assert claims["exp"] - claims["iat"] == 600

    def test_la_vida_devuelta_son_segundos_no_fecha(self):
        """El cliente suma segundos en vez de interpretar una fecha absoluta.
        La fecha absoluta es donde se cuelan los errores de una hora de
        diferencia de zona horaria."""

        _, segundos = crear_token("x", "y@z.mx", expira_en_minutos=1)

        assert isinstance(segundos, int)
        assert 55 <= segundos <= 60

    def test_el_caducado_se_distingue_del_mal_formado(self):
        """El cliente los trata distinto: con uno va al login, con el otro
        reintentar es inútil. Si los dos fueran el mismo error, el frontend
        tendria que adivinar."""

        caducado, _ = crear_token("x", "y@z.mx", expira_en_minutos=0)
        time.sleep(1.1)

        with pytest.raises(TokenCaducado):
            leer_token(caducado)

        with pytest.raises(ErrorDeToken):
            leer_token("esto-no-es-un-token")

        # Y el primero sigue siendo un caso del segundo, para el codigo que
        # solo quiere distinguir "serve" de "no sirve".
        assert issubclass(TokenCaducado, ErrorDeToken)

    def test_faltar_cualquier_parte_es_un_error_de_token(self):
        for token in [
            "",
            "a",
            "a.b",
            "a.b.c.d",
            "....",
        ]:
            with pytest.raises(ErrorDeToken):
                leer_token(token)

    def test_el_token_de_otro_secreto_no_sirve(self):
        """La clave se lee al verificar, no se captura al importar. Un token
        firmado con la clave de otro despliegue tiene que dejar de servir."""

        import app.core.security as security

        token, _ = crear_token("x", "y@z.mx")
        original = security.settings.SECRET_KEY
        try:
            security.settings.SECRET_KEY = "otra-clave-completamente-distinta-000000"
            with pytest.raises(ErrorDeToken):
                leer_token(token)
        finally:
            security.settings.SECRET_KEY = original

    def test_cambiar_un_claim_invalida_la_firma(self):
        """El `exp` va DENTRO de la parte firmada. Reescribirlo para estirar
        la vida rompe la firma, porque la firma cubre el cuerpo entero."""

        token, _ = crear_token("x", "y@z.mx", expira_en_minutos=10)
        cabecera, cuerpo, firma = token.split(".")

        import base64
        import json

        claims = json.loads(urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)))
        claims["exp"] = claims["exp"] + 999999
        nuevo_cuerpo = (
            base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        )

        with pytest.raises(ErrorDeToken):
            leer_token(f"{cabecera}.{nuevo_cuerpo}.{firma}")

    def test_el_tipo_de_token_se_comprueba(self):
        """Hoy solo hay access tokens. Cuando haya refresh, un access token no
        puede pasar por el endpoint de refresh: sin este claim, un token de
        vida corta serviria como si fuera de vida larga.

        Firmado de verdad, no con la firma rota: si la firma esta rota,
        `leer_token` sale antes de llegar aqui y el test pasa por el motivo
        equivocado. Este es el caso que de verdad importa, porque presupone
        que el atacante ya tiene la clave.
        """

        with pytest.raises(ErrorDeToken, match="acceso"):
            leer_token(_firmar(_claims(typ="refresh")))

        # Y un token de acceso bien formado sigue funcionando: la regla no
        # puede ser "rechazar siempre".
        claims = leer_token(_firmar(_claims()))
        assert claims["typ"] == "access"

    def test_alg_none_firmado_no_abre(self):
        """El ataque clasico de JWT, con la firma puesta.

        `alg: none` significa "no verifiques la firma". Aqui la hay, y aun
        asi tiene que rechazarse, porque el algoritmo lo declara una parte del
        token que controla el atacante. Si la comprobacion del `alg` desapareciera,
        este token entraria.
        """

        with pytest.raises(ErrorDeToken, match="algoritmo"):
            leer_token(_firmar(_claims(), cabecera={"alg": "none", "typ": "JWT"}))

    def test_otro_algoritmo_firmado_no_abre(self):
        """`HS512` tampoco. La lista es cerrada a proposito: aceptar el
        algoritmo que el token pida es Delegar la decision a quien lo firma."""

        with pytest.raises(ErrorDeToken, match="algoritmo"):
            leer_token(_firmar(_claims(), cabecera={"alg": "HS512", "typ": "JWT"}))

    def test_una_cabecera_que_no_es_objeto_no_abre(self):
        """La cabecera es la parte que menos se ha revisado en el mundo. Si no
        es un diccionario, no hay `alg` que leer, y eso no puede pasar por
        "no habia algoritmo"."""

        cabecera_rota = _b64e(b'"esto-es-una-cadena-y-no-un-objeto"')
        cuerpo = _b64e(json.dumps(_claims(), separators=(",", ":")).encode())
        import hashlib
        import hmac

        from app.core import security

        firma = _b64e(
            hmac.new(
                security.settings.SECRET_KEY.encode("utf-8"),
                f"{cabecera_rota}.{cuerpo}".encode("ascii"),
                hashlib.sha256,
            ).digest()
        )

        with pytest.raises(ErrorDeToken):
            leer_token(f"{cabecera_rota}.{cuerpo}.{firma}")

    def test_un_token_sin_exp_no_abre(self):
        """`exp` ausente, o de texto, o nulo.

        El primero es el token eterno: sin esta comprobacion, "firmalo una vez
        y usalo para siempre" funciona. El segundo es un `"99999999999"` que
        llega como texto, o el `null` de un JSON: sin el `isinstance`, la
        comparacion de Python da `TypeError` y sale un 500 en vez de un 401.

        Un `exp` enorme SI se acepta, a proposito: quien puede firmar eso ya
        tiene la clave del servidor, y un tope inventado aqui seria una regla
        mas que mantener y un segundo sitio donde equivocarse.
        """

        for claims in (
            {k: v for k, v in _claims().items() if k != "exp"},
            _claims(exp="99999999999"),
            _claims(exp=None),
        ):
            with pytest.raises(ErrorDeToken):
                leer_token(_firmar(claims))

    def test_un_token_sin_sub_no_abre(self):
        """Sin `sub` la dependencia no tiene a quien buscar y terminaria
        consultando por None, que en SQL es `WHERE id IS NULL`."""

        for claims in (
            {k: v for k, v in _claims().items() if k != "sub"},
            _claims(sub=""),
        ):
            with pytest.raises(ErrorDeToken):
                leer_token(_firmar(claims))

    def test_un_cuerpo_que_no_es_objeto_no_abre(self):
        """`json.loads('"texto"')` devuelve una cadena, y una cadena tiene
        `.get`? No. Sin este chequeo, un token con el cuerpo mal formado
        revienta con AttributeError en vez de dar 401."""

        import hashlib
        import hmac

        from app.core import security

        cabecera = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        cuerpo = _b64e(b"[1, 2, 3]")
        firma = _b64e(
            hmac.new(
                security.settings.SECRET_KEY.encode("utf-8"),
                f"{cabecera}.{cuerpo}".encode("ascii"),
                hashlib.sha256,
            ).digest()
        )

        with pytest.raises(ErrorDeToken):
            leer_token(f"{cabecera}.{cuerpo}.{firma}")


# =====================================================================
# Lo que no se puede comprobar desde afuera
# =====================================================================
#
# Estas reglas no tienen ninguna prueba posible por comportamiento: depende de
# como se implementa, no de que devuelve. Un test de temporizacion para la
# comparacion en tiempo constante fallaria en la mitad de las ejecuciones
# (la diferencia que se busca es de nanosegundos) y cuando pasara no probaria
# nada.
#
# La unica forma es leer el codigo y exigir que la llamada este ahi. Es un test
# debil y conviene decirlo: no demuestra que la comparacion sea constante, solo
# que no se ha cambiado por un `==`. Es el mismo compromiso que hacen los
# analizadores de codigo estatico al buscar estas cosas, y es preferible a que
# la regla exista solo en un comentario.


class TestLoQueSoloSeCompruebaLeyendoElCodigo:
    def _fuente(self) -> str:
        from app.core import security

        return Path(security.__file__).read_text(encoding="utf-8")

    def test_la_firma_se_compara_con_hmac(self):
        fuente = self._fuente()
        assert "hmac.compare_digest(firma, esperada)" in fuente, (
            "la firma del token se esta comparando de otra manera. Con `==` "
            "sale en cuanto encuentra la primera diferencia, y eso filtra "
            "cuanto del token esta bien."
        )

    def test_la_contrasena_se_compara_con_hmac(self):
        fuente = self._fuente()
        assert "hmac.compare_digest(derivada, esperado)" in fuente, (
            "mismo motivo que la firma: comparar la contrasena con `==` "
            "devuelve cuanto llevas acertado."
        )

    def test_la_contrasena_de_trampa_viene_del_azar(self):
        """El hash de trampa existe para gastar el mismo tiempo que una
        verificacion real. Si detras hubiera una contrasena fija, el hash seria
        el mismo en todos los despliegues y con el tiempo se podria terminar
        usando como contrasena de alguien. Tiene que salir de un generador."""

        fuente = self._fuente()
        assert "secrets.token_urlsafe" in fuente, (
            "hash_de_trampa() no esta tomando su contrasena del generador de "
            "azar: hay una constante escrita ahi."
        )

    def test_sin_donde_no_llegar_despues_de_verificar_la_firma(self):
        """El orden importa y no se puede comprobar por comportamiento: si el
        `alg` se leyera ANTES de la firma, un token sin firma seria el que
        decide que algoritmo se acepta."""

        fuente = self._fuente()
        firma = fuente.index("hmac.compare_digest(firma, esperada)")
        algoritmo = fuente.index('cabecera.get("alg")')
        assert firma < algoritmo, (
            "el algoritmo se esta leyendo antes de verificar la firma"
        )
