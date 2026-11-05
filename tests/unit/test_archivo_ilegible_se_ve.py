"""Un archivo que no se puede leer tiene que aparecer por su nombre.

## EL CASO, MEDIDO EL 2026-10-16

Tres fotos en la carpeta, las tres dan `[Errno 5] Input/output error` al abrirlas
—el montaje de archivos de Docker Desktop habia quedado roto tras un reinicio—,
y el `POST /scan` devuelve:

    200 OK   {"archivos_vistos": 0, "nuevos": 0, ...}

Sin registro de corrida, sin fila en `scan_files`, sin evento, sin un numero, y
**sin una palabra en la pantalla**. El unico rastro era una linea de log:

    no se pudo leer IMG_4398.heic: [Errno 5] Input/output error

Quien escanea no abre logs. Y el mensaje que recibe no es "no hay archivos": es
"no hay archivos", que es justo lo que dice una carpeta vacia. **DosSituaciones
que piden acciones opuestas se ven igual desde fuera.**

## POR QUE ESTO NO ES "OTRO ERROR MAS"

El escaner ya sabia contar errores: `con_error` es para los que se abren y no se
entienden, `no_soportados` para los que estan bien pero en un formato que no se
lee, y `omitidos_por_tope` para los que se dejaran para la pasada siguiente. Un
archivo ilegible **no es ninguno de los tres**: no llega a existir para el
escaner, asi que no se volvera a mirar nunca, y de los tres solo
`omitidos_por_tope` promete un "vuelve a escanear" que aqui no se cumple.

Por eso va con nombre y motivo, y no como un numero: "3 ilegibles" deja igual
de amplio el abanico entre "la carpeta no esta montada" —un problema de
entorno— y "tres papeles dañados en el disco" —hay que ir por ellos.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import scan_service


@pytest.fixture
def con_carpeta(tmp_path, monkeypatch):
    """Una carpeta de entrada real, con el servicio apuntando a ella."""
    base = tmp_path / "entrada"
    base.mkdir()
    monkeypatch.setattr(scan_service.settings, "TICKETS_INPUT_DIR", str(base))
    return base


def test_un_archivo_que_no_se_puede_abrir_aparece_con_su_motivo(con_carpeta, monkeypatch):
    """LA DEFENSA. Sin esto, el archivo no aparece en NADA."""
    (con_carpeta / "bueno.pdf").write_bytes(b"%PDF-1.4 ok")
    (con_carpeta / "roto.pdf").write_bytes(b"%PDF-1.4 roto")

    real = Path.lstat

    def lstat_que_falla_en_uno(self, *a, **kw):
        if self.name == "roto.pdf":
            raise OSError(5, "Input/output error")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "lstat", lstat_que_falla_en_uno)

    ilegibles: list[dict] = []
    archivos = scan_service.listar_archivos(con_ilegibles=ilegibles)

    assert [v.relative_path for v in archivos] == ["bueno.pdf"], (
        "el archivo que si se puede leer sigue siendo candidato"
    )
    assert ilegibles == [{"ruta": "roto.pdf", "motivo": str(OSError(5, "Input/output error"))}], (
        "el que no se pudo abrir tiene que salir con su nombre y su motivo. Sin "
        "esto el POST /scan responde 200 con cero y no hay forma de distinguir "
        "'la carpeta esta vacia' de 'la carpeta esta ahi y no se puede abrir'."
    )


def test_una_carpeta_sin_problemas_no_reporta_ilegibles(con_carpeta):
    """El caso normal no cambia: cero ilegibles, y sin ruido en la respuesta."""
    (con_carpeta / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (con_carpeta / "b.pdf").write_bytes(b"%PDF-1.4 b")

    ilegibles: list[dict] = []
    archivos = scan_service.listar_archivos(con_ilegibles=ilegibles)

    assert len(archivos) == 2
    assert ilegibles == []


def test_sin_el_parametro_sigue_devolviendo_lo_de_siempre(con_carpeta, monkeypatch):
    """Los nueve llamadores que NO piden el dato no se enteran.

    Es la razon de que sea un parametro de salida y no un valor de retorno
    cambiado: la suite y el servicio siguen usando la funcion como antes, y el
    dato llega solo a quien lo pide.
    """
    (con_carpeta / "roto.pdf").write_bytes(b"%PDF-1.4 roto")

    real = Path.lstat

    def lstat_que_falla(self, *a, **kw):
        if self.name == "roto.pdf":
            raise OSError(5, "Input/output error")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "lstat", lstat_que_falla)

    assert scan_service.listar_archivos() == []


class TestLaRespuestaLoExpone:
    """El dato tiene que LLEGAR, no solo existir en el servicio.

    MEDIDO el 2026-10-16: que el servicio tenga el dato no sirve si
    el `POST /scan` no lo devuelve, porque quien escanea lee la respuesta, no el
    codigo.
    """

    def test_el_schema_lo_tiene(self):
        from app.schemas.scan import ScanResponse

        assert "ilegibles" in ScanResponse.model_fields, (
            "el campo que dice que archivos no se pudieron abrir tiene que "
            "estar en la respuesta del POST /scan"
        )

    def test_el_router_lo_pasa(self):
        """Que el router se lo pase al resumen, no solo que el campo exista.

        Un campo en el schema que el router nunca llena es un campo que siempre
        sale vacio: la defensa existe y no protege nada.
        """
        from pathlib import Path as _P

        fuente = _P(__file__).resolve().parents[2] / "app" / "api" / "scans.py"
        assert "ilegibles=resumen.ilegibles" in fuente.read_text(encoding="utf-8"), (
            "el router tiene que pasar `resumen.ilegibles` a la respuesta"
        )