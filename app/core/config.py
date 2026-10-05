
import logging
import secrets
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    PROJECT_NAME: str = "Expense Reconciler MVP"
    API_V1_STR: str = "/api/v1"
    DATABASE_URL: str
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # -------------------------------------------------------------------------
    # Autenticacion
    # -------------------------------------------------------------------------
    #
    # `ENVIRONMENT` decide que pasa cuando no hay `SECRET_KEY`.
    #
    # En "production" el arranque falla. Es a proposito: un secreto autogenerado
    # en un despliegue real firma tokens con una clave que nadie mas conoce y
    # que cambia en cada reinicio, y eso se descubre cuando dos instancias
    # del API empiezan a invalidarse los tokens la una a la otra, en
    # produccion, sin error visible.
    #
    # En cualquier otro caso se genera una clave efimera y se avisa. Rompe los
    # tokens al reiniciar, que es molesto en desarrollo y es preferible a
    # tener una clave fija en el codigo: un secreto por defecto hardcodeado es
    # un secreto publico, y con el repositorio abierto eso es cualquiera que
    # quiera firmar tokens validos.
    ENVIRONMENT: str = "development"

    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 horas, una jornada de trabajo

    # AI Settings
    AI_ENABLED: bool = True
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    AZURE_OPENAI_ENDPOINT: str | None = None
    AZURE_OPENAI_API_KEY: str | None = None
    AZURE_OPENAI_API_VERSION: str = "2024-02-15-preview"
    AZURE_OPENAI_DEPLOYMENT: str | None = None
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT: str | None = None

    # Ollama (Local)
    OLLAMA_ENABLED: bool = False
    OLLAMA_BASE_URL: str = ""
    OLLAMA_MODEL: str = "llama3.1:8b"
    OLLAMA_VISION_MODEL: str = "moondream"  # Vision-capable model for image extraction (~1.8GB)
    OLLAMA_TIMEOUT: float = 600.0

    # Local embedding fallback (sentence-transformers)
    LOCAL_EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    # AI Processing settings
    AI_MAX_TOKENS: int = 1200
    AI_TEMPERATURE: float = 0.1
    EMBEDDING_DIMENSIONS: int = 1536
    SIMILARITY_THRESHOLD: float = 0.85

    # -------------------------------------------------------------------------
    # Carpeta de tickets escaneada
    # -------------------------------------------------------------------------
    #
    # La carpeta de la que el escaner lee. Es la unica ruta que el escaner
    # conoce, y no se acepta ninguna otra por parametro: ver la nota de
    # seguridad de `app/services/scan_service.py`, porque aceptar un
    # `folder_path` del cliente es lectura arbitraria del disco del servidor.
    #
    # BAJO `Documents/Tickets/`, y no suelta en `Documents`. La entrada y la de
    # escaneados son **hermanas** dentro de ese padre, y esa hermandad es la que
    # hace funcionar el archivado sin configurarlo: `carpeta_de_escaneados()`
    # resuelve `entrada.parent / "Tickets_Scan"` cuando la salida esta vacia.
    #
    # El padre comun no es estetica: deja las dos carpetas del sistema juntas,
    # arrastrables como una unidad, y separadas de cualquier otra carpeta de
    # papel que haya en `Documents`.
    TICKETS_INPUT_DIR: str = "/Users/carloslott/Documents/Tickets/Tickets_app"

    # Se crea si no existe. Un escaner que aborta porque la carpeta todavia no
    # esta es un escaner que nadie ejecuta la primera vez: el primer arranque
    # es justo el momento en que la carpeta no existe.
    TICKETS_INPUT_DIR_AUTOCREAR: bool = True

    # Subcarpetas. Un comprobante de un viaje esta en `viaje/2025/03/`, y un
    # escaner de un solo nivel no lo veria nunca.
    TICKETS_SCAN_RECURSIVO: bool = True

    # Cada archivo se lee entero para hashearlo. Sin un tope, una carpeta con
    # un video de 2 GB hace que el escaner se quede sin memoria y, con el, caiga
    # tambien el API, porque es el mismo proceso.
    TICKETS_SCAN_MAX_BYTES: int = 25 * 1024 * 1024

    # Archivos por corrida. Un tope por corrida y no por archivo: el escaneo
    # completa con los que alcanza, y el siguiente `POST /scan` sigue.
    TICKETS_SCAN_MAX_ARCHIVOS: int = 200

    # --- Archivado: donde va un comprobante ya resuelto -------------------
    #
    # FUERA de `TICKETS_INPUT_DIR`, y no como una subcarpeta. Es lo unico que
    # hace falta para que el escaneo recursivo no vuelva a leer lo archivado: si
    # `Ticket_Scan` estuviera dentro, el siguiente `POST /scan` veria los
    # archivos movidos, su `relative_path` habria cambiado, el ledger no los
    # reconoceria y re-OCRearia cada corrida.
    #
    # Con Docker, esta es la ruta DENTRO del contenedor, no la del host. La del
    # host va en `TICKETS_SCAN_OUTPUT_HOST_DIR` y la monta `docker-compose.yml`,
    # por el mismo motivo que `TICKETS_INPUT_DIR`: montar `.:/app` y usar una
    # ruta del host seria buscar `/Users/...` dentro del contenedor.
    #
    # VACIA significa "una carpeta hermana de `TICKETS_INPUT_DIR`", y se resuelve
    # en `archivado_service.carpeta_de_escaneados()`.
    #
    # No se pone `/tickets_scan` como omision a proposito: esa ruta solo existe
    # DENTRO del contenedor. Fuera de Docker —los tests, `uvicorn` directo— es un
    # path del sistema y crearlo da "Read-only file system" en macOS. Medido: 24
    # tests caidos por esto. La omision tiene que funcionar en los dos lados, y
    # "hermano de la carpeta de entrada" funciona en los dos.
    TICKETS_SCAN_OUTPUT_DIR: str = ""

    # Apagar el archivado sin tocar la ruta. Con `false`, nada se mueve: util
    # para comparar el comportamiento con y sin, y para un operador que todavia
    # no se fia del movimiento automatico.
    #
    # NO es lo mismo que vaciar la carpeta a mano: con esto apagado el archivo
    # se queda donde estaba y se puede volver a escanear.
    TICKETS_SCAN_ARCHIVAR: bool = True

    # Mover al digitalizar, en vez de esperar a que alguien confirme la compra.
    #
    # Los dos caminos existen y no son lo mismo:
    #
    # - `true` (este): el comprobante se va de la carpeta de entrada en cuanto el
    #   sistema lo intento y guardo su veredicto. La carpeta de entrada queda
    #   como bandeja de trabajo real: lo que hay ahi es lo que NO se ha
    # digitalizado todavia.
    # - `false`: el archivo se queda hasta que alguien confirma la compra
    #   (`TICKETS_SCAN_ARCHIVAR_AL_CONFIRMAR`). Con mas seguridad —solo se mueve
    #   lo que una persona autorizo— y la carpeta de entrada se llena de papel ya
    #   resuelto que hay que distinguir a ojo.
    #
    # Por que `true` por omision, cuando la exactitud del OCR es 33.3%: porque la
    # carpeta de escaneados NO significa "esto se leyo bien", significa "esto ya
    # se intento". El veredicto sigue en `tickets.extraction_status` y la respuesta
    # del escaneo trae `archivados_pendientes`, que es justamente el numero de los
    # que se movieron sin leerse bien. Es un numero visible, no un silencio.
    #
    # Con `false`, la carpeta de entrada mezcla lo pendiente con lo ya resuelto y
    # no hay forma de distinguirlos sin consultar la base.
    TICKETS_SCAN_ARCHIVAR_AL_ESCANEAR: bool = True

    # Mover al confirmar la compra. Es el camino de `false` en el ajuste de
    # arriba, y se puede tener el sincrónico o el otro: con los dos en `true`, el
    # segundo casi nunca dispara, porque el archivo ya se movio al escanear.
    TICKETS_SCAN_ARCHIVAR_AL_CONFIRMAR: bool = True

    # Tesseract necesita el binario del sistema, no solo el paquete de Python.
    # Si no esta, `OCR_ENABLED=true` no da error al arrancar: da error por cada
    # foto, y el operador ve "fallo de OCR" en lugar de "no instalaste
    # tesseract-ocr". Por eso se comprueba al arrancar y se avisa una vez.
    OCR_ENABLED: bool = True
    # --- Cuando hay que preguntarle a la IA -------------------------------
    #
    # El ajuste que decide si una lectura SIN lineas de producto se acepta o se
    # le vuelve a pedir a la IA. Y hay una tension real detrás, que conviene
    # tener escrita:
    #
    # - Por un lado, la regla de la cascada dice que "una foto buena no toca
    #   ningun modelo": el OCR local lee un ticket impreso sin equivocarse y
    #   pagar un modelo por eso es tirar dinero. Es la razon de que el OCR sea el
    #   primer escalon y no una optimizacion menor.
    # - Por otro, un total sin lineas de producto no describe una compra, la
    #   describe a medias. Y sin lineas no hay inventario: que es el motivo de
    #   que exista el modulo.
    #
    # Con `true` (por omision) se le pregunta a la IA **siempre que falten las
    # lineas**, y eso significa que una foto sin lineas cuesta una llamada al
    # modelo. Con una carpeta de 3,000 comprobantes son 3,000 llamadas.
    #
    # Con `false` se aplica la regla de siempre —"bueno es bueno"— y se acepta la
    # lectura sin lineas. El ticket queda sin compra, y eso es correcto para un
    # gasto pero deja el inventario vacio.
    #
    # NO HAY UNA OPCION QUE SEA LAS DOS COSAS: se elige quien paga y quien lee.
    # Lo que no hay es decidir sin saberlo, y por eso el comportamiento viene
    # de aqui y no de una condicion escondida en la cascada.
    ESCALAR_A_IA_SIN_LINEAS: bool = True

    OCR_IDIOMA: str = "spa+eng"
    OCR_PSM: int = 6  # bloque unico: el cuerpo del ticket, sin columnas

    @model_validator(mode="after")
    def _revisa_el_secreto(self) -> "Settings":
        if not self.SECRET_KEY:
            if self.ENVIRONMENT.lower() == "production":
                raise ValueError(
                    "SECRET_KEY es obligatoria en produccion. Sin ella cualquier "
                    "token firmado con la clave de otro despliegue seria valido "
                    "aqui. Genera una con: python3 -c \"import secrets; "
                    'print(secrets.token_urlsafe(48))"'
                )
            self.SECRET_KEY = secrets.token_urlsafe(48)
            logger.warning(
                "SECRET_KEY no esta definida: se genero una clave de un solo uso "
                "para este proceso. Todos los tokens se invalidan al reiniciar. "
                "Defina SECRET_KEY en el entorno antes de desplegar."
            )
        elif len(self.SECRET_KEY) < 32:
            raise ValueError(
                "SECRET_KEY tiene menos de 32 caracteres. Una clave corta se "
                "puede adivinar; la de una sesion de trabajo no cabe en 32."
            )
        return self

    @model_validator(mode="after")
    def _revisa_el_cors(self) -> "Settings":
        """Se niega a arrancar con `*` en `CORS_ORIGINS`.

        La app se sirve con `allow_credentials=True`, y esa combinacion con un
        origen comodin no es "no restrictivo": es el peor de los dos mundos.
        `allow_origins=["*"]` hace que `is_allowed_origin` de Starlette devuelva
        True para cualquier `Origin`, y como hay credenciales, la respuesta
        REFLEJA el origen que pidio el navegador en vez de mandar `*`:

            Access-Control-Allow-Origin: https://sitio-que-no-es-nuestro.example
            Access-Control-Allow-Credentials: true

        Eso es: cualquier pagina web que se abra en el navegador de quien esta
        usando la herramienta puede leer los tickets, las empresas y el extracto
        bancario con la sesion que ya tiene abierta. No hace falta saber la
        contrasena ni tener un token, y el ataque no aparece en el log del
        servidor porque para el servidor es una peticion normal.

        El navegador exige que el script envie el token, asi que si viviera en
        una cookie `HttpOnly` el dano seria de lectura y no de escritura. Este
        API usa `Authorization: Bearer`, que el frontend guarda en `localStorage`,
        y ahi si lo tiene: un `fetch` con cabeceras a medida desde otro origen
        puede reusarlo.

        Por eso no es una advertencia: es un arranque abortado. Es el mismo
        criterio que el del `SECRET_KEY` de menos de 32 caracteres, y por el
        mismo motivo: una configuracion que no se puede usar bien se detecta
        arrancando, no despues de que alguien la haya usado mal.
        """
        if "*" in self.CORS_ORIGINS:
            raise ValueError(
                "CORS_ORIGINS no puede contener '*': la app se sirve con "
                "allow_credentials=True, y con un origen comodin Starlette "
                "refleja CUALQUIER origen con credenciales, que deja que "
                "cualquier sitio web lea la API con la sesion del navegador. "
                'Escriba los origenes uno por uno: CORS_ORIGINS=["http://localhost:3000"]'
            )
        return self

    @model_validator(mode="after")
    def _revisa_la_carpeta_de_tickets(self) -> "Settings":
        """Deja la carpeta de entrada existiendo y utilizable.

        Se valida en el arranque y no en el primer escaneo por una razon
        concreta: la carpeta casi nunca existe en el primer arranque, y esa es
        la unica vez que nadie la ha creado. Si la validacion viviera en
        `POST /scan`, el primer `POST /scan` de la vida de la instalacion
        fallaria, y el mensaje seria "la carpeta no existe" suggestiendo que
        esta mal, cuando lo que pasa es que todavia no hay nada que escanear.

        Que se cree sola y no falle es lo que hace que el primer uso sea un
        `POST /scan` que devuelve cero archivos en vez de un error de
        filesystem.

        Rutas relativas se resuelven contra el directorio de trabajo, que es lo
        que espera cualquiera que escriba `TICKETS_INPUT_DIR=./tickets` sin
        saber donde esta parado el proceso. Se guardan ya expandidas (`~`) y
        absolutas, porque resolverlas en cada escaneo daria la sensacion de que
        el resultado depende de donde se lanzo el proceso.
        """
        ruta = Path(self.TICKETS_INPUT_DIR).expanduser().resolve()
        self.TICKETS_INPUT_DIR = str(ruta)

        if ruta.is_file():
            # Un archivo donde se espera una carpeta: esto no se arregla
            # creando nada, y un `makedirs` sobre un archivo existente revienta
            # con un error de errno que no dice nada util. Se dice aqui, con el
            # nombre de los dos.
            raise ValueError(
                f"TICKETS_INPUT_DIR es un archivo, no una carpeta: {ruta}. "
                "Apuntalo a la carpeta donde estan los tickets."
            )

        if not ruta.exists():
            if not self.TICKETS_INPUT_DIR_AUTOCREAR:
                raise ValueError(
                    f"TICKETS_INPUT_DIR no existe: {ruta}. Defina "
                    "TICKETS_INPUT_DIR_AUTOCREAR=true para crearla al arrancar."
                )
            ruta.mkdir(parents=True, exist_ok=True)
            logger.info("se creo la carpeta de tickets: %s", ruta)
        elif not ruta.is_dir():
            raise ValueError(f"TICKETS_INPUT_DIR no es una carpeta: {ruta}")

        return self

    # Los dos archivos, y en ESTE orden.
    #
    # `docker-compose.yml` carga `.env` y despues `.env.local`, y con la misma
    # variable en los dos gana el ULTIMO. `config.py` hacia lo contrario: leia
    # solo el archivo de configuracion, sin el de la clave. Ese desajuste tiene
    # una consecuencia concreta: la `SECRET_KEY` de `.env.local` solo valia
    # dentro de Docker, y correr `uvicorn` en la maquina para depurar firmaba
    # tokens con la de `.env`. Dos claves, dos sesiones, y un "mi sesion se
    # cae" que no se reproducia donde se reproducia.
    #
    # Ahora los dos leen los dos archivos y en el mismo orden, asi que el token
    # que firma Docker es el mismo que firma `uvicorn` local. Si `.env.local` no
    # existe, se lee solo `.env`: es la configuracion de un clone nuevo.
    #
    # ANTES ERAN TRES ARCHIVOS. `.env` tenia la contrasena de la base, `.env.dev`
    # la configuracion y `.env.local` la clave de firma. Ahora son dos, y la
    # fusion no es cosmetica.
    #
    # Lo que cambia de verdad es que `POSTGRES_PASSWORD` y `PROJECT_NAME` estan
    # en el MISMO archivo. Antes esa separacion era lo que permitia decir "el
    # archivo de configuracion no lleva secretos", y ese criterio ya no se
    # puede aplicar asi. Lo que si se sigue aplicando, y por eso el bloque de
    # `DATABASE_URL` de `.env` lleva su propia nota, es que la contrasena se
    # interpola UNA sola vez (en `docker-compose.yml`) para armar la URL del
    # contenedor. Ese es el unico lugar donde vive una URL con contrasena.
    #
    # Lo que NO se hace es intentar ser clever con la precedencia. Las variables
    # de entorno reales ganan sobre los dos archivos, y `docker-compose` pone
    # `DATABASE_URL` y `TICKETS_INPUT_DIR` en `environment:` justamente para eso.
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        extra="ignore",
    )


settings = Settings()