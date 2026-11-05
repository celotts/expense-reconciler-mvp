#!/usr/bin/env python3
"""Crea una cuenta con acceso. No hay autorregistro, este es el camino.

Por que no hay un formulario de "registrate" en la aplicacion: el sistema
guarda gastos, movimientos bancarios y veredictos que sostienen un cierre
contable. Un alta publica es la forma mas rapida de que un tercero se siente a
escribir ahi, y lo que escribe no se puede deshacer despues. Quien decide que
alguien entra, es quien tiene la base de datos.

La contrasena se pide por teclado y no por argumento. Un `--password` queda en
el historial del shell y en el `ps` de cualquier otro proceso de la maquina, y
despues de eso la unica forma de limpiarla es rotar la contrasena.

Uso:
    PYTHONPATH=. python3 scripts/crear_usuario.py --email ana@empresa.mx
    PYTHONPATH=. python3 scripts/crear_usuario.py --email ana@empresa.mx --desactivar
    PYTHONPATH=. python3 scripts/crear_usuario.py --listar

Idempotente en el sentido de que reintentar no crea un segundo usuario ni
cambia la contrasena del primero: si el correo ya existe, dice que existe y
termina. Cambiar la contrasena es un acto explicito con `--cambiar`, para que
nadie la cambie por accidente pensando que esta creando otra cuenta.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal, engine
from app.core.security import hashear_contrasena
from app.models.user import UserModel

# scrypt con N=2**14 tarda ~100 ms por hash a proposito. Menos de 8 caracteres
# es un numero de intentos acotado; el limite de intentos del login ayuda, pero
# el limite se limpia al reiniciar el proceso y una contrasena corta sobrevive
# a eso.
MINIMO_CONTRASENA = 8


def _pide_contrasena(confirmar: bool) -> str | None:
    while True:
        contrasena = getpass.getpass("Contrasena: ")
        if len(contrasena) < MINIMO_CONTRASENA:
            print(
                f"  Muy corta. Al menos {MINIMO_CONTRASENA} caracteres "
                f"(llevas {len(contrasena)})."
            )
            continue
        if confirmar and contrasena != getpass.getpass("Repite: "):
            # No es un error del usuario, es un error de tecleo. Se repite en
            # vez de terminar: nadie tendria que volver a empezar.
            print("  No coinciden. Intentalo de nuevo.")
            continue
        return contrasena


def _nombre_por_defecto(correo: str) -> str:
    """Si no se pasa `--nombre`, se toma la parte del correo antes de la @.

    Es una Guess, y se marca como tal en la interfaz para que la persona lo
    cambie. Un nombre de mas es mejor que una columna NOT NULL que hay que
    rellenar a mano en el primer despliegue.
    """
    return correo.split("@")[0].replace(".", " ").replace("_", " ").title() or "Usuario"


async def _crear(email: str, nombre: str, contrasena: str) -> int:
    async with AsyncSessionLocal() as session:
        existe = (
            await session.execute(
                select(UserModel).where(func.lower(UserModel.email) == email)
            )
        ).scalar_one_or_none()

        if existe is not None:
            print(f"Ya existe {existe.email} (activo={existe.is_active}).")
            print("Para cambiar la contrasena: --cambiar")
            print("Para dar de baja:          --desactivar")
            return 0

        usuario = UserModel(
            email=email,
            nombre=nombre,
            password_hash=hashear_contrasena(contrasena),
        )
        session.add(usuario)
        try:
            await session.commit()
        except IntegrityError:
            # Dos personas con el mismo correo corriendo el script a la vez.
            # El indice unico es el que garantiza; el SELECT de arriba solo
            # reduce la ventana, no la cierra.
            await session.rollback()
            print(f"Ya existe una cuenta con {email}.")
            return 0

        print(f"Cuenta creada: {usuario.email} ({usuario.nombre})")
        print("El token dura lo que ACCESS_TOKEN_EXPIRE_MINUTES (8 horas por omision).")
        return 0


async def _cambiar(email: str) -> int:
    async with AsyncSessionLocal() as session:
        usuario = (
            await session.execute(
                select(UserModel).where(func.lower(UserModel.email) == email)
            )
        ).scalar_one_or_none()
        if usuario is None:
            print(f"No existe ninguna cuenta con {email}.")
            return 1

        contrasena = _pide_contrasena(confirmar=True)
        if contrasena is None:
            return 1

        usuario.password_hash = hashear_contrasena(contrasena)
        await session.commit()
        print(f"Contrasena cambiada para {usuario.email}.")
        print(
            "Ojo: cambiar la contrasena NO invalida los tokens ya emitidos. "
            "Siguen siendo validos hasta su exp, y expira en 8 horas."
        )
        return 0


async def _alternar(email: str, activar: bool) -> int:
    async with AsyncSessionLocal() as session:
        usuario = (
            await session.execute(
                select(UserModel).where(func.lower(UserModel.email) == email)
            )
        ).scalar_one_or_none()
        if usuario is None:
            print(f"No existe ninguna cuenta con {email}.")
            return 1

        usuario.is_active = activar
        await session.commit()
        estado = "activada" if activar else "dada de baja"
        print(f"Cuenta {estado}: {usuario.email}")
        if activar:
            print("Los tokens que tenia antes vuelven a servir: el token no lleva")
            print("el estado de la cuenta, se comprueba contra la base en cada uso.")
        return 0


async def _listar() -> int:
    async with AsyncSessionLocal() as session:
        cuentas = (
            await session.execute(select(UserModel).order_by(UserModel.email))
        ).scalars().all()

    if not cuentas:
        print("No hay ninguna cuenta. Crea la primera:")
        print("  PYTHONPATH=. python3 scripts/crear_usuario.py --email tu@empresa.mx")
        return 0

    print(f"{'correo':40} {'nombre':22} {'activo':7} ultimo acceso")
    print("-" * 92)
    for cuenta in cuentas:
        acceso = (
            cuenta.last_login_at.strftime("%Y-%m-%d %H:%M")
            if cuenta.last_login_at
            else "nunca"
        )
        print(
            f"{cuenta.email:40} {cuenta.nombre:22} "
            f"{('si' if cuenta.is_active else 'no'):7} {acceso}"
        )
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="Administracion de cuentas")
    parser.add_argument("--email", help="Correo de la cuenta")
    parser.add_argument("--nombre", help="Nombre que se muestra en la interfaz")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument(
        "--cambiar", action="store_true", help="Cambiar la contrasena de una cuenta"
    )
    grupo.add_argument(
        "--desactivar", action="store_true", help="Dar de baja (no borra el historial)"
    )
    grupo.add_argument(
        "--reactivar", action="store_true", help="Volver a habilitar una cuenta"
    )
    grupo.add_argument(
        "--listar", action="store_true", help="Ver las cuentas y su ultimo acceso"
    )
    args = parser.parse_args()

    if args.listar:
        return await _listar()

    if not args.email:
        parser.error("falta --email")

    email = args.email.strip().lower()
    if "@" not in email:
        print("El correo no parece un correo. Sin @ no hay cuenta.")
        return 1

    if args.cambiar:
        return await _cambiar(email)
    if args.desactivar:
        return await _alternar(email, activar=False)
    if args.reactivar:
        return await _alternar(email, activar=True)

    contrasena = _pide_contrasena(confirmar=True)
    if contrasena is None:
        return 1

    nombre = args.nombre or _nombre_por_defecto(email)
    return await _crear(email, nombre, contrasena)


if __name__ == "__main__":
    try:
        codigo = asyncio.run(main())
    finally:
        # El pool se cierra siempre, tambien cuando el script se corta con
        # Ctrl-C a mitad de una transaccion. Sin esto queda una conexion
        # colgada y un aviso de "la base esta ocupada" en la siguiente.
        asyncio.run(engine.dispose())
    sys.exit(codigo)
