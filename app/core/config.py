
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Expense Reconciler MVP"
    API_V1_STR: str = "/api/v1"
    DATABASE_URL: str
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

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
    OLLAMA_TIMEOUT: float = 600.0

    # Local embedding fallback (sentence-transformers)
    LOCAL_EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    # AI Processing settings
    AI_MAX_TOKENS: int = 1200
    AI_TEMPERATURE: float = 0.1
    EMBEDDING_DIMENSIONS: int = 1536
    SIMILARITY_THRESHOLD: float = 0.85

    model_config = SettingsConfigDict(env_file=".env.dev", extra="ignore")


settings = Settings()