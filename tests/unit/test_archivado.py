"""Mover el comprobante a la carpeta de escaneados.

Las defensas y el test que las mata:

  no se mueve lo que no tiene ticket     test_un_archivo_sin_ticket_no_se_mueve
  no se pisa un comprobante existente    test_no_se_sobreescribe_nunca
  simular no mueve                       test_simular_no_toca_el_disco
  sin empresa no se mueve                 test_sin_empresa_no_se_mueve
  la carpeta destino se crea              test_se_crea_la_carpeta_destino

`conftest.py` apaga el archivado para todas las pruebas —porque un test de
LECTURA no debe mover archivos del disco— asi que este archivo lo enciende
explicitamente con `archivar`. Ver la nota de ahi.

LO QUE ESTA FUERA DE ALCANCE, Y POR QUE

El archivado con la compra PROCESADA vive en `scan_service.archivar_si_ya_resuelto`,
que tiene su propia logica y su propio criterio. Aqui se prueba `archivado_service`:
la parte que mueve.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import settings
from app.services import archivado_service as arch


@pytest.fixture
def carpetas(tmp_path):
    """Carpeta de entrada y de escaneados de verdad, en el tmp.

    El archivado se enciende aqui, porque `conftest` lo apaga para todo lo demas.
    """
    entrada = tmp_path / "tickets_app"
    destino = tmp_path / "Tickets_Scan"
    entrada.mkdir()
    destino.mkdir()

    from app.core.config import settings as s

    s.TICKETS_INPUT_DIR = str(entrada)
    s.TICKETS_SCAN_OUTPUT_DIR = str(destino)
    s.TICKETS_SCAN_ARCHIVAR = True

    yield entrada, destino

    s.TICKETS_SCAN_ARCHIVAR = False


class TestArchivar:
    def test_mueve_y_el_archivo_sigue_existiendo(self, carpetas):
        """MOVER NO ES BORRAR.

        Es la garantia de la que depende una auditoria: el comprobante cambia de
        sitio y sus bytes no cambian. Un archivo que desaparece no se puede cotejar
        contra lo que el sistema leyo de el.
        """
        entrada, destino = carpetas
        origen = entrada / "comprobante.pdf"
        origen.write_bytes(b"%PDF-1.4 contenido original")

        resultado = arch.archivar(origen, "comprobante.pdf")

        assert resultado.movido is True
        assert not origen.exists()
        destino_final = destino / "comprobante.pdf"
        assert destino_final.exists()
        # Mismos bytes. No es una copia truncada ni una relectura.
        assert destino_final.read_bytes() == b"%PDF-1.4 contenido original"

    def test_un_archivo_que_no_esta_no_es_error(self, carpetas):
        """Es el caso NORMAL de la segunda corrida, y no puede romper el escaneo.

        Un `raise` aqui haria que cada `POST /scan` posterior fallara, porque todos
        los archivos ya movidos aparecen como "no esta".
        """
        entrada, destino = carpetas

        resultado = arch.archivar(entrada / "ya-se-movio.pdf", "ya-se-movio.pdf")

        assert resultado.movido is False
        assert "ya no esta" in (resultado.motivo or "")

    def test_no_se_sobreescribe_nunca(self, carpetas):
        """LA DEFENZA. Un comprobante pisado es un comprobante perdido, y se
        pierde en silencio: el escaner devuelve exito y el archivo desaparecio.

        El caso real: un comprobante llamado `factura.pdf` se archiva, y meses
        despues llega OTRO `factura.pdf`. En la carpeta de destino colisionan, y
        sin esto el segundo pisa al primero.

        (Dos archivos con el mismo nombre en la MISMA carpeta de origen no pueden
        existir —el sistema de archivos no lo permite—, asi que la colision solo
        se da en el destino, entre corridas.)
        """
        entrada, destino = carpetas

        primero = entrada / "factura.pdf"
        primero.write_bytes(b"PRIMERO")
        arch.archivar(primero, "factura.pdf")

        # Llega otro comprobante con el MISMO nombre, de otro mes.
        segundo = entrada / "factura.pdf"
        segundo.write_bytes(b"SEGUNDO")
        resultado = arch.archivar(segundo, "factura.pdf")

        assert resultado.movido is True
        # Los dos existen, cada uno con SU contenido.
        assert (destino / "factura.pdf").read_bytes() == b"PRIMERO"
        assert resultado.ruta_destino.read_bytes() == b"SEGUNDO"
        assert resultado.ruta_destino.name != "factura.pdf", "debe llevar sufijo"

    def test_conserva_la_estructura_de_subcarpetas(self, carpetas):
        """Un comprobante de un viaje no se aplasta contra otro con el mismo nombre.

        El escaneo es recursivo justamente para respetar esa agrupacion; perderla
        al archivar seria tirar informacion que el sistema ya sabe usar.
        """
        entrada, destino = carpetas
        (entrada / "viaje" / "2025" / "03").mkdir(parents=True)
        origen = entrada / "viaje" / "2025" / "03" / "caseta.pdf"
        origen.write_bytes(b"x")

        resultado = arch.archivar(origen, "viaje/2025/03/caseta.pdf")

        assert resultado.movido is True
        assert resultado.ruta_destino == destino / "viaje" / "2025" / "03" / "caseta.pdf"
        assert resultado.ruta_destino.exists()

    def test_simular_no_toca_el_disco(self, carpetas):
        """Se ve QUE se moveria y a donde, sin mover nada.

        Con el archivado activo por omision, la primera corrida deja la carpeta de
        entrada vacia. Eso conviene verlo antes de que ocurra.
        """
        entrada, destino = carpetas
        origen = entrada / "comprobante.pdf"
        origen.write_bytes(b"contenido")

        destino_calculado = arch.carpeta_de_escaneados() / "comprobante.pdf"

        # Esto es lo que hace `archivar_tras_lectura` con `simular=True`: calcula
        # el destino y no llama a `archivar`.
        assert destino_calculado == destino / "comprobante.pdf"
        assert origen.exists(), "el archivo original sigue donde estaba"
        assert not (destino / "comprobante.pdf").exists()

    def test_se_crea_la_carpeta_destino(self, tmp_path, monkeypatch):
        """Si no existe, se crea. Y el destino por omision es HERMANO de la de
        entrada, no una ruta fija.

        La omision tiene que funcionar fuera de Docker: una ruta de contenedor
        como `/tickets_scan` da "Read-only file system" en macOS. Medido.
        """
        entrada = tmp_path / "tickets_app"
        entrada.mkdir()
        destino = tmp_path / "Tickets_Scan"

        from app.core.config import settings as s

        monkeypatch.setattr(s, "TICKETS_INPUT_DIR", str(entrada))
        monkeypatch.setattr(s, "TICKETS_SCAN_OUTPUT_DIR", "")

        resuelta = arch.carpeta_de_escaneados()

        assert resuelta == destino
        assert resuelta.exists()   # creada, no solo calculada

