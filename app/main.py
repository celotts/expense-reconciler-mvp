import logging
import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.api_router import api_router
from app.core.config import settings
from app.core.database import get_db

_es_produccion = settings.ENVIRONMENT.lower() == "production"
logger = logging.getLogger(__name__)
_docs_url = None if _es_produccion else "/docs"
_redoc_url = None if _es_produccion else "/redoc"

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    docs_url=_docs_url,
    redoc_url=_redoc_url,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)


@app.on_event("startup")
async def _sembrar_sos() -> None:
    """Crea el usuario inicial si no existe ninguno.

    Usa exactamente los valores `email` y `password_inicial` del entorno. El
    password no se genera ni se imprime: quien instala la herramienta elige la
    clave en `.env` y esta función solo guarda su hash. Si ya hay usuarios, no
    se toca nada.
    """
    try:
        from sqlalchemy import func, select

        from app.core.database import AsyncSessionLocal
        from app.core.security import hashear_contrasena
        from app.models.user import UserModel

        async with AsyncSessionLocal() as db:
            total = (
                await db.execute(select(func.count()).select_from(UserModel))
            ).scalar_one()

            if total == 0:
                load_dotenv()

                password_inicial = os.getenv("password_inicial")
                username = os.getenv("email", "admin@localhost.com").strip().lower()

                if not password_inicial:
                    raise ValueError(
                        "Error: No se encontró la variable 'password_inicial' en el archivo .env"
                    )

                admin = UserModel(
                    email=username,
                    nombre="Administrador",
                    password_hash=hashear_contrasena(password_inicial),
                    perfil="admin",
                    grupo="administracion",
                    is_active=True,
                )
                db.add(admin)
                await db.commit()

                print("=" * 60)
                print("  PRIMER ARRANQUE: usuario inicial creado")
                print(f"  Email:      {username}")
                print("  Contraseña: la definida en password_inicial del .env")
                print("  Cambiala en el primer login.")
                print("=" * 60)
    except Exception:  # noqa: BLE001
        # No abortar el arranque si el esquema aun no esta listo, pero deja
        # el fallo visible: un admin que no se crea deja a quien instala sin
        # forma de entrar.
        logger.exception("No se pudo crear el usuario inicial")


@app.get("/health", tags=["Health Check"])
async def health_check(
    response: Response,
    sesion: AsyncSession = Depends(get_db),
) -> dict:
    """El chequeo de vida, y de qué la app depende para funcionar.

    ## POR QUE CONSULTA LA BASE Y NO SOLO EL PROCESO

    MEDIDO en este repo antes de este cambio: con `expense_pgvector` parado,
    este endpoint respondía **200 `{"status": "healthy"}`**. El contenedor se
    declaraba `healthy` y `docker compose ps` decía que todo estaba en orden
    mientras **ninguna ruta que lea datos funcionaba**: cada una devolvía 500
    por conexión rechazada.

    Eso es un healthcheck que no sabe nada, y el daño no es solo theoretical:
    es el que decide si `expense-front` puede levantarse
    (`depends_on: service_healthy`). Con esta mentira, el front levantaba
    apuntando a una API que no podía contestar y el síntoma era "el front está
    arriba pero nada carga".

    ## POR QUE NO ES UNA DEFENSA CONTRA CUALQUIER FALLA

    `SELECT 1` prueba que hay una conexión y que Postgres responde. **No**
    comprueba que el esquema esté cargado: una base a la que le falta `tickets`
    pasa este chequeo y falla en cada endpoint. Para eso está el healthcheck del
    contenedor de Postgres, que pregunta por `to_regclass('public.tickets')` y
    no por el socket — porque durante el init la conexión responde antes de que
    las tablas existan. Son dos preguntas distintas y cada una la contesta su
    healthcheck: esta dice "hay base", el otro dice "la base tiene el esquema".

    ## POR SIGUE SIN PEDIR TOKEN

    Un monitor externo no tiene credenciales, y uno que las pide reporta caído un
    servicio que está arriba. Y la respuesta no dice nada del dominio: ni
    tickets, ni montos, ni cuentas. Solo el estado de la conexión.

    El 503 lleva `Connection: close` porque, sin él, nginx y el navegador
    pueden cachear el 503 y seguir mostrando "caído" después de que la base
    volvía. El nombre del error de SQLAlchemy tampoco sale en la respuesta: en
    una URL de Postgres el usuario y la contraseña viajan dentro, y un
    healthcheck abierto a localhost no es el lugar donde se publican.
    """
    try:
        await sesion.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — cualquier falla es "no está sana"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        response.headers["Connection"] = "close"
        return {
            "status": "unhealthy",
            "service": settings.PROJECT_NAME,
            "database": "no responde",
        }

    response.headers["Cache-Control"] = "no-store"
    return {"status": "healthy", "service": settings.PROJECT_NAME,
            "database": "ok"}