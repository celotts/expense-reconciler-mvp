import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.api_router import api_router
from app.core.config import settings
from app.core.database import get_db

app = FastAPI(
    title=settings.PROJECT_NAME, version="0.1.0", docs_url="/docs", redoc_url="/redoc"
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

    ## POR QUE NO HAY CONTRASENA POR DEFECTO

    MEDIDO: antes se creaba `sos@empresa.mx` / `SosTesting123` en cada
    arranque. Eso es un backdoor: cualquier persona que sepa el repo sabe la
    contraseña del primer admin. Para un producto que se vende, eso es inaceptable.

    Ahora: si no hay NINGUN usuario, se crea uno con email `admin@localhost`
    y contraseña aleatoria de 16 caracteres. La contraseña se imprime UNA VEZ
    en el log del arranque. Si ya hay usuarios, no se toca nada.

    El usuario la cambia en el primer login (el frontend lo fuerza).
    """
    try:
        import secrets

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

                # Obtiene las variables desde el archivo .env o .env.local
                alfabeto = os.getenv("password_inicial")
                username = os.getenv("email", "admin@localhost")

                # Verificación de que el alfabeto se cargó correctamente
                if not alfabeto:
                    raise ValueError(
                        "Error: No se encontró la variable 'password_inicial' en el archivo .env"
                    )

                print("Alfabeto cargado con éxito para generar la clave inicial.")

                # Genera una contraseña aleatoria de 16 caracteres usando el alfabeto del .env
                password_inicial = "".join(secrets.choice(alfabeto) for _ in range(16))

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

                # Se imprime UNA vez. No se vuelve a imprimir porque total != 0.
                print("=" * 60)
                print("  PRIMER ARRANQUE: usuario inicial creado")
                print(f"  Email:      {username}")
                print(f"  Contraseña: {password_inicial}")
                print("  Cambiala en el primer login.")
                print("=" * 60)
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