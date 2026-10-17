"""Módulo de auditoría para registrar todas las operaciones del sistema.

Registra:
- Login/logout de usuarios
- Intento fallido de acceso
- Digitalización de comprobantes
- Inserciones, actualizaciones y deletes de datos
- Expiración de sesiones
- Cada operación va acompañada de: usuario, rol, acción, módulo, registro afectado y detalles
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Auditoria


async def registrar_auditoria(
    accion: str,
    modulo: str,
    registro_afectado: Optional[str] = None,
    detalles: Optional[Dict[str, Any]] = None,
    rol: Optional[str] = None,
) -> None:
    """Registra una operación en la tabla de auditoría."""
    try:
        async with AsyncSessionLocal() as session:
            ip_registro = None

            entrada = Auditoria(
                usuario_id=None,
                rol=rol,
                accion=accion,
                modulo=modulo,
                registro_afectado=registro_afectado,
                detalles=detalle_a_json(detalles),
                ip_inet=ip_registro,
                fecha=datetime.now(timezone.utc),
            )

            session.add(entrada)
            await session.commit()
    except Exception as e:
        print(f"Error al registrar auditoría: {e}")


def detalle_a_json(detalles: Optional[Dict[str, Any]]) -> Optional[str]:
    if detalles is None:
        return None
    import json
    try:
        return json.dumps(detalles, default=str)
    except (TypeError, ValueError):
        return str(detalles)


async def registrar_login_auditoria(usuario_id: str, rol: str, ip: Optional[str] = None) -> None:
    await registrar_auditoria(
        accion='login',
        modulo='auth',
        registro_afectado=None,
        detalles={'ip': ip, 'user_agent': None},
        rol=rol,
    )


async def registrar_logout_auditoria(usuario_id: str, rol: str, ip: Optional[str] = None) -> None:
    await registrar_auditoria(
        accion='logout',
        modulo='auth',
        registro_afectado=None,
        detalles={'ip': ip},
        rol=rol,
    )


async def registrar_intento_fallido_auditoria(
    modulo: str,
    accion: str,
    usuario_identificacion: str,
    rol: str,
    ip: Optional[str] = None,
    motivo: Optional[str] = None,
) -> None:
    await registrar_auditoria(
        accion=f'{accion}_fallido',
        modulo=modulo,
        detalles={'motivo': motivo, 'ip': ip, 'usuario': usuario_identificacion},
        rol=rol,
    )


async def registrar_digitalizacion_auditoria(
    ticket_id: str,
    usuario_id: str,
    rol: str,
    archivo: str,
    resultado: str,
    exitoso: bool = True,
) -> None:
    await registrar_auditoria(
        accion='digitalizar',
        modulo='scan',
        registro_afectado=ticket_id,
        detalles={
            'archivo': archivo,
            'resultado': resultado,
            'exitoso': exitoso,
            'usuario': usuario_id,
        },
        rol=rol,
    )


async def registrar_update_auditoria(
    modelo: str,
    registro_id: str,
    campos_modificados: list,
    usuario_id: str,
    rol: str,
) -> None:
    await registrar_auditoria(
        accion='update',
        modulo=modelo,
        registro_afectado=registro_id,
        detalles={'campos_modificados': campos_modificados, 'usuario': usuario_id},
        rol=rol,
    )


async def registrar_delete_auditoria(
    modelo: str,
    registro_id: str,
    usuario_id: str,
    rol: str,
) -> None:
    await registrar_auditoria(
        accion='delete',
        modulo=modelo,
        registro_afectado=registro_id,
        detalles={'usuario': usuario_id},
        rol=rol,
    )


async def registrar_error_sistema_auditoria(
    modulo: str,
    error: str,
    usuario_id: Optional[str] = None,
    ip: Optional[str] = None,
) -> None:
    await registrar_auditoria(
        accion='error_sistema',
        modulo=modulo,
        detalles={'error': error, 'ip': ip, 'usuario': usuario_id},
        rol=None,
    )


async def registrar_perfil_usuario(
    usuario_id: str,
    nuevo_perfil: str,
    usuario_operador: str,
) -> Dict[str, Any]:
    """Mantenedor de perfiles de usuario."""
    perfiles_validos = ['consultor', 'digitalizador', 'supervisor', 'admin', 'qa', 'programador', 'develop']

    if nuevo_perfil not in perfiles_validos:
        return {'exito': False, 'mensaje': f'Perfil inválido. Los perfiles válidos son: {", ".join(perfiles_validos)}'}

    try:
        async with AsyncSessionLocal() as session:
            from app.models import UserModel
            result = await session.execute(select(UserModel).where(UserModel.id == usuario_id))
            usuario = result.scalar_one_or_none()

            if not usuario:
                return {'exito': False, 'mensaje': 'Usuario no encontrado'}

            await registrar_auditoria(
                accion='cambiar_perfil',
                modulo='users',
                registro_afectado=usuario_id,
                detalles={'perfil_anterior': usuario.perfil, 'perfil_nuevo': nuevo_perfil},
                rol=usuario.perfil if hasattr(usuario, 'perfil') else None,
            )

            usuario.perfil = nuevo_perfil
            if hasattr(usuario, 'updated_at'):
                usuario.updated_at = datetime.now(timezone.utc)
            session.add(usuario)
            await session.commit()

            await registrar_auditoria(
                accion='perfil_actualizado',
                modulo='users',
                registro_afectado=usuario_id,
                detalles={'perfil_anterior': usuario.perfil if usuario else None, 'perfil_nuevo': nuevo_perfil},
                rol=usuario.perfil if hasattr(usuario, 'perfil') else None,
            )

            return {'exito': True, 'mensaje': f'Perfil cambiado de {usuario.perfil if usuario else "desconocido"} a {nuevo_perfil}'}
    except Exception as e:
        return {'exito': False, 'mensaje': f'Error al cambiar perfil: {str(e)}'}