#!/usr/bin/env python3
"""Crea usuarios de prueba para los tres ambientes (dev, qa, prod).

Cada ambiente recibe un set de usuarios con los perfiles del sistema, para
poder probar el control de acceso por rol y por grupo. Las contraseñas siguen
la convencion `<ambiente>Testing123` (devTesting123, qaTesting123, prodTesting123).

Idempotente: si el correo ya existe, se omite (no cambia la contraseña).

Uso:
    PYTHONPATH=. python3 scripts/crear_usuarios_prueba.py
    PYTHONPATH=. python3 scripts/crear_usuarios_prueba.py --ambiente dev
"""

from __future__ import annotations
import argparse
import asyncio
import sys
from typing import Iterable

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.security import hashear_contrasena
from app.models.user import UserModel

# Perfiles del sistema y su grupo asociado.
PERFILES = [
    ("admin", "administracion"),
    ("develop", "administracion"),
    ("qa", "administracion"),
    ("programador", "administracion"),
    ("supervisor", "supervision"),
    ("digitalizador", "operaciones"),
    ("consultor", "consultas"),
]

AMBIENTES = ["dev", "qa", "prod"]


async def crear_ambiente(ambiente: str, perfiles: list | None = None) -> None:
    contrasena = f"{ambiente}Testing123"
    print(f"\n=== Ambiente: {ambiente} (password: {contrasena}) ===")
    creados = 0
    omitidos = 0
    perfiles_usar = perfiles if perfiles else PERFILES
    async with AsyncSessionLocal() as session:
        for perfil, grupo in perfiles_usar:
            email = f"{perfil}-{ambiente}@empresa.mx"
            nombre = f"{perfil.capitalize()} {ambiente.upper()}"
            existe = (
                await session.execute(
                    select(UserModel).where(func.lower(UserModel.email) == email)
                )
            ).scalar_one_or_none()
            if existe is not None:
                omitidos += 1
                print(f"  [omitido] {email} ya existe")
                continue
            usuario = UserModel(
                email=email,
                nombre=nombre,
                password_hash=hashear_contrasena(contrasena),
                perfil=perfil,
                grupo=grupo,
                is_active=True,
            )
            session.add(usuario)
            try:
                await session.commit()
            except Exception as exc:
                await session.rollback()
                print(f"  [error] {email}: {exc}")
                continue
            creados += 1
            print(f"  [creado]  {email} (perfil={perfil}, grupo={grupo})")
    print(f"  Resumen: {creados} creados, {omitidos} omitidos")


async def main(ambientes: Iterable[str], perfiles: list | None = None) -> int:
    perfiles_usar = perfiles if perfiles else PERFILES
    for amb in ambientes:
        await crear_ambiente(amb, perfiles_usar)
    print("\nAcceso: http://localhost:3000  (login con email/password de arriba)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ambiente", choices=AMBIENTES)
    parser.add_argument("--perfil", choices=[p for p, _ in PERFILES])
    args = parser.parse_args()
    ambientes = [args.ambiente] if args.ambiente else AMBIENTES
    perfiles_filtrados = [pg for pg in PERFILES if (args.perfil is None or pg[0] == args.perfil)]
    sys.exit(asyncio.run(main(ambientes, perfiles_filtrados)))
