"""La sesion: quien entra, quien no, y que pasa cuando el token caduca.

Estos tests usan `async_client_sin_autenticar`, que NO sobrescribe
`get_current_user`. Si lo sobrescribiera, todos estarian probando el override,
que siempre deja pasar, y la proteccion real no se probaria nunca. Ese es el
error clasico de probar la configuracion en vez del codigo.
"""

import time
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.security import crear_token, hashear_contrasena
from app.models.company import CompanyModel
from app.models.user import UserModel


@pytest.fixture(autouse=True)
def _reinicia_el_limite():
    """El limite de intentos vive en memoria del proceso.

    Sin esto, un test que prueba el bloqueo deja el par (correo, IP) frenado
    para todos los tests que corran despues en el mismo proceso, y el fallo
    aparece en un test que no tiene nada que ver, un dia despues y en un orden
    distinto. Es la forma mas dificil de depurar de todo el suite.
    """
    rate_limit.reiniciar()
    yield
    rate_limit.reiniciar()


async def _crea_usuario(db_session: AsyncSession, **cambios) -> UserModel:
    datos = {
        "email": f"ana{uuid4().hex[:8]}@empresa.mx",
        "nombre": "Ana Perez",
        "password_hash": hashear_contrasena("contrasena-larga"),
        **cambios,
    }
    usuario = UserModel(**datos)
    db_session.add(usuario)
    await db_session.commit()
    await db_session.refresh(usuario)
    return usuario


# =====================================================================
# Sin token
# =====================================================================


class TestSinToken:
    async def test_no_se_entra_sin_token(self, async_client_sin_autenticar, test_company):
        respuesta = await async_client_sin_autenticar.get("/api/v1/companies/")

        assert respuesta.status_code == 401
        assert respuesta.headers.get("WWW-Authenticate") == "Bearer"

    async def test_tampoco_a_las_rutas_de_lectura(
        self, async_client_sin_autenticar, test_company
    ):
        """No es solo escribir lo que esta protegido.

        Un endpoint de lectura sin token devuelve todos los gastos de todas
        las empresas. Para una herramienta contable, leer el historico entero
        sin estar dado de alta es practicamente lo mismo que no tener control
        de acceso.
        """
        for ruta in (
            "/api/v1/tickets/",
            "/api/v1/bank-transactions/",
            "/api/v1/reconciliations/",
            "/api/v1/tickets/review-queue",
            "/api/v1/tickets/spot-check",
        ):
            respuesta = await async_client_sin_autenticar.get(ruta)
            assert respuesta.status_code == 401, f"{ruta} respondio {respuesta.status_code}"

    async def test_una_cabecera_autorizacion_inventada_no_alcanza(
        self, async_client_sin_autenticar, test_company
    ):
        """Sin el prefijo "Bearer", FastAPI no lo reconoce como credencial y
        el resultado tiene que ser el mismo 401, no un 500."""

        respuesta = await async_client_sin_autenticar.get(
            "/api/v1/companies/",
            headers={"Authorization": "abc.def.ghi"},
        )

        assert respuesta.status_code == 401

    async def test_el_health_sigue_abierto(self, async_client_sin_autenticar):
        """El chequeo de vida no pide token.

        Un /health protegido necesita credenciales, y un monitor que no las
        tiene reporta caido un servicio que esta arriba. Y /health no devuelve
        nada del dominio: ni tickets, ni montos, ni cuentas.
        """
        respuesta = await async_client_sin_autenticar.get("/health")

        assert respuesta.status_code == 200
        assert respuesta.json()["status"] == "healthy"
        assert "users" not in respuesta.text
        assert "password" not in respuesta.text.lower()


# =====================================================================
# El login
# =====================================================================


