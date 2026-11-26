"""Permisos por accion, derivados del rol EN la empresa.

El rol no autoriza por si solo una accion: `owner` y `viewer` son dos cadenas,
pero la pregunta que hay que responder es "¿puede crear / editar / exportar?".
Tener el mapa en un solo sitio evita que cada router wrote su propio `if
rol == ...`, que es como se pierde una accion nueva: el endpoint se anade, el
front lo pinta y nadie recuerda actualizar la lista.

Este modulo NO decide nada por su cuenta. La autoridad sigue siendo la fila de
`usuarios_empresas` leida en cada peticion; aqui solo se traduce esa fila a
acciones. El front recibe la misma traduccion en `GET /auth/empresas` para no
ofrecer botones que el servidor va a rechazar, pero un front desactualizado no
puede ampliar los permisos: la guardia de `deps.py` vuelve a preguntar en BD.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from app.core.enums import RolEmpresa


class Permiso(str, Enum):
    """Las acciones que una pantalla puede pedirle a la API."""

    LEER = "leer"
    CREAR = "crear"
    EDITAR = "editar"
    BORRAR = "borrar"
    APROBAR = "aprobar"
    CONCILIAR = "conciliar"
    EXPORTAR = "exportar"
    CERRAR = "cerrar"
    ADMINISTRAR = "administrar"
    AUDITAR = "auditar"


TODOS_LOS_PERMISOS: frozenset[Permiso] = frozenset(Permiso)

# `operator` conserva crear y editar porque digitaliza y trabaja en su caja; no
# concilia, no exporta y no administra. `accountant` conserva las acciones de
# cierre y conciliacion que ya tenia; solo pierde administrar, que ademas lo
# exige `requiere_rol` en las rutas de Mantenimiento. Ningun rol existente pierde
# una accion que hoy pudiera ejecutar: `viewer` es el unico nuevo y es de solo
# lectura.
PERMISOS_POR_ROL: dict[RolEmpresa, frozenset[Permiso]] = {
    RolEmpresa.OWNER: TODOS_LOS_PERMISOS,
    RolEmpresa.ADMIN: TODOS_LOS_PERMISOS,
    RolEmpresa.ACCOUNTANT: TODOS_LOS_PERMISOS - {Permiso.ADMINISTRAR},
    RolEmpresa.OPERATOR: frozenset(
        {Permiso.LEER, Permiso.CREAR, Permiso.EDITAR, Permiso.CERRAR}
    ),
    RolEmpresa.VIEWER: frozenset({Permiso.LEER}),
}


def permisos_de_rol(rol: RolEmpresa | str | None) -> tuple[str, ...]:
    """Los permisos del rol, en un orden fijo para que el JSON sea estable.

    Un rol desconocido devuelve los permisos de `viewer`, no todos: un valor que
    no entendemos no puede ampliar la autoridad. El CHECK de la base ya impide
    que aparezca uno nuevo sin migracion, pero un dato antiguo no deberia volverse
    admin porque el enum cambio.
    """
    miembro = a_rol_de_empresa(rol)
    concedidos = PERMISOS_POR_ROL.get(miembro, PERMISOS_POR_ROL[RolEmpresa.VIEWER])
    return tuple(permiso.value for permiso in Permiso if permiso in concedidos)


def a_rol_de_empresa(rol: RolEmpresa | str | None) -> RolEmpresa | None:
    """Traduce un valor de BD sin convertir un dato raro en un 500.

    Un CHECK impide escribir roles nuevos en Postgres, pero una fila antigua o
    una base restaurada pueden traer cualquier cadena. `RolEmpresa(rol)` lanza
    `ValueError` y eso se veria como fallo del servidor en medio de una
    peticion. Aqui ese dato no se arregla: simplemente no autoriza nada.
    """
    if isinstance(rol, RolEmpresa):
        return rol
    try:
        return RolEmpresa(rol)
    except ValueError:
        return None


def roles_validos(roles: Iterable[RolEmpresa | str | None]) -> tuple[RolEmpresa, ...]:
    return tuple(v for rol in roles if (v := a_rol_de_empresa(rol)) is not None)


def tiene_permiso(rol: RolEmpresa | str | None, permiso: Permiso) -> bool:
    miembro = a_rol_de_empresa(rol)
    if miembro is None:
        return False
    return permiso in PERMISOS_POR_ROL.get(miembro, frozenset())
