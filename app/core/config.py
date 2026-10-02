
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
    TICKETS_INPUT_DIR: str = "/Users/carloslott/Documents/Tickets_app"

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

    # Tesseract necesita el binario del sistema, no solo el paquete de Python.
    # Si no esta, `OCR_ENABLED=true` no da error al arrancar: da error por cada
    # foto, y el operador ve "fallo de OCR" en lugar de "no instalaste
    # tesseract-ocr". Por eso se comprueba al arrancar y se avisa una vez.
    OCR_ENABLED: bool = True
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

    model_config = SettingsConfigDict(env_file=".env.dev", extra="ignore")


settings = Settings()