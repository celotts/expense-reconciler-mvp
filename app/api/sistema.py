"""Endpoints de sistema: backup, estado, version.

Son los que un usuario no tecnico necesita para mantener la app viva sin
llamar a soporte. Un contador que pierde sus comprobantes porque se fue la
luz no va a abrir una terminal ni a leer logs.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import FileResponse

from app.core.deps import UsuarioActual

router = APIRouter(tags=["Sistema"])


def _borrar_archivo(path: Path) -> None:
    """Elimina el archivo temporal del respaldo tras enviarlo."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


@router.get("/backup", summary="Descargar respaldo de la base de datos")
async def descargar_backup(
    usuario: UsuarioActual,
    background_tasks: BackgroundTasks,
) -> FileResponse:
    """Descarga un archivo SQL con toda la base de datos.

    ## POR QUE EXISTE

    El SAT exige conservar comprobantes 5 años. Si se pierde el disco, se
    pierden los comprobantes y la evidencia. Un contador que no tiene backup
    automatizado es un contador que va a perder datos.

    ## COMO FUNCIONA

    Ejecuta `pg_dump` sobre la base de datos y devuelve el archivo SQL.
    El usuario lo guarda donde quiera (USB, Dropbox, etc.).

    ## QUIEN PUEDE

    Solo usuarios con perfil `admin` o `develop`. Un digitalizador no necesita
    respaldar la base entera.
    """
    if usuario.perfil not in ("admin", "develop", "programador"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un administrador puede generar un respaldo.",
        )

    # Obtener la URL de la base de datos
    from app.core.config import settings
    db_url = settings.DATABASE_URL

    # Extraer host, puerto, usuario, password, dbname de la URL
    # Formato: postgresql+asyncpg://user:pass@host:port/dbname
    import re
    m = re.match(r"postgresql\+?asyncpg?://([^:]+):([^@]+)@([^:/]+):?(\d+)?/(.+)", db_url)
    if not m:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo parsear la URL de la base de datos.",
        )

    db_user, db_pass, db_host, db_port, db_name = m.groups()
    db_port = db_port or "5432"

    nombre = f"backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.sql"
    ruta = Path("/tmp") / nombre

    try:
        subprocess.run(
            [
                "pg_dump",
                "-h", db_host,
                "-p", db_port,
                "-U", db_user,
                "-d", db_name,
                "-f", str(ruta),
                "--no-owner",
                "--no-privileges",
            ],
            env={"PGPASSWORD": db_pass, "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin"},
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="El comando pg_dump no esta instalado. Instala las herramientas de cliente de PostgreSQL.",
        ) from exc
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al generar el respaldo: {e.stderr}",
        ) from e

    background_tasks.add_task(_borrar_archivo, ruta)

    return FileResponse(
        path=str(ruta),
        filename=nombre,
        media_type="application/sql",
    )