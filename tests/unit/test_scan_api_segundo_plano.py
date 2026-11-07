"""`POST /scan` en segundo plano (202) y el mapeo de las listas nuevas.

El modo sincrono sigue siendo el de por omision: con fotos el escaneo tarda
minutos y nginx corta a los 300 s, asi que el front pide `segundo_plano=true`
y sigue el progreso por `GET /scan/runs`. Estos tests llaman al handler
directo (sin HTTP) con el `db` en `None`: en los dos caminos el `db` no se
toca cuando `company_id` es `None` y el trabajo esta burlado.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.responses import JSONResponse

from app.api.scans import escanear as escanear_handler
from app.schemas.scan import ScanRequest, ScanResponse
from app.services.scan_service import ResumenEscaneo


def _usuario():
    return SimpleNamespace(email="ana@test.local")


async def test_segundo_plano_responde_202_y_no_espera(monkeypatch):
    """Con `segundo_plano=true` se acepta de inmediato y el trabajo sigue."""
    from app.api import scans as scans_modulo

    llamadas: list[dict] = []

    async def _falso_trabajo(*args, **kwargs):
        llamadas.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(scans_modulo, "_escanear_en_segundo_plano", _falso_trabajo)

    respuesta = await escanear_handler(
        ScanRequest(segundo_plano=True), _usuario(), None
    )

    assert isinstance(respuesta, JSONResponse)
    assert respuesta.status_code == 202
    # La tarea se lanzo con `create_task`: hay que ceder el control para que
    # corra antes de afirmar que se llamo.
    await asyncio.sleep(0)
    assert len(llamadas) == 1
    assert llamadas[0]["kwargs"]["reprocesar"] is False


async def test_sin_segundo_plano_sigue_siendo_sincrono(monkeypatch):
    """Sin el flag, el comportamiento no cambia: se espera el resultado."""
    from app.services import scan_service

    async def _falso_escanear(*_args, **_kwargs):
        return ResumenEscaneo(carpeta="/tmp", archivos_vistos=0)

    monkeypatch.setattr(scan_service, "escanear", _falso_escanear)

    respuesta = await escanear_handler(ScanRequest(), _usuario(), None)

    assert isinstance(respuesta, ScanResponse)
    assert respuesta.archivos_vistos == 0


async def test_el_sincrono_mapea_duplicados_y_fotos(monkeypatch):
    """Las dos listas nuevas llegan al `ScanResponse` sin romperse."""
    from app.services import scan_service

    async def _falso_escanear(*_args, **_kwargs):
        resumen = ResumenEscaneo(carpeta="/tmp", archivos_vistos=2, nuevos=1)
        resumen.duplicados_por_contenido = [
            {
                "archivo": "b.pdf",
                "proveedor": "Tiendas Ramirez SA de CV",
                "rfc": "TRAM910101XXX",
                "total": "1100.00",
                "fecha": "2025-03-15",
                "fecha_provisional": False,
                "folio": None,
                "ticket_id": "00000000-0000-0000-0000-000000000000",
                "regla": "proveedor+total+fecha",
                "tipo": "contenido",
            }
        ]
        resumen.fotos_por_revisar = [
            {
                "archivo": "c.png",
                "motivo": "foto borrosa, vuelve a tomarla con más luz y sin reflejo",
                "motivos": ["borrosa"],
            }
        ]
        return resumen

    monkeypatch.setattr(scan_service, "escanear", _falso_escanear)

    respuesta = await escanear_handler(ScanRequest(), _usuario(), None)

    assert isinstance(respuesta, ScanResponse)
    assert len(respuesta.duplicados_por_contenido) == 1
    assert respuesta.duplicados_por_contenido[0]["proveedor"] == (
        "Tiendas Ramirez SA de CV"
    )
    assert respuesta.duplicados_por_contenido[0]["total"] == "1100.00"
    assert len(respuesta.fotos_por_revisar) == 1
    assert "borrosa" in (respuesta.fotos_por_revisar[0]["motivo"] or "")
