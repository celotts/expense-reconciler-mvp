"""Membresias por empresa: la unica verdad sobre quien puede hacer que.

El router es un adaptador delgado; la decision de si se puede quitar el ultimo
administrador vive aqui, igual que el alcance del `operator` en caja chica. Un
`DELETE` que dejara la empresa sin administradores no es un error del cuerpo,
es un estado del sistema que no se permite: 409, con la explicacion.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RolEmpresa
from app.models.user import UserModel
from app.models.usuario_empresa import UsuarioEmpresaModel
from app.schemas.membresias import MembresiaCreate, MembresiaItemResponse, MembresiaUpdate
from app.services.auditoria import registrar_auditoria

ADMINISTRADORES = (RolEmpresa.OWNER, RolEmpresa.ADMIN)


def _es_administrador(rol: RolEmpresa | str) -> bool:
    return RolEmpresa(rol) in ADMINISTRADORES


async def _fila(
    db: AsyncSession, company_id: UUID, user_id: UUID
) -> UsuarioEmpresaModel:
    membresia = await db.get(UsuarioEmpresaModel, (user_id, company_id))
    if membresia is None:
        # 404 y no 403: un 403 confirmaria que esa cuenta existe en esa empresa.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esa cuenta no tiene membresia en la empresa activa",
        )
    return membresia


async def _no_quitar_ultimo_admin(
    db: AsyncSession,
    company_id: UUID,
    membresia: UsuarioEmpresaModel,
    rol_nuevo: RolEmpresa | str,
    activo_nuevo: bool,
) -> None:
    """El ultimo admin/owner de una empresa no se degrada ni se da de baja.

    Contar ADMINISTRADORES en vez de solo `admin` importa: con un `owner` y un
    `admin`, quitarle el rol al `admin` deja un `owner` que puede seguir
    administrando. Con un solo `admin`, quitarlo deja la empresa sin nadie que
    pueda dar acceso a los demas, y la unica via seria editsr la base a mano.
    """
    if _es_administrador(membresia.rol) and (
        not _es_administrador(rol_nuevo) or not activo_nuevo
    ):
        total = await db.scalar(
            select(func.count()).select_from(UsuarioEmpresaModel).where(
                UsuarioEmpresaModel.company_id == company_id,
                UsuarioEmpresaModel.activo.is_(True),
                UsuarioEmpresaModel.rol.in_([r.value for r in ADMINISTRADORES]),
            )
        )
        if (total or 0) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Esta es la ultima cuenta con rol de administrador en la "
                    "empresa. Asigne otro administrador antes de quitarle el acceso."
                ),
            )


def _item(m: UsuarioEmpresaModel, cuenta: UserModel) -> MembresiaItemResponse:
    return MembresiaItemResponse(
        company_id=m.company_id,
        user_id=m.user_id,
        email=cuenta.email,
        nombre=cuenta.nombre,
        rol=RolEmpresa(m.rol),
        activo=m.activo,
    )


async def listar_membresias(
    db: AsyncSession, company_id: UUID
) -> list[MembresiaItemResponse]:
    filas = (
        await db.execute(
            select(UsuarioEmpresaModel, UserModel)
            .join(UserModel, UserModel.id == UsuarioEmpresaModel.user_id)
            .where(UsuarioEmpresaModel.company_id == company_id)
            .order_by(UserModel.email)
        )
    ).all()
    return [_item(m, cuenta) for m, cuenta in filas]


async def asignar_membresia(
    db: AsyncSession,
    company_id: UUID,
    datos: MembresiaCreate,
    *,
    actor_id: UUID,
    actor_rol: RolEmpresa,
    ip: str,
) -> MembresiaItemResponse:
    cuenta = await db.get(UserModel, datos.user_id)
    if cuenta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="La cuenta no existe"
        )

    membresia = await db.get(UsuarioEmpresaModel, (datos.user_id, company_id))
    if membresia is not None and membresia.activo:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esa cuenta ya tiene un rol activo en esta empresa",
        )

    if membresia is None:
        membresia = UsuarioEmpresaModel(
            user_id=datos.user_id,
            company_id=company_id,
            rol=datos.rol.value,
            activo=datos.activo,
        )
        db.add(membresia)
        accion = "asignar_rol_empresa"
    else:
        # La fila inactiva se reutiliza: la PK compuesta es la identidad de la
        # relacion, y un INSERT con ON CONFLICT seria una segunda implementacion
        # de la misma decision en otro sitio.
        membresia.rol = datos.rol.value
        membresia.activo = datos.activo
        accion = "reactivar_rol_empresa"

    await db.commit()
    await db.refresh(membresia)
    await registrar_auditoria(
        accion=accion,
        modulo="usuarios",
        usuario_id=str(actor_id),
        registro_afectado=str(datos.user_id),
        detalles={
            "company_id": str(company_id),
            "rol": datos.rol.value,
            "activo": datos.activo,
            "email": cuenta.email,
        },
        rol=actor_rol.value,
        ip=ip,
        resultado="exito",
        db=db,
    )
    return _item(membresia, cuenta)


async def actualizar_membresia(
    db: AsyncSession,
    company_id: UUID,
    user_id: UUID,
    datos: MembresiaUpdate,
    *,
    actor_id: UUID,
    actor_rol: RolEmpresa,
    ip: str,
) -> MembresiaItemResponse:
    membresia = await _fila(db, company_id, user_id)
    cambios = datos.cambios
    if not cambios:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No mandaste ningún campo que se pueda cambiar",
        )

    rol_nuevo = cambios.get("rol", membresia.rol)
    activo_nuevo = cambios.get("activo", membresia.activo)
    await _no_quitar_ultimo_admin(db, company_id, membresia, rol_nuevo, activo_nuevo)

    cuenta = await db.get(UserModel, user_id)
    assert cuenta is not None  # la FK lo garantiza; la BD no crea huerfanos.

    valores_antes = {
        campo: getattr(membresia, campo) for campo in cambios
    }
    for campo, valor in cambios.items():
        setattr(membresia, campo, valor.value if hasattr(valor, "value") else valor)
    await db.commit()
    await db.refresh(membresia)

    await registrar_auditoria(
        accion="cambiar_rol_empresa",
        modulo="usuarios",
        usuario_id=str(actor_id),
        registro_afectado=str(user_id),
        detalles={
            "company_id": str(company_id),
            "email": cuenta.email,
            "anteriores": valores_antes,
            "nuevos": cambios,
        },
        rol=actor_rol.value,
        ip=ip,
        resultado="exito",
        db=db,
    )
    return _item(membresia, cuenta)