class TestElLogin:
    async def test_entra_con_credenciales_correctas(
        self, async_client_sin_autenticar, db_session
    ):
        usuario = await _crea_usuario(db_session)

        respuesta = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "contrasena-larga"},
        )

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["token_type"] == "bearer"
        assert cuerpo["access_token"]
        assert cuerpo["expires_in"] > 0
        assert cuerpo["user"]["email"] == usuario.email

    async def test_el_token_sirve_para_entrar_despues(
        self, async_client_sin_autenticar, db_session, test_company
    ):
        """El cierre del circulo: el token que devuelve el login es el unico
        que abre la puerta. Sin este test, el login podria devolver cualquier
        cadena y todos los demas seguirian en verde porque usan el override."""

        usuario = await _crea_usuario(db_session)
        login = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "contrasena-larga"},
        )
        token = login.json()["access_token"]

        respuesta = await async_client_sin_autenticar.get(
            "/api/v1/companies/", headers={"Authorization": f"Bearer {token}"}
        )

        assert respuesta.status_code == 200

    async def test_contrasena_mala_no_entra(
        self, async_client_sin_autenticar, db_session
    ):
        usuario = await _crea_usuario(db_session)

        respuesta = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "no-es-la-contrasena"},
        )

        assert respuesta.status_code == 401

    async def test_correo_inexistente_dice_lo_mismo(
        self, async_client_sin_autenticar, db_session
    ):
        """Si los dos mensajes fueran distintos, el login seria un buscador de
        correos dados de alta: basta con leer el 401 para saber a quien
        corresponden los correos que existen en esa empresa.

        El caso con el que se compara es "existe pero la contrasena esta mal",
        no "existe y la contrasena esta bien": ese entra, y no sirve como
        referencia de lo que responde un fallo.
        """

        usuario = await _crea_usuario(db_session)

        inexistente = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": "nadie@estaempresa.mx", "password": "contrasena-larga"},
        )
        contrasena_mala = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "otra-cosa-larga"},
        )

        assert inexistente.status_code == contrasena_mala.status_code == 401
        assert inexistente.json()["detail"] == contrasena_mala.json()["detail"]

    async def test_un_correo_inexistente_tambien_cuenta_intento(
        self, async_client_sin_autenticar, db_session
    ):
        """Probar contrasenas contra una cuenta que no existe tiene que gastar
        el mismo esfuerzo que contra una que si.

        Si el limite solo se anotara en el camino de "existe pero la contrasena
        esta mal", un atacante podria probar ilimitadamente con correos
        inventados. Y el limite es lo unico que hay: no hay captcha, ni cuenta
        atras, ni ninguna otra defensa.
        """

        # El mismo correo inventado en los cinco intentos: el limite es por
        # (cuenta, IP), y aqui lo que se prueba es que "no existe" cuente igual
        # que "existe y fallo". Con un correo distinto cada vez cada intento
        # tendria su propia bolsa y no se mediria nada.
        inexistente = f"nadie{uuid4().hex[:6]}@estaempresa.mx"
        cuerpo = {"email": inexistente, "password": "x"}

        for _ in range(rate_limit.MAX_INTENTOS - 1):
            respuesta = await async_client_sin_autenticar.post(
                "/api/v1/auth/login", json=cuerpo
            )
            assert respuesta.status_code == 401, (
                f"un correo que no existe contesto {respuesta.status_code}"
            )

        respuesta = await async_client_sin_autenticar.post(
            "/api/v1/auth/login", json=cuerpo
        )
        assert respuesta.status_code == 429

    async def test_contra_un_correo_inexistente_se_gasta_el_mismo_esfuerzo(
        self, async_client_sin_autenticar, db_session, monkeypatch
    ):
        """La comprobacion de la trampa tiene que ocurrir de verdad, no basta
        con que este escrita en el codigo.

        Se mide contando llamadas: el caso "no existe" tiene que pasar por
        `verificar_contrasena` igual que el caso "existe y falla". Si no, el
        login contesta en microsegundos con un correo que no existe y en 100 ms
        con uno que si, y esa diferencia por si sola ya dice que correos estan
        dados de alta, sin que haga falta leer ningun mensaje.
        """

        usuario = await _crea_usuario(db_session)
        llamadas: list[str] = []

        from app.api import auth

        real = auth.verificar_contrasena

        def _contando(contrasena: str, guardado: str) -> bool:
            llamadas.append(guardado)
            return real(contrasena, guardado)

        monkeypatch.setattr(auth, "verificar_contrasena", _contando)

        await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": "nadie@estaempresa.mx", "password": "lo-que-sea"},
        )
        assert len(llamadas) == 1, "un correo inexistente no verifico nada"
        assert llamadas[0] == auth.hash_de_trampa(), "verifico contra otra cosa"

        llamadas.clear()
        await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "lo-que-sea"},
        )
        assert len(llamadas) == 1
        assert llamadas[0] == usuario.password_hash, "no comparo contra su hash"

    async def test_correo_dado_de_baja_no_entra(
        self, async_client_sin_autenticar, db_session
    ):
        """La baja es `is_active = false`, y el login falla como con una
        contrasena mala. Si contestara "tu cuenta esta dada de baja", bastaria
        con un correo para saber que alguien estuvo aqui."""

        usuario = await _crea_usuario(db_session, is_active=False)

        respuesta = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "contrasena-larga"},
        )

        assert respuesta.status_code == 401
        assert "baja" not in respuesta.json()["detail"].lower()

    async def test_el_correo_no_distingue_mayusculas(
        self, async_client_sin_autenticar, db_session
    ):
        """Con UNIQUE en la base, "Ana@Empresa.mx" y "ana@empresa.mx" serian
        dos cuentas si no se normaliza antes de consultar. Y quien teclea el
        correo con otra caja tiene que poder entrar igual."""

        usuario = await _crea_usuario(db_session, email="ana@empresa.mx")

        respuesta = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": "  ANA@Empresa.MX  ", "password": "contrasena-larga"},
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["user"]["email"] == usuario.email

    async def test_quien_entra_queda_registrado(
        self, async_client_sin_autenticar, db_session
    ):
        """`last_login_at` no decide nada (eso lo hace el token), pero es la
        unica fuente de "quien toco esto y cuando" que sobrevive a un
        reinicio del contenedor. Sin escribirlo, no sirve de nada."""

        usuario = await _crea_usuario(db_session)
        assert usuario.last_login_at is None

        await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "contrasena-larga"},
        )
        await db_session.refresh(usuario)

        assert usuario.last_login_at is not None


