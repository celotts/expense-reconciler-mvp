from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.DATABASE_URL, echo=True, future=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """La sesion que usan todos los endpoints.

    Dice `AsyncIterator` y no `AsyncSession` porque es lo que ES: una funcion
    generadora asincrona que produce una sesion y la cierra al terminar. La
    anotacion anterior decia `AsyncSession`, que describe lo que hay DENTRO del
    `yield`, no lo que devuelve la funcion — y esa confusion es la que hace que
    el `Depends(get_db)` se entienda como "llama a get_db y ya tienes sesion",
    que es justo el uso que FastAPI no tiene y por el que se abren sesiones a
    mano que nunca se cierran.

    `AsyncIterator` y no `AsyncGenerator[..., None]`: ambos son correctos y el
    segundo dice una segunda cosa que aqui no se usa — que el generador puede
    recibir `send()`. Este no.
    """
    async with AsyncSessionLocal() as session:
        yield session
