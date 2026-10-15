from fastapi import Depends, FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.api_router import api_router
from app.core.config import settings
from app.core.database import get_db

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
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
    """Crea el usuario sos (super usuario) si no existe. Permite iniciar
    la administracion de perfiles y crear admins desde el primer arranque."""
    try:
        from app.core.database import AsyncSessionLocal
        from app.core.security import hashear_contrasena
        from app.models.user import UserModel
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            existente = (await db.execute(
                select(UserModel).where(UserModel.email == "sos@empresa.mx")
            )).scalar_one_or_none()
            if existente is None:
                sos = UserModel(
                    email="sos@empresa.mx",
                    nombre="Super Sos",
                    password_hash=hashear_contrasena("SosTesting123"),
                    perfil="admin",
                    grupo="administracion",
                    is_active=True,
                )
                db.add(sos)
                await db.commit()
    except Exception:  # noqa: BLE001
        # No abortar el arranque si el esquema aun no esta listo.
        pass


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
    volvió. El nombre del error de SQLAlchemy tampoco sale en la respuesta: en
    una URL de Postgres el usuario y la contraseña viajan dentro, y un
    healthcheck abierto a localhost no es el lugar donde se publican.
    """
    # `get_db` y no una sesion propia: es la misma sesion que usan los endpoints,
    # y es la que los tests pueden sobreescribir para correr contra SQLite. Una
    # sesion abierta a mano aqui seria real en el contenedor y falsa en la suite.
    try:
        await sesion.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — cualquier falla es "no está sana"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        response.headers["Connection"] = "close"
        return {"status": "unhealthy", "service": settings.PROJECT_NAME,
                "database": "no responde"}

    # `no-store` en el 200 y no solo `close` en el 503: el `close` impide que
    # el 503 se quede pegado, pero no impide que un proxy guarde el 200 viejo y
    # lo sirva despues de una caida. Los dos lados del mismo problema.
    response.headers["Cache-Control"] = "no-store"
    return {"status": "healthy", "service": settings.PROJECT_NAME,
            "database": "ok"}