# =====================================================================
# El limite de intentos
# =====================================================================


class TestElLimiteDeIntentos:
    async def test_despues_de_cinco_fallos_se_frena(
        self, async_client_sin_autenticar, db_session
    ):
        """Sin esto, scrypt hace lento cada intento pero no imposible: cinco
        por minuto contra una contrasena corta son 1.6 millones de pruebas al
        dia."""

        usuario = await _crea_usuario(db_session)

        for _ in range(rate_limit.MAX_INTENTOS - 1):
            respuesta = await async_client_sin_autenticar.post(
                "/api/v1/auth/login",
                json={"email": usuario.email, "password": "incorrecta"},
            )
            assert respuesta.status_code == 401, "aun deberia contestar credenciales malas"

        respuesta = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "incorrecta"},
        )

        assert respuesta.status_code == 429
        assert int(respuesta.headers["Retry-After"]) > 0

    async def test_estando_frenado_la_contrasena_correcta_tampoco_entra(
        self, async_client_sin_autenticar, db_session
    ):
        """Si la contrasena correcta pasara mientras hay bloqueo, el bloqueo no
        frenaria a nadie: bastaria con acertar por accidente para que un
        atacante con la mitad de las contrasenas aprenda el patron."""

        usuario = await _crea_usuario(db_session)
        for _ in range(rate_limit.MAX_INTENTOS):
            await async_client_sin_autenticar.post(
                "/api/v1/auth/login",
                json={"email": usuario.email, "password": "incorrecta"},
            )

        respuesta = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "contrasena-larga"},
        )

        assert respuesta.status_code == 429

    async def test_el_bloqueo_es_por_cuenta_no_global(
        self, async_client_sin_autenticar, db_session
    ):
        """Si el limite fuera global, alguien podra denegar el acceso a los
        demas con cinco intentos, sin necesitar ninguna contrasena."""

        victima = await _crea_usuario(db_session, email="victima@empresa.mx")
        for _ in range(rate_limit.MAX_INTENTOS):
            await async_client_sin_autenticar.post(
                "/api/v1/auth/login",
                json={"email": victima.email, "password": "incorrecta"},
            )

        otro = await _crea_usuario(db_session, email="otro@empresa.mx")
        respuesta = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": otro.email, "password": "contrasena-larga"},
        )

        assert respuesta.status_code == 200

    async def test_acentar_el_correo_despues_de_tres_fallos_ya_no_es_intento(
        self, async_client_sin_autenticar, db_session
    ):
        """Cinco errores de tecleo y luego acertar no debe dejar a la persona
        esperando a que expire el bloqueo. Quien se equivoco ya demostro que
        sabe la contrasena."""

        usuario = await _crea_usuario(db_session)
        for _ in range(rate_limit.MAX_INTENTOS - 1):
            await async_client_sin_autenticar.post(
                "/api/v1/auth/login",
                json={"email": usuario.email, "password": "incorrecta"},
            )

        respuesta = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "contrasena-larga"},
        )

        assert respuesta.status_code == 200

        # Y despues de acertar, el par vuelve a estar limpio: se pueden volver
        # a permitir MAX_INTENTOS - 1 fallos y el siguiente sigue siendo 401.
        # Si el contador no se hubiera reiniciado, el quinto de esta tanda ya
        # seria el que frena.
        for _ in range(rate_limit.MAX_INTENTOS - 2):
            await async_client_sin_autenticar.post(
                "/api/v1/auth/login",
                json={"email": usuario.email, "password": "incorrecta"},
            )
        respuesta = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "incorrecta"},
        )
        assert respuesta.status_code == 401, "el limite no se reinicio tras acertar"

    async def test_dos_direcciones_no_se_cuotan_entre_si(
        self, async_client_sin_autenticar, db_session
    ):
        """El limite es por (cuenta, IP), no solo por cuenta.

        Sin el componente de IP, alguien podria bloquear el acceso de otra
        persona con cinco intentos, sin saber ninguna contrasena: solo con
        conocer su correo. Y al reves, un atacante con una botnet esquivaria el
        limite por cuenta cambiando de direccion en cada intento, que es
        justo el caso para el que se separa la IP.

        Aqui se comprueba la segunda mitad: una IP nueva arranca con su
        presupuesto limpio.
        """

        usuario = await _crea_usuario(db_session)
        cuerpo = {"email": usuario.email, "password": "incorrecta"}

        for _ in range(rate_limit.MAX_INTENTOS):
            respuesta = await async_client_sin_autenticar.post(
                "/api/v1/auth/login",
                json=cuerpo,
                headers={"X-Forwarded-For": "10.0.0.1"},
            )
            assert respuesta.status_code in (401, 429)

        desde_otra = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json=cuerpo,
            headers={"X-Forwarded-For": "10.0.0.2"},
        )

        assert desde_otra.status_code == 401, (
            "la IP de la cabecera no se esta usando: todos cuentan como uno"
        )

    async def test_la_ip_detras_de_un_proxy_no_es_la_del_proxy(
        self, async_client_sin_autenticar, db_session
    ):
        """El motivo por el que existe la lectura de `X-Forwarded-For`: detras
        de un proxy, `request.client.host` es la del proxy, o sea la misma para
        todo el mundo. Con esa sola, dos personas de oficinas distintas se
        contarian los intentos de la otra y veinte personas legitimas se
        bloquearian entre ellas.

        Con la cabecera se distinguen: seis fallos repartidos en tres IPs por
        cabeza no bloquean a nadie, mientras que seis seguidos desde la misma
        si.
        """

        usuario = await _crea_usuario(db_session)
        cuerpo = {"email": usuario.email, "password": "incorrecta"}

        for ip in ("10.1.0.1", "10.1.0.1", "10.1.0.2", "10.1.0.2", "10.1.0.3", "10.1.0.3"):
            respuesta = await async_client_sin_autenticar.post(
                "/api/v1/auth/login", json=cuerpo, headers={"X-Forwarded-For": ip}
            )
            assert respuesta.status_code == 401, (
                f"con dos intentos por IP todavia no hay bloqueo, y {ip} dio "
                f"{respuesta.status_code}"
            )


