"""La sesion contra Postgres real, de extremo a extremo.

Los tests de `tests/integration/test_auth_api.py` corren contra SQLite, y
SQLite no se parece a Postgres en tres cosas que aqui importan:

  1. **Tipos.** El `sub` de un token es texto y la columna `users.id` es UUID.
     SQLite acepta comparar los dos sin quejarse. Postgres no: lanza un error
     de tipo, que es un 500 y no un 401. Esto ya paso de verdad al construir
     la dependencia, y el test de SQLite estaba en verde mientras tanto.
  2. **`lower()` sobre el indice.** El login busca con
     `func.lower(UserModel.email) == correo`. Postgres puede usar el indice
     para eso; SQLite no tiene indice ninguno. Que aqui tarde 300 ms en vez de
     3 no es lo que se verifica, pero un `COLLATE` mal puesto si lo detectaria.
  3. **Restricciones y valores por omision.** El `UNIQUE` del correo, el
     `DEFAULT true` de `is_active`, los `NOT NULL`. SQLite los aplica tambien,
     pero con su propia traduccion, y un default que se define distinto se ve
     al leer la tabla, no al probarla.

Este script levanta el MISMO codigo de la API contra la base migrada, entra
con un token de verdad y comprueba que el token abre lo que tiene que abrir.

    PYTHONPATH=. DATABASE_URL=... python3 scripts/verify_postgres_auth.py
"""

import os
import asyncio
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import rate_limit
from app.core.security import (
    crear_token,
    hashear_contrasena,
    leer_token,
    verificar_contrasena,
)
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.models.user import UserModel

def _fatal(mensaje: str) -> str:
    """Sale con un mensaje, en vez de un KeyError sin pista."""
    print(f"ERROR: {mensaje}", file=sys.stderr)
    raise SystemExit(2)


# El DSN se lee del entorno, no se escribe aqui. Este archivo estuvo versionado
# en un repositorio publico con la password escrita en esta linea, y eso permitia
# a cualquiera entrar a la base de datos. Rotar la password no borra el archivo
# viejo del historial: ademas de rotarla, la password deja de estar en el codigo.
#     export POSTGRES_PASSWORD=...      (o ponla en .env.local)
DSN = "postgresql+asyncpg://postgres:{}@localhost:5434/expense_db".format(
    os.environ.get("POSTGRES_PASSWORD") or _fatal("POSTGRES_PASSWORD no esta definida.")
)

fallos: list[str] = []
_correo_de_la_corrida: str | None = None
_ticket_de_la_corrida = None
_empresa_de_la_corrida = None


def check(desc: str, condicion: bool) -> None:
    print(f"  {'OK  ' if condicion else 'FALLA'}  {desc}")
    if not condicion:
        fallos.append(desc)


async def _limpiar_si_hubo_excepcion(motor) -> None:
    """Borra lo que creo la corrida, aunque reviente a mitad.

    Sin esto cada corrida deja una cuenta y un ticket de verificacion, y la
    tabla `users` -- que es pequena y que alguien va a listar con
    `crear_usuario.py --listar` -- se llena de `verificacion...@local.test`.

    Es `async` y no una funcion normal a proposito: se llama desde un `finally`
    que esta DENTRO de `asyncio.run`, y un `asyncio.run` anidado lanza
    "cannot be called from a running event loop". La version anterior fallaba
    exactamente ahi, y como el aviso se imprimia y no se.cancelaba la corrida,
    el verificador decia CUMPLE dejando basura.
    """
    if _correo_de_la_corrida is None and _empresa_de_la_corrida is None:
        return

    try:
        async with async_sessionmaker(motor, expire_on_commit=False)() as db:
            if _ticket_de_la_corrida is not None:
                await db.execute(
                    TicketModel.__table__.delete().where(
                        TicketModel.id == _ticket_de_la_corrida
                    )
                )
            # La empresa se guarda con SU propia guarda. Atar su borrado a
            # `_ticket_de_la_corrida` suena logico y no lo es: la seccion 6 ya
            # borro el ticket y dejo esa variable en None, asi que la empresa
            # se quedaba para siempre. Un verificador que acumula lo que
            # verifica deja de poder verificar.
            if _empresa_de_la_corrida is not None:
                await db.execute(
                    CompanyModel.__table__.delete().where(
                        CompanyModel.id == _empresa_de_la_corrida
                    )
                )
            if _correo_de_la_corrida is not None:
                await db.execute(
                    UserModel.__table__.delete().where(
                        func.upper(UserModel.email) == _correo_de_la_corrida.upper()
                    )
                )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"  AVISO: no se pudo limpiar la cuenta de prueba: {exc}")


