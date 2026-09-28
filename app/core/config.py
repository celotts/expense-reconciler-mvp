
import logging
import secrets

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
    OLLAMA_VISION_MODEL: str = "llava:7b"  # Vision-capable model for image extraction
    OLLAMA_TIMEOUT: float = 600.0

    # Local embedding fallback (sentence-transformers)
    LOCAL_EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    # AI Processing settings
    AI_MAX_TOKENS: int = 1200
    AI_TEMPERATURE: float = 0.1
    EMBEDDING_DIMENSIONS: int = 1536
    SIMILARITY_THRESHOLD: float = 0.85

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

    model_config = SettingsConfigDict(env_file=".env.dev", extra="ignore")


settings = Settings()