# =====================================================================
# El limite, medido con un reloj que si avanza
# =====================================================================
#
# Por HTTP no se puede comprobar que los intentos envejezcen: la ventana son 60
# segundos reales y ningun test va a esperar eso. Se prueba el modulo con un
# reloj falso, que es donde vive la regla.


class TestLaVentanaDeIntentos:
    """`monotonic` se sustituye por un reloj que solo avanza cuando el test
    lo dice. Asi "hace dos minutos que no lo intentas" es una linea en vez de
    una espera."""

    @pytest.fixture
    def reloj(self, monkeypatch):
        from app.core import rate_limit

        ahora = {"t": 1000.0}
        monkeypatch.setattr(rate_limit, "monotonic", lambda: ahora["t"])
        ahora["avanza"] = lambda segundos: ahora.__setitem__("t", ahora["t"] + segundos)
        return ahora

    def test_los_intentos_viejos_se_olvidan(self, reloj):
        """Cinco errores de tecleo separados en el tiempo no son cinco intentos
        contra la misma cuenta: la ventana existe para no castigar a quien
        lleva media hora sin equivocarse.

        Se llega a MAS de MAX_INTENTOS a proposito, y no a MAX_INTENTOS - 1. Con
        cuatro, la bolsa podria estar llena y aun asi no frenarse, asi que el
        test passaria aunque la ventana no olvidara nada. El fallo real solo
        aparece en el quinto.
        """

        for intento in range(rate_limit.MAX_INTENTOS + 2):
            restantes = rate_limit.anotar_intento_fallido("ana@empresa.mx", "10.0.0.1")
            assert restantes == 0, f"el intento {intento + 1} no deberia frenar nada"
            reloj["avanza"](rate_limit.VENTANA_SEGUNDOS + 1)

        assert rate_limit.esta_bloqueado("ana@empresa.mx", "10.0.0.1") == 0

    def test_uno_nuevo_no_hace_bajar_a_los_viejos(self, reloj):
        """La ventana es deslizante, no de todo o nada. Un intento nuevo no
        empuja a los viejos fuera, y dos intentos separados por el justo doble
        de la ventana no cuentan como el mismo racha."""

        for _ in range(rate_limit.MAX_INTENTOS - 1):
            rate_limit.anotar_intento_fallido("ana@empresa.mx", "10.0.0.1")
            reloj["avanza"](1.0)

        reloj["avanza"](rate_limit.VENTANA_SEGUNDOS + 1)

        assert rate_limit.anotar_intento_fallido("ana@empresa.mx", "10.0.0.1") == 0
        assert rate_limit.anotar_intento_fallido("ana@empresa.mx", "10.0.0.1") == 0
        assert rate_limit.esta_bloqueado("ana@empresa.mx", "10.0.0.1") == 0

    def test_los_intentos_recientes_se_cuentan(self, reloj):
        """El contrario: cinco intentos seguidos en la misma ventana si frenan.
        Este es el caso que da nombre al modulo."""

        bloqueados = 0
        for _ in range(rate_limit.MAX_INTENTOS):
            bloqueados = rate_limit.anotar_intento_fallido("ana@empresa.mx", "10.0.0.1")
            reloj["avanza"](1.0)

        assert bloqueados == int(rate_limit.VENTANA_SEGUNDOS)

    def test_un_bloqueo_caduca_solo(self, reloj):
        """Pasada la ventana, el bloqueo se disuelve solo. Si no, con cinco
        equisocos la persona se quedaria fuera hasta que alguien reiniciara el
        proceso."""

        for _ in range(rate_limit.MAX_INTENTOS):
            rate_limit.anotar_intento_fallido("ana@empresa.mx", "10.0.0.1")

        assert rate_limit.esta_bloqueado("ana@empresa.mx", "10.0.0.1") > 0

        reloj["avanza"](rate_limit.VENTANA_SEGUNDOS + 1)

        assert rate_limit.esta_bloqueado("ana@empresa.mx", "10.0.0.1") == 0

    def test_acertar_limpia_el_bloqueo_puesto(self, reloj):
        """Quien se equivoco cinco veces y luego acierta tiene que poder
        seguir trabajando: el bloqueo se borra entero, no solo la cola de
        intentos.

        Por HTTP esto no se puede probar. El endpoint consulta `esta_bloqueado`
        ANTES de gastar un scrypt, asi que un login correcto nunca llega
        mientras el bloqueo siga vivo, y la rama que limpia quedaria sin
        recorrer. Se comprueba en el modulo, que es donde esta escrita.
        """

        for _ in range(rate_limit.MAX_INTENTOS):
            rate_limit.anotar_intento_fallido("ana@empresa.mx", "10.0.0.1")
        assert rate_limit.esta_bloqueado("ana@empresa.mx", "10.0.0.1") > 0

        rate_limit.anotar_intento_exitoso("ana@empresa.mx", "10.0.0.1")

        assert rate_limit.esta_bloqueado("ana@empresa.mx", "10.0.0.1") == 0

    def test_una_ip_distinta_no_hereda_el_bloqueo(self, reloj):
        """El bloqueo es del par, no de la cuenta. Con el contrario, bloquear a
        alguien seria ya una denegacion de servicio con cinco peticiones."""

        for _ in range(rate_limit.MAX_INTENTOS):
            rate_limit.anotar_intento_fallido("ana@empresa.mx", "10.0.0.1")

        assert rate_limit.esta_bloqueado("ana@empresa.mx", "10.0.0.1") > 0
        assert rate_limit.esta_bloqueado("ana@empresa.mx", "10.0.0.2") == 0

    def test_un_correo_distinto_no_hereda_el_bloqueo(self, reloj):
        """Y al reves: cinco intentos contra una cuenta no tocan a las demas.
        Sin esto, atacar una cuenta deja a todos fuera."""

        for _ in range(rate_limit.MAX_INTENTOS):
            rate_limit.anotar_intento_fallido("ana@empresa.mx", "10.0.0.1")

        assert rate_limit.esta_bloqueado("beto@empresa.mx", "10.0.0.1") == 0

    def test_el_correo_no_distingue_mayusculas_en_la_llave(self, reloj):
        """Con UNIQUE en la base, "Ana@x.mx" y "ana@x.mx" son la misma cuenta.
        Si el contador las guardara distintas, bastaria alternar la caja para
        tener presupuesto doble."""

        for _ in range(rate_limit.MAX_INTENTOS):
            rate_limit.anotar_intento_fallido("ana@empresa.mx", "10.0.0.1")

        assert rate_limit.esta_bloqueado("  ANA@Empresa.MX  ", "10.0.0.1") > 0


