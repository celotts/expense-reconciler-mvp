from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    PROJECT_NAME: str = "Expense Reconciler MVP"
    API_V1_STR: str = "/api/v1"
    DATABASE_URL: str
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]

    model_config = SettingsConfigDict(env_file=".env.dev", extra="ignore")

settings = Settings()
