"""El origen de una peticion: quien puede leer la API desde el navegador.

Por que esto necesita un test de HTTP y no de codigo. El ataque que se previene
ocurre entero en la CABECERA de la respuesta, y esa cabecera la decide
`CORSMiddleware`, no el codigo de este proyecto. Un test que leyera el `main.py`
comprobando que no hay un `"*"` no probaria nada: el middleware con
`allow_origins=["*"]` y `allow_credentials=True` REFLEJA el `Origin` que le
pidan, y por eso el unico modo de saber que la app esta bien es preguntarle.

El escenario, sin tecnicismos: alguien tiene la herramienta abierta en el
navegador, con su sesion valida. Se abre en otra pestana una pagina que no es
nuestra. Esa pagina pide `/api/v1/tickets` con un `fetch`. Si el servidor
responde `Access-Control-Allow-Origin` con el dominio de esa pagina y
`Access-Control-Allow-Credentials: true`, el navegador le entrega la respuesta y
la pagina lee los tickets, las empresas y el extracto bancario. No hace falta la
contrasena, no hace falta saber que la API existe, y en el log del servidor
aparece una peticion normal.

Lo que tiene que ser verdad:

  1. Un origen que NO esta en la lista no recibe `Access-Control-Allow-Origin`.
  2. La peticion normal (sin `Origin`) no se ve afectada: si no, curl y el CLI
     dejan de poder hablar con la API y la defensa seria "no abras la app".
  3. Un origen que SI esta en la lista lo recibe, porque el frontend de
     desarrollo depende de eso.
"""

from __future__ import annotations

ORIGEN_PROPIO = "http://localhost:3000"
ORIGEN_AJENO = "https://sitio-que-no-es-nuestro.example"


class TestUnOrigenAjenoNoLeeLaApi:
    async def test_no_recibe_permiso_para_leer(self, async_client):
        respuesta = await async_client.get(
            "/api/v1/tickets/",
            headers={"Origin": ORIGEN_AJENO},
        )

        assert (
            respuesta.headers.get("access-control-allow-origin") != ORIGEN_AJENO
        ), (
            "el servidor le permitio leer a un origen que no esta en "
            f"CORS_ORIGINS: {respuesta.headers.get('access-control-allow-origin')!r}"
        )

    async def test_el_credencial_sobrevive_solo_sin_el_permiso_de_origen(
        self, async_client
    ):
        """Lo que de verdad pasa, y por que la cabecera que parece la importante
        no lo es.

        `Access-Control-Allow-Credentials: true` sale en TODAS las respuestas de
        esta app, venga el `Origin` que venga: Starlette la pone en
        `simple_headers` al construir el middleware, sin mirar el origen. Es
        enganoso: aparenta ser lo que protege cuando no lo es.

        Lo que detiene el ataque es la OTRA cabecera: sin
        `Access-Control-Allow-Origin` que coincida con la pestana, el navegador
        descarta la respuesta antes de que el script la lea, y el
        `true` de credenciales no sirve de nada. Por eso los dos tests van
        juntos y por eso el que importa es el del origen.

        Este test deja constancia de la combinacion real para que nadie "lo
        arregle" borrando la cabecera de credenciales y creyendo que con eso se
        resuelve: lo que evita el ataque es no autorizar el origen.
        """
        respuesta = await async_client.get(
            "/api/v1/tickets/",
            headers={"Origin": ORIGEN_AJENO},
        )

        permitido = respuesta.headers.get("access-control-allow-origin")
        credenciales = respuesta.headers.get("access-control-allow-credentials")

        # El navegador bloquea por la ausencia del permiso de origen.
        assert permitido != ORIGEN_AJENO
        # Y esto es lo que hace que el test de al lado no sea opcional.
        assert credenciales == "true"

    async def test_el_preflight_tampoco_se_le_abre(self, async_client):
        """El `OPTIONS` es la peticion que decide si el `GET` siguiente se
        puede hacer. Si el preflight se responde bien, el navegador continua.

        Se prueba por separado porque es un camino distinto del codigo dentro
        del middleware: una defensa que solo mirara `simple_response`
        dejaria pasar el preflight.
        """
        respuesta = await async_client.options(
            "/api/v1/tickets/",
            headers={
                "Origin": ORIGEN_AJENO,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

        permitido = respuesta.headers.get("access-control-allow-origin")
        assert permitido != ORIGEN_AJENO, (
            f"el preflight le abrio la puerta a {ORIGEN_AJENO}: {permitido!r}"
        )

    async def test_un_peticion_sin_origin_sigue_siendo_una_peticion_normal(
        self, async_client
    ):
        """Sin cabecera `Origin` no hay CORS que aplicar.

        Lo comprueba lo contrario de lo que parece: si el middleware se
        rompiera y'empezara a contestar 403 a quien no manda `Origin`, curl y el
        CLI quedarían sin poder hablar con la API.
        """
        respuesta = await async_client.get("/api/v1/tickets/")

        assert respuesta.status_code == 200


class TestElOrigenDeLaAppSiFunciona:
    async def test_el_origen_declarado_si_puede_leer(self, async_client):
        """La defensa no puede ser "no abras la app".

        Con el origen en `CORS_ORIGINS`, la respuesta trae el permiso y las
        credenciales: es lo que hace que el frontend en Vite funcione.
        """
        respuesta = await async_client.get(
            "/api/v1/tickets/",
            headers={"Origin": ORIGEN_PROPIO},
        )

        assert respuesta.headers.get("access-control-allow-origin") == ORIGEN_PROPIO
        assert respuesta.headers.get("access-control-allow-credentials") == "true"