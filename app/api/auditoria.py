"""Endpoint para recibir eventos de auditoria del frontend.

Cada accion del usuario en la UI (navegar a una pantalla, ejecutar un
proceso, etc.) se registra aqui. Sirve para trazabilidad completa de quien
hizo que y cuando.
"""

from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Request, status, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from app.core.database import get_db
from app.core.deps import UsuarioActual
from app.models import Auditoria, UserModel
from app.core.database import AsyncSessionLocal

router = APIRouter(tags=["Auditoria"])


class EventoFrontend(BaseModel):
    accion: str = Field(..., min_length=1, max_length=80)
    modulo: str = Field(..., min_length=1, max_length=80)
    registro_afectado: str | None = None
    detalles: dict | None = None


@router.post("/evento", status_code=status.HTTP_204_NO_CONTENT)
async def registrar_evento_frontend(
    evento: EventoFrontend,
    usuario: UsuarioActual,
    request: Request,
) -> None:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    async with AsyncSessionLocal() as db:
        entrada = Auditoria(
            usuario_id=usuario.id if hasattr(usuario, "id") else None,
            rol=getattr(usuario, "perfil", None) or getattr(usuario, "rol", None),
            accion=evento.accion,
            modulo=evento.modulo,
            registro_afectado=evento.registro_afectado,
            detalles=__import__("json").dumps(
                {**(evento.detalles or {}), "ip": ip, "user_agent": ua},
                default=str,
            ),
            ip_inet=ip,
            fecha=datetime.now(timezone.utc),
        )
        db.add(entrada)
        await db.commit()

class AuditoriaListadoItem(BaseModel):
    id: int
    usuario_id: str | None = None
    rol: str | None = None
    accion: str
    modulo: str | None = None
    registro_afectado: str | None = None
    detalles: str | None = None
    ip_inet: str | None = None
    fecha: datetime | None = None
    usuario_email: str | None = None

class AuditoriaListaResponse(BaseModel):
    total: int
    items: list[AuditoriaListadoItem]

@router.get("", response_model=AuditoriaListaResponse)
async def listar_auditoria(
    usuario: UsuarioActual,
    accion: str | None = Query(None),
    modulo: str | None = Query(None),
    fecha_desde: datetime | None = Query(None),
    fecha_hasta: datetime | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> AuditoriaListaResponse:
    # Solo perfiles privilegiados ven la auditoria completa; los demas reciben 403.
    perfil = getattr(usuario, "perfil", None) or "digitalizador"
    privilegiados = {"admin", "develop", "qa", "programador"}
    if perfil not in privilegiados:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Sin permiso para ver la auditoría.")

    q = select(Auditoria).order_by(Auditoria.fecha.desc())
    cq = select(func.count()).select_from(Auditoria)
    if accion:
        q = q.where(Auditoria.accion == accion)
        cq = cq.where(Auditoria.accion == accion)
    if modulo:
        q = q.where(Auditoria.modulo == modulo)
        cq = cq.where(Auditoria.modulo == modulo)
    if fecha_desde:
        q = q.where(Auditoria.fecha >= fecha_desde)
        cq = cq.where(Auditoria.fecha >= fecha_desde)
    if fecha_hasta:
        q = q.where(Auditoria.fecha <= fecha_hasta)
        cq = cq.where(Auditoria.fecha <= fecha_hasta)

    total = (await AsyncSessionLocal().execute(cq)).scalar_one()
    rows = (await AsyncSessionLocal().execute(q.limit(limit).offset(offset))).scalars().all()

    items = []
    for r in rows:
        email = None
        if r.usuario_id is not None:
            u = (await AsyncSessionLocal().execute(
                select(UserModel).where(UserModel.id == r.usuario_id)
            )).scalar_one_or_none()
            if u is not None:
                email = u.email
        items.append(AuditoriaListadoItem(
            id=int(r.id),
            usuario_id=str(r.usuario_id) if r.usuario_id else None,
            usuario_email=email,
            rol=r.rol,
            accion=r.accion,
            modulo=r.modulo,
            registro_afectado=(str(r.registro_afectado) if r.registro_afectado else None),
            detalles=r.detalles,
            ip_inet=r.ip_inet,
            fecha=r.fecha,
        ))

    return AuditoriaListaResponse(total=int(total), items=items)