async def main() -> int:
    global _correo_de_la_corrida, _ticket_de_la_corrida, _empresa_de_la_corrida

    motor = create_async_engine(DSN, echo=False)
    Session = async_sessionmaker(motor, expire_on_commit=False)
    correo = f"verificacion{uuid.uuid4().hex[:10]}@local.test"
    _correo_de_la_corrida = correo

    try:
        async with Session() as db:
            # ----------------------------------------------------------------
            print("\n1) La tabla existe con las restricciones que el codigo espera")
            # ----------------------------------------------------------------
            usuario = UserModel(
                email=correo,
                nombre="Verificacion Local",
                password_hash=hashear_contrasena("contrasena-de-verificacion"),
            )
            db.add(usuario)
            await db.commit()
            await db.refresh(usuario)

            check("el id lo pone la base (uuid_generate_v4)", usuario.id is not None)
            check("is_active sale de la base, no del codigo", usuario.is_active is True)
            check(
                "created_at tiene zona horaria",
                usuario.created_at.tzinfo is not None,
            )
            check("last_login_at arranca en NULL", usuario.last_login_at is None)

            # Se copia el id a una variable suelta antes de seguir. Un
            # `rollback()` mas abajo expira los atributos del objeto, y
            # entonces `usuario.id` intenta una carga perezosa, que en un
            # contexto async lanza MissingGreenlet. Guardar el valor aqui
            # evita depender de cuando se leen los atributos.
            usuario_id = usuario.id
            hash_guardado = usuario.password_hash

            # ----------------------------------------------------------------
            print("\n2) El hash: scrypt con memoria real, no la de SQLite")
            # ----------------------------------------------------------------
            check(
                "el hash se guardo entero y con la version",
                hash_guardado.startswith("scrypt-v1$"),
            )
            check(
                "la contrasena verifica contra lo guardado",
                verificar_contrasena("contrasena-de-verificacion", hash_guardado),
            )
            check(
                "otra contrasena no verifica",
                not verificar_contrasena("otra-cosa", hash_guardado),
            )
            check(
                "el hash cabe en la columna de 255",
                len(hash_guardado) <= 255,
            )

            # ----------------------------------------------------------------
            print("\n3) El indice UNIQUE del correo manda, tenga la caja que tenga")
            # ----------------------------------------------------------------
            # El UNIQUE va sobre `lower(email)`. Con un UNIQUE sobre la columna
            # tal cual, "Ana@empresa.mx" y "ana@empresa.mx" serian dos cuentas
            # validas, y el login (que siempre busca con `lower()`) devolveria
            # dos filas y reventaria con MultipleResultsFound: un 500 en vez
            # de un mensaje de "esa cuenta ya existe".
            #
            # SQLite no distingue mayusculas en un UNIQUE, asi que este test
            # NO puede vivir en la suite: ahi pasaria con cualquiera de las dos
            # versiones del indice. Por eso esta aqui.
            try:
                db.add(
                    UserModel(
                        email=correo.upper(),
                        nombre="Duplicado en mayusculas",
                        password_hash=hashear_contrasena("x" * 12),
                    )
                )
                await db.commit()
                unico = False
            except Exception:  # noqa: BLE001
                await db.rollback()
                unico = True
            check('"VERIFICACION...@LOCAL.TEST" choca con el indice unico', unico)

            # Y que la consulta del login no encuentre jamas dos filas, que es
            # lo que la dejaria reventar.
            async with Session() as otra:
                coincidencias = (
                    await otra.execute(
                        select(func.count())
                        .select_from(UserModel)
                        .where(func.lower(UserModel.email) == correo)
                    )
                ).scalar_one()
            check("la busqueda del login devuelve exactamente una fila", coincidencias == 1)

            # El `rollback` de arriba expiro el objeto de la sesion, asi que
            # `usuario` ya no vale: leer un atributo suyo dispara una carga
            # perezosa, que en async lanza MissingGreenlet. Se vuelve a leer
            # con un SELECT, que ademas comprueba que la fila sigue ahi
            # despues del intento fallido.
            otra_vez = (
                await db.execute(select(UserModel).where(UserModel.id == usuario_id))
            ).scalar_one_or_none()
            check("la cuenta sigue ahi tras el intento fallido", otra_vez is not None)
            usuario = otra_vez

            # ----------------------------------------------------------------
            print("\n4) El token contra una columna UUID de verdad")
            # ----------------------------------------------------------------
            # ESTE es el punto del script. Con SQLite, comparar el `sub` (texto)
            # con `users.id` (uuid) no daña nada. Con Postgres revienta.
            token, segundos = crear_token(str(usuario_id), correo)
            claims = leer_token(token)
            check("el sub del token es el texto del id", claims["sub"] == str(usuario_id))

            from app.core.deps import get_current_user
            from fastapi.security import HTTPAuthorizationCredentials

            leido = await get_current_user(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db
            )
            check("la dependencia encontro la fila por el UUID del sub", leido.id == usuario_id)

            # Y el caso que reventaba: un `sub` que no es un UUID tiene que dar
            # 401, no un error de tipo del driver.
            from fastapi import HTTPException

            token_malo, _ = crear_token("esto-no-es-un-uuid", "x@y.mx")
            try:
                await get_current_user(
                    HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_malo), db
                )
                dio_401 = False
            except HTTPException as exc:
                dio_401 = exc.status_code == 401
            check("un sub que no es UUID da 401 y no un 500", dio_401)

            # ----------------------------------------------------------------
            print("\n5) La cuenta dada de baja pierde el acceso al instante")
            # ----------------------------------------------------------------
            usuario.is_active = False
            await db.commit()
            try:
                await get_current_user(
                    HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db
                )
                bloqueada = False
            except HTTPException as exc:
                bloqueada = exc.status_code == 401
            check("el mismo token deja de servir en la siguiente peticion", bloqueada)

            usuario.is_active = True
            await db.commit()

            # ----------------------------------------------------------------
            print("\n6) La autoria se escribe como texto, no como llave")
            # ----------------------------------------------------------------
            # `spot_checked_by` es VARCHAR(255) a proposito, no una llave
            # foranea. Si algún dia alguien lo cambiara por una llave, dar de
            # baja el veredicto borraria el dato de quien lo firmo, que es
            # justo lo que la columna existe para no perder.
            # Se crea la empresa de la corrida en vez de reutilizar una que ya
            # este. Reutilizar dejaria un ticket de verificacion colgado de una
            # empresa real, y el siguiente que consultara esa empresa veria un
            # ticket que no es suyo. Ademas asi la seccion corre siempre: si se
            # salta cuando la base esta vacia, nadie se entera de que no se
            # probo.
            empresa = CompanyModel(
                name=f"Verify auth {uuid.uuid4().hex[:6]}",
                tax_id=f"A{uuid.uuid4().hex[:9].upper()}",
            )
            db.add(empresa)
            await db.commit()
            await db.refresh(empresa)
            company_id = empresa.id
            _empresa_de_la_corrida = company_id

            ticket = TicketModel(
                company_id=company_id,
                provider_name="WALMART",
                total_amount=Decimal("100.00"),
                tax_amount=Decimal("16.00"),
                expense_date=date(2025, 1, 15),
                extraction_status="AUTO_APROBADO",
                confidence=Decimal("0.95"),
                confidence_source="llm",
                source_type="image",
                source_hash=uuid.uuid4().hex,
                spot_check_status="PENDIENTE",
                spot_checked_by=correo,
                reviewed_by=correo,
            )
            db.add(ticket)
            await db.commit()
            await db.refresh(ticket)
            _ticket_de_la_corrida = ticket.id

            # Se borra una cuenta APARTE, no esta. Si se borrara esta, las
            # secciones siguientes trabajarian con un objeto desligado: poner
            # `last_login_at` en uno ya borrado no persiste nada, y el
            # `refresh` de despues lanzaria InstanceNotPersistent, que es un
            # fallo del script disfrazado de fallo de la base.
            efimera = UserModel(
                email=f"efimera{uuid.uuid4().hex[:8]}@local.test",
                nombre="Cuenta que se borra",
                password_hash=hashear_contrasena("contrasena-efimera"),
            )
            db.add(efimera)
            await db.commit()
            await db.refresh(efimera)
            efimera_id = efimera.id
            await db.delete(efimera)
            await db.commit()

            async with Session() as otra:
                sobrevive = (
                    await otra.execute(
                        select(TicketModel).where(TicketModel.id == ticket.id)
                    )
                ).scalar_one_or_none()
                borrada = (
                    await otra.execute(
                        select(UserModel).where(UserModel.id == efimera_id)
                    )
                ).scalar_one_or_none()
            check("la cuenta de prueba si se borro", borrada is None)
            check(
                "el veredicto sobrevive a que se borre la cuenta",
                sobrevive is not None and sobrevive.spot_checked_by == correo,
            )

            await db.execute(
                TicketModel.__table__.delete().where(TicketModel.id == ticket.id)
            )
            await db.commit()
            _ticket_de_la_corrida = None

            # ----------------------------------------------------------------
            print("\n7) El limite de intentos contra el reloj de verdad")
            # ----------------------------------------------------------------
            # No prueba el limite (eso lo hace la suite con reloj falso), sino
            # que el modulo se importa y se usa sin romperse en este proceso.
            rate_limit.reiniciar()
            remoto = "10.99.0.1"
            bloqueados = 0
            for _ in range(rate_limit.MAX_INTENTOS):
                bloqueados = rate_limit.anotar_intento_fallido(correo, remoto)
            check("cinco intentos seguidos frenan", bloqueados > 0)
            check(
                "y el par queda bloqueado",
                rate_limit.esta_bloqueado(correo, remoto) > 0,
            )
            rate_limit.reiniciar()
            check("reiniciar() lo deja limpio", rate_limit.esta_bloqueado(correo, remoto) == 0)

            # ----------------------------------------------------------------
            print("\n8) La hora la pone la base, no la maquina")
            # ----------------------------------------------------------------
            # `last_login_at` lo escribe el codigo con `utcnow()`. Lo que se
            # comprueba aqui es que al releerlo siga siendo comparable con una
            # fecha de la base. Un TIMESTAMP sin zona guardado en una columna
            # con zona (o al reves) es el clasico "el reporte dice que el
            # acceso fue hace seis horas".
            marca: datetime = datetime.now(timezone.utc)
            usuario.last_login_at = marca
            await db.commit()
            await db.refresh(usuario)
            check(
                "last_login_at vuelve con zona horaria de la base",
                usuario.last_login_at.tzinfo is not None,
            )
            check(
                "y no se movio de hora al cruzar la base de ida y vuelta",
                abs((usuario.last_login_at - marca).total_seconds()) < 1,
            )
    finally:
        await _limpiar_si_hubo_excepcion(motor)
        await motor.dispose()

    print()
    if fallos:
        print(f"{len(fallos)} de las comprobaciones fallaron:")
        for desc in fallos:
            print(f"  - {desc}")
        return 1
    print("CUMPLE: la sesion funciona contra Postgres real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
