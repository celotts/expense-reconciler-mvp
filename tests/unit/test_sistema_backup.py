from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.sistema import _borrar_archivo, descargar_backup
from app.models.user import UserModel


def test_borrar_archivo_elimina_archivo(tmp_path: Path):
    archivo = tmp_path / "test_backup.sql"
    archivo.write_text("BACKUP DATA")
    assert archivo.exists()

    _borrar_archivo(archivo)
    assert not archivo.exists()


def test_borrar_archivo_tolera_archivo_inexistente(tmp_path: Path):
    archivo = tmp_path / "inexistente.sql"
    assert not archivo.exists()
    # No debe levantar ninguna excepción
    _borrar_archivo(archivo)


@pytest.mark.asyncio
async def test_descargar_backup_rechaza_no_admin():
    usuario_comun = UserModel(perfil="digitalizador")
    bg = BackgroundTasks()

    with pytest.raises(HTTPException) as exc_info:
        await descargar_backup(usuario=usuario_comun, background_tasks=bg)

    assert exc_info.value.status_code == 403
    assert "Solo un administrador" in exc_info.value.detail


@pytest.mark.asyncio
async def test_descargar_backup_agenda_borrado(tmp_path: Path):
    admin = UserModel(perfil="admin")
    bg = BackgroundTasks()

    # Simular URL de PostgreSQL y ejecución exitosa de pg_dump
    fake_url = "postgresql+asyncpg://admin:pass@localhost:5432/expense_db"
    with patch("app.core.config.settings.DATABASE_URL", fake_url):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            resp = await descargar_backup(usuario=admin, background_tasks=bg)

    assert resp.status_code == 200
    assert resp.media_type == "application/sql"
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func == _borrar_archivo
