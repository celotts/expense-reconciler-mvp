"""El informe de cierre mensual. Un router fino: la logica vive en el servicio.

Dos endpoints y no uno, y la razon es que el contador los usa en momentos
distintos: el JSON es el preview que revisa ANTES de firmar nada, y el PDF es lo
que se lleva. Con un solo endpoint habria que generar el PDF para mirarlo, y el
formato no se lee en pantalla.

**AMBOS SALEN DEL MISMO OBJETIVO.** No hay una version "para el PDF": el servicio
construye un `ReporteCierreMensual` y estas dos rutas lo consumen. Si el preview y
el documento se armaran por separado, el contador veria dos exactitudes distintas
en la misma pantalla y la promesa del producto se caeria ahi.

**Orden de declaracion.** `.pdf` va antes que el literal sin sufijo. No colisionan
hoy (`/cierre-mensual` y `/cierre-mensual.pdf` son cadenas distintas), pero este
repo ya rompio ese orden una vez y lo pago (`known-issues.md` §1): la regla es que
un literal se declara antes que cualquier cosa que lo pueda absorbing.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import UsuarioActual
from app.schemas.reporte import ReporteCierreMensual
from app.services import reporte_cierre
from app.services.reporte_cierre import PATRON_PERIODO, PeriodoInvalido

router = APIRouter()

CompanyId = Annotated[UUID, Query(description="Empresa del informe")]
Periodo = Annotated[
    str,
    Query(
        pattern=PATRON_PERIODO.pattern,
        description="Periodo del informe, AAAA-MM. Por ejemplo 2026-01.",
        examples=["2026-01"],
    ),
]


async def _reporte(
    db: AsyncSession, company_id: UUID, periodo: str, usuario
) -> ReporteCierreMensual:
    """Construye el informe o responde 404/422. El try va aqui y no en cada ruta.

    Dos errores distintos con dos codigos distintos, y no por completado:
    `2026-13` es una peticion mal formada y `empresa-inexistente` es que no hay
    nada que reportar. La empresa que no existe NO produce un informe vacio: un
    vacio se lee como "este mes no gastaste", y eso es una afirmacion distinta, y
    falsa.
    """
    try:
        return await reporte_cierre.construir_reporte(
            db,
            company_id=company_id,
            periodo=periodo,
            firmado_por=usuario.email,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La empresa indicada no existe",
        ) from exc
    except PeriodoInvalido as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/cierre-mensual.pdf", response_class=Response)
async def descargar_cierre_mensual(
    company_id: CompanyId,
    periodo: Periodo,
    db: Annotated[AsyncSession, Depends(get_db)],
    usuario: UsuarioActual,
) -> Response:
    """El PDF del cierre. Se declara ANTES del literal sin sufijo.

    `firmed_por` sale del token y nunca del cuerpo: si el nombre de quien firma se
    aceptara en la peticion, un informe podria salir firmado por quien no lo reviso
    (regla 23 de `AGENTS.md`).
    """
    reporte = await _reporte(db, company_id, periodo, usuario)
    contenido = reporte_cierre.generar_pdf(reporte)
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{reporte_cierre.nombre_del_pdf(reporte)}"'
            )
        },
    )


@router.get("/cierre-mensual", response_model=ReporteCierreMensual)
async def ver_cierre_mensual(
    company_id: CompanyId,
    periodo: Periodo,
    db: Annotated[AsyncSession, Depends(get_db)],
    usuario: UsuarioActual,
) -> ReporteCierreMensual:
    """El mismo informe en JSON, para el preview del front."""
    return await _reporte(db, company_id, periodo, usuario)