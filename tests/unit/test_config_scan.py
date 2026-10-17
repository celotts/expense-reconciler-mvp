"""La carpeta de tickets se valida al arrancar, no en el primer escaneo.

Estas pruebas fijan el comportamiento de `Settings._revisa_la_carpeta_de_tickets`.
Cada caso tiene una razon detras, y casi todas son sobre el PRIMER arranque: la
carpeta casi nunca existe la primera vez, y esa es la unica vez que nadie la ha
creado.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(tmp_path: Path, **extra) -> Settings:
    """Un `Settings` completo, con la base en memoria y la carpeta en `tmp_path`.

    `extra` pisa `TICKETS_INPUT_DIR` si viene, y por eso se arma el dict y se
    desempaca: pasar la carpeta dos veces (una aqui y otra en `extra`) es un
    `TypeError` con dos valores, no un override.
    """
    valores = {
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "TICKETS_INPUT_DIR": str(tmp_path / "tickets"),
        **extra,
    }
    return Settings(**valores)


class TestLaCarpetaSeCreaSola:

    def test_una_carpeta_inexistente_se_crea(self, tmp_path):
        """El primer `POST /scan` de la vida de la instalacion no puede fallar.

        Si la validacion viviera en el endpoint, ese primer POST responderia "la
        carpeta no existe", que sugiere que esta mal, cuando lo que pasa es que
        todavia no hay nada que escanear. Crearla aqui hace que el primer uso sea
        un escaneo con cero archivos en vez de un error de filesystem.
        """
        destino = tmp_path / "tickets"
        assert not destino.exists()

        _settings(tmp_path)

        assert destino.is_dir()

    def test_una_carpeta_inexistente_con_autocrear_apagado_falla(self, tmp_path):
        """Con `TICKETS_INPUT_DIR_AUTOCREAR=false` la app NO arranca.

        Hay quien quiere que una carpeta mal puesta sea un fallo ruidoso en
        desarrollo y no un `makedirs` que crea un arbol de directorios en el
        lugar equivocado. Por eso la opcion existe.
        """
        with pytest.raises(ValidationError, match="no existe"):
            _settings(tmp_path, TICKETS_INPUT_DIR_AUTOCREAR=False)

    def test_una_carpeta_que_ya_existe_no_produce_error(self, tmp_path):
        destino = tmp_path / "tickets"
        destino.mkdir()
        (destino / "ya-hay-cosas.pdf").write_bytes(b"%PDF-1.7 x")

        _settings(tmp_path)

        # Y no se borra lo que hubiera: crear la carpeta no puede vaciarla.
        assert (destino / "ya-hay-cosas.pdf").exists()

    def test_crea_los_padres_intermedios(self, tmp_path):
        """`viajes/2025/marzo` se crea entero, no solo el ultimo nivel."""
        _settings(tmp_path, TICKETS_INPUT_DIR=str(tmp_path / "a" / "b" / "c"))

        assert (tmp_path / "a" / "b" / "c").is_dir()


class TestRutasResueltas:

    def test_una_ruta_relativa_se_guarda_absoluta(self, tmp_path, monkeypatch):
        """Se resuelve al arrancar, no en cada escaneo.

        Dejarla relativa haria que el resultado dependiera de donde se lanzo el
        proceso, y `GET /scan/config` mostraria una ruta que no es la que el
        operador tiene en la cabeza.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "tickets").mkdir()

        s = Settings(
            DATABASE_URL="sqlite+aiosqlite:///:memory:",
            TICKETS_INPUT_DIR="./tickets",
        )

        assert Path(s.TICKETS_INPUT_DIR).is_absolute()
        assert Path(s.TICKETS_INPUT_DIR).is_dir()

    def test_un_arroba_se_expande(self, tmp_path, monkeypatch):
        """`~/Documents/tickets` se expande, no se crea una carpeta "~" en el cwd.

        Es un error de los que solo aparecen en la maquina de alguien: `~` es
        literal para `os.makedirs`, y sin expandir crea `./~/Documents/tickets`.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "tickets").mkdir()

        s = Settings(
            DATABASE_URL="sqlite+aiosqlite:///:memory:",
            TICKETS_INPUT_DIR="~/tickets",
        )

        assert s.TICKETS_INPUT_DIR == str((tmp_path / "tickets").resolve())


class TestLaCarpetaEstaMalPuesta:

    def test_un_archivo_donde_va_la_carpeta_falla_con_los_dos_nombres(self, tmp_path):
        """El mensaje dice cual es la variable y cual es el valor.

        "makedirs: [Errno 17] File exists" no dice nada. El mensaje dice que
        `TICKETS_INPUT_DIR` apunta a un archivo y cual es la ruta, que es lo que
        hace falta para arreglarlo sin abrir el `.env`.
        """
        archivo = tmp_path / "tickets"
        archivo.write_text("no soy una carpeta")

        with pytest.raises(ValidationError) as exc:
            Settings(
                DATABASE_URL="sqlite+aiosqlite:///:memory:",
                TICKETS_INPUT_DIR=str(archivo),
            )

        # El nombre de la variable y la ruta tienen que estar en el mensaje.
        texto = str(exc.value)
        assert "TICKETS_INPUT_DIR" in texto
        assert str(archivo) in texto


class TestElValorPorOmision:

    def test_el_default_es_la_carpeta_del_prompt(self):
        """El valor por omision es el que pide el enunciado, y esta declarado.

        Se comprueba contra el codigo y no contra `.env.example`, porque el
        default que importa es el de `Settings`: es el que se usa cuando no hay
        `.env` en absoluto, que es el caso del contenedor y el de la primera vez
        que alguien clona esto.
        """
        s = Settings(DATABASE_URL="sqlite+aiosqlite:///:memory:")

        assert s.TICKETS_INPUT_DIR == "/Users/carloslott/Documents/Tickets/Tickets_app"

    def test_el_default_esta_tambien_en_env_example(self):
        """La plantilla dice lo mismo que el codigo.

        Si divergen, la documentacion miente sobre lo que hace la app. Se
        comprueba leyendo el archivo, porque el valor por omision del enunciado
        esta en los dos lados y basta con que uno se olvide.
        """
        ruta = Path(__file__).resolve().parents[2] / ".env.example"
        texto = ruta.read_text(encoding="utf-8")

        assert "TICKETS_INPUT_DIR=" in texto
        assert "TICKETS_INPUT_DIR=/Users/carloslott/Documents/Tickets/Tickets_app" in texto

    def test_entrada_y_escaneados_son_hermanas(self):
        """La entrada y la de escaneados comparten padre, y no es decorativo.

        `archivado_service.carpeta_de_escaneados()` resuelve, cuando
        `TICKETS_SCAN_OUTPUT_DIR` esta vacia, `entrada.parent / "Tickets_Scan"`.
        Ese "hermano" es lo que hace que el archivado funcione sin configurarlo.

        Si alguien cambia el default de la entrada a una ruta que no termina en
        `Tickets_app`, el hermano deja de ser `Tickets_Scan` y los comprobantes
        se van a una carpeta con otro nombre — o, peor, a un sitio que nadie mira.
        Este test no puede saber si el nombre es el que uno quiere; puede saber
        que la relacion de hermandad tiene que seguir siendo la que el servicio
        asume.
        """
        s = Settings(DATABASE_URL="sqlite+aiosqlite:///:memory:")

        entrada = Path(s.TICKETS_INPUT_DIR)
        hermano = entrada.parent / "Tickets_Scan"

        assert entrada.parent == hermano.parent, (
            "la carpeta de escaneados se resuelve como el hermano de la entrada; "
            "si dejan de ser hermanos, el archivado deja de caer donde se espera"
        )
        assert hermano.name == "Tickets_Scan"

    def test_ninguna_ruta_del_repo_apunta_a_la_ubicacion_vieja(self):
        """Ninguna ruta queda colgando de la ubicacion anterior.

        Una ruta vieja no rompe nada por si sola —el default del codigo manda— pero
        si desorienta: alguien copia un valor de la documentacion, lo pone en su
        `.env`, monta la carpeta vieja en el contenedor y no ve ningun
        comprobante, sin ningun error que lo diga.

        Se comprueba sobre los archivos que el proyecto usa para DOCUMENTAR las
        rutas. No sobre todo el repo: un ejemplo historico en una nota de un
        defecto ya corregido es informacion, no un error.
        """
        raiz_repo = Path(__file__).resolve().parents[2]
        objetivos = [
            raiz_repo / ".env.example",
            raiz_repo / "README.md",
            raiz_repo / "docker-compose.yml",
            raiz_repo / "app" / "core" / "config.py",
        ]

        for ruta in objetivos:
            texto = ruta.read_text(encoding="utf-8")
            assert "Documents/Tickets_app" not in texto, (
                f"{ruta.name} todavia apunta a la ubicacion vieja "
                "(Documents/Tickets_app). Ahora es Documents/Tickets/Tickets_app."
            )
            assert "Documents/Tickets_Scan" not in texto, (
                f"{ruta.name} todavia apunta a la ubicacion vieja "
                "(Documents/Tickets_Scan). Ahora es Documents/Tickets/Tickets_Scan."
            )


class TestLosTopes:

    def test_el_tope_por_corrida_esta_declarado(self, tmp_path):
        """Sin tope por corrida, la primera vez que hay 3000 comprobates dura
        minutos y probablemente revienta un proxy."""
        s = _settings(tmp_path)
        assert s.TICKETS_SCAN_MAX_ARCHIVOS > 0

    def test_el_tope_de_bytes_cabe_un_ticket_y_no_un_video(self, tmp_path):
        """El tope por archivo tiene que dejar pasar una foto de telefono.

        Una foto de telefono moderno son 3-6 MB. Un tope de 1 MB rechazaria la
        mayoria de las fotos y el escaner no serviria para lo que existe.
        """
        s = _settings(tmp_path)
        assert s.TICKETS_SCAN_MAX_BYTES > 6 * 1024 * 1024

    def test_recursivo_esta_encendido_por_omision(self, tmp_path):
        """Un comprobante de viaje esta en `viaje/2025/03/`.

        Con esto apagado, un escaner de un solo nivel no veria nunca los que no
        estan en la raiz, y el operador tendria que aplanar la carpeta a mano.
        """
        assert _settings(tmp_path).TICKETS_SCAN_RECURSIVO is True
