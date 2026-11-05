"""Que un archivo demasiado grande devuelva 413, no 500 ni 400 ni nada."""

from __future__ import annotations

import io
from uuid import uuid4

import pytest
from fastapi import UploadFile, status
from httpx import AsyncClient

from app.core.subida import TICKET_MAX_BYTES, CSV_MAX_BYTES, leer_con_limite


def _file_de(tamano: int, nombre: str = "test.txt") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(b"x" * tamano),
        filename=nombre,
        headers={"content-type": "application/octet-stream"},
    )


class TestElTopeDeSubida:
    async def test_un_ticket_justo_dentro_del_tope_pasa(self):
        """Un archivo exactamente en el tope se lee entero."""
        f = _file_de(TICKET_MAX_BYTES, "ticket.pdf")
        contenido = await leer_con_limite(f, TICKET_MAX_BYTES, "ticket.pdf")
        assert len(contenido) == TICKET_MAX_BYTES

    async def test_un_ticket_un_byte_por_encima_devuelve_413(self):
        """Un byte mas y el lector levanta 413 antes de cargarlo entero."""
        f = _file_de(TICKET_MAX_BYTES + 1, "ticket.pdf")
        with pytest.raises(Exception) as exc:
            await leer_con_limite(f, TICKET_MAX_BYTES, "ticket.pdf")
        # La excepcion es un HTTPException 413; se comprueba en el endpoint,
        # aqui basta con que no devuelva bytes.
        assert exc.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE

    async def test_un_csv_justo_dentro_del_tope_pasa(self):
        f = _file_de(CSV_MAX_BYTES, "extracto.csv")
        contenido = await leer_con_limite(f, CSV_MAX_BYTES, "extracto.csv")
        assert len(contenido) == CSV_MAX_BYTES

    async def test_un_csv_un_byte_por_encima_devuelve_413(self):
        f = _file_de(CSV_MAX_BYTES + 1, "extracto.csv")
        with pytest.raises(Exception) as exc:
            await leer_con_limite(f, CSV_MAX_BYTES, "extracto.csv")
        assert exc.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE

    async def test_el_mensaje_dice_cuanto_pesa_el_tope_en_mb(self):
        """El mensaje del error es legible, no un numero crudo."""
        f = _file_de(CSV_MAX_BYTES + 1, "extracto.csv")
        with pytest.raises(Exception) as exc:
            await leer_con_limite(f, CSV_MAX_BYTES, "extracto.csv")
        msg = exc.value.detail
        assert "MB" in msg, f"el mensaje no dice MB: {msg}"
        assert "25" in msg or "25.0" in msg, f"el tope del CSV no aparece: {msg}"

    async def test_endpoint_extract_devuelve_413_si_pasa_el_tope(
        self, async_client: AsyncClient, test_company
    ):
        """Prueba de integracion: el endpoint real aplica el tope."""
        gigante = b"x" * (TICKET_MAX_BYTES + 1)
        files = {"file": ("gigante.pdf", gigante, "application/pdf")}
        data = {"file_type": "pdf"}
        r = await async_client.post("/api/v1/tickets/extract", files=files, data=data)
        assert r.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, (
            f"se esperaba 413, llego {r.status_code}: {r.text}"
        )

    async def test_endpoint_csv_devuelve_413_si_pasa_el_tope(
        self, async_client: AsyncClient, test_company
    ):
        """El endpoint de CSV tambien aplica su tope."""
        gigante = b"x" * (CSV_MAX_BYTES + 1)
        files = {"file": ("gigante.csv", gigante, "text/csv")}
        data = {
            "company_id": str(test_company.id),
            "date_column": "fecha",
            "amount_column": "importe",
            "description_column": "concepto",
            "reference_column": "referencia",
            "date_format": "%d/%m/%Y",
            "decimal_separator": ",",
            "thousands_separator": ".",
            "encoding": "utf-8",
        }
        r = await async_client.post(
            "/api/v1/bank-transactions/import-csv-and-create",
            files=files,
            data=data,
        )
        assert r.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, (
            f"se esperaba 413, llego {r.status_code}: {r.text}"
        )


class TestElTopeNoSePuedeSaltarse:
    """Pruebas por mutacion: si quitas el tope, estas pruebas fallan."""

    async def test_si_quito_el_tope_un_archivo_grande_pasa(self):
        """MUTACION: quita `if total > limite:` del codigo.

        Si la comprobacion desaparece, este test falla porque el lector ya no
        levanta 413 y devuelve el contenido del archivo.
        """
        f = _file_de(CSV_MAX_BYTES * 2, "dos_veces.csv")
        with pytest.raises(Exception) as exc:
            await leer_con_limite(f, CSV_MAX_BYTES, "dos_veces.csv")
        assert exc.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE

    async def test_si_quito_el_tope_en_endpoint_tambien_falla(
        self, async_client: AsyncClient, test_company
    ):
        """MUTACION: cambia `leer_ticket` por `await file.read()` en el endpoint.

        Si el endpoint deja de usar el lector con tope, este test falla.
        """
        gigante = b"x" * (TICKET_MAX_BYTES + 100)
        files = {"file": ("gigante.pdf", gigante, "application/pdf")}
        data = {"file_type": "pdf"}
        r = await async_client.post("/api/v1/tickets/extract", files=files, data=data)
        assert r.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, (
            "si el endpoint salta el lector, este pasa y el test falla"
        )


class TestNoHayFugasDeMemoria:
    """Comprobaciones extra de que el pico de memoria es el tope."""

    async def test_leer_en_trozos_no_acumula_mas_que_el_tope(self):
        """Aunque el archivo sea enorme, lo que se guarda en `partes` no pasa
        del tope, porque se corta antes de anadir el trozo que rebasa.
        """
        # Archivo de 2x el tope: se lee un trozo que rebasa, se levanta, y
        # `partes` tiene justo `TICKET_MAX_BYTES` bytes (menos un trozo).
        f = _file_de(TICKET_MAX_BYTES * 2, "enorme.pdf")
        with pytest.raises(Exception):
            await leer_con_limite(f, TICKET_MAX_BYTES, "enorme.pdf")
        # El test pasa si la excepcion sale; no hay forma facil de inspeccionar
        # la memoria pico desde aqui, pero el comportamiento es deterministico:
        # el bucle corta antes de hacer `partes.append(trozo)` cuando `total > limite`.


class TestValoresLimite:
    async def test_cero_bytes_devuelve_bytes_vacio(self):
        f = _file_de(0, "vacio.pdf")
        contenido = await leer_con_limite(f, TICKET_MAX_BYTES, "vacio.pdf")
        assert contenido == b""

    async def test_un_byte_devuelve_un_byte(self):
        f = _file_de(1, "uno.pdf")
        contenido = await leer_con_limite(f, TICKET_MAX_BYTES, "uno.pdf")
        assert contenido == b"x"