# =====================================================================
# El token
# =====================================================================


class TestElToken:
    async def test_el_hash_de_contrasena_no_sale_por_la_api(
        self, async_client_sin_autenticar, db_session
    ):
        """Ni en el login, ni en /auth/me, ni en ninguna respuesta. Un campo
        que no esta declarado en el schema no sale, y por eso el schema esta
        escrito a mano en vez de exponer el modelo entero."""

        usuario = await _crea_usuario(db_session)

        login = await async_client_sin_autenticar.post(
            "/api/v1/auth/login",
            json={"email": usuario.email, "password": "contrasena-larga"},
        )
        cuerpo = login.json()
        assert "password_hash" not in cuerpo["user"]
        assert "contrasena-larga" not in login.text
        assert usuario.password_hash not in login.text

        yo = await async_client_sin_autenticar.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {cuerpo['access_token']}"},
        )
        assert "password_hash" not in yo.text
        assert "password" not in yo.json()

    async def test_un_token_falsificado_no_abre(
        self, async_client_sin_autenticar, test_company
    ):
        """La parte que mas se rompe cuando se reimplementa el JWT a mano: si
        la firma no se compara con constante y en entero, un atacante elige el
        contenido que quiera."""

        token, _ = crear_token("inventado", "nadie@empresa.mx")
        cabecera, cuerpo, firma = token.split(".")
        falsificado = f"{cabecera}.{cuerpo}.{firma[:-2]}XY"

        respuesta = await async_client_sin_autenticar.get(
            "/api/v1/companies/", headers={"Authorization": f"Bearer {falsificado}"}
        )

        assert respuesta.status_code == 401

    async def test_alg_none_no_abre(
        self, async_client_sin_autenticar, test_company
    ):
        """El ataque clasico de JWT: cambiar `alg` por `none` y quitar la
        firma. Se comprueba el algoritmo DESPUES de verificar la firma, asi que
        un token sin firma nunca llega a decirnos que algoritmo quiere."""

        import base64
        import json

        def b64(datos: dict) -> str:
            crudo = json.dumps(datos, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(crudo).decode().rstrip("=")

        cuerpo = b64({"sub": "cualquiera", "typ": "access", "exp": int(time.time()) + 999})
        token = f'{b64({"alg": "none", "typ": "JWT"})}.{cuerpo}.'

        respuesta = await async_client_sin_autenticar.get(
            "/api/v1/companies/", headers={"Authorization": f"Bearer {token}"}
        )

        assert respuesta.status_code == 401

    async def test_el_token_caducado_dice_que_caduco(
        self, async_client_sin_autenticar, test_company
    ):
        """Este es el unico mensaje propio de un 401, y hay una razon: el
        cliente lo necesita. Ante un token caducado tiene que ir al login; ante
        uno invalido no tiene sentido reintentar, porque va a fallar igual.

        `TokenCaducado` se comprueba antes que `ErrorDeToken` en el `except`,
        asi que un token caducado no puede caer en el mensaje generico.
        """

        token, _ = crear_token("alguien", "alguien@empresa.mx", expira_en_minutos=0)
        time.sleep(1.1)

        respuesta = await async_client_sin_autenticar.get(
            "/api/v1/companies/", headers={"Authorization": f"Bearer {token}"}
        )

        assert respuesta.status_code == 401
        assert "sesion" in respuesta.json()["detail"].lower()

    async def test_el_token_de_otro_servidor_no_abre(
        self, async_client_sin_autenticar, test_company, monkeypatch
    ):
        """La clave se lee en el momento de verificar, no se cachea. Un token
        firmado con la clave de otro despliegue tiene que dejar de servir en
        cuanto la clave cambia."""

        token, _ = crear_token("alguien", "alguien@empresa.mx")

        from app.core import security

        monkeypatch.setattr(
            security.settings, "SECRET_KEY", "otra-clave-que-no-es-esta-000000000000"
        )

        respuesta = await async_client_sin_autenticar.get(
            "/api/v1/companies/", headers={"Authorization": f"Bearer {token}"}
        )

        assert respuesta.status_code == 401

    async def test_una_cuenta_dada_de_baja_pierde_el_token_inmediatamente(
        self, async_client_sin_autenticar, db_session
    ):
        """El token sigue siendo criptograficamente valido hasta que caduca. Si
        el token fuera la unica fuente de verdad, dar de baja a alguien no
        tendria efecto hasta las 8 horas siguientes, que es exactamente cuando
        a uno le da por cambiar la contrasena porBSD algo que sospecha."""

        usuario = await _crea_usuario(db_session)
        token, _ = crear_token(str(usuario.id), usuario.email)

        entra = await async_client_sin_autenticar.get(
            "/api/v1/companies/", headers={"Authorization": f"Bearer {token}"}
        )
        assert entra.status_code == 200

        usuario.is_active = False
        await db_session.commit()

        despues = await async_client_sin_autenticar.get(
            "/api/v1/companies/", headers={"Authorization": f"Bearer {token}"}
        )

        assert despues.status_code == 401

    async def test_yo_sin_token_no_devuelve_yo(
        self, async_client_sin_autenticar
    ):
        """`/auth/me` es la llamada de arranque del cliente. Si respondiera
        con un usuario cualquiera, el cliente creeria que la sesion esta viva
        con la pestana cerrada y el token caducado hace horas."""

        respuesta = await async_client_sin_autenticar.get("/api/v1/auth/me")

        assert respuesta.status_code == 401
