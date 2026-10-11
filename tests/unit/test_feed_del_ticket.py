"""El feed en vivo tiene que pintar los DATOS del ticket, no solo el importe.

POR QUE ESTE ARCHIVO EXISTE
===========================

`POST /scan` es sincrono y con fotos tarda minutos. `scan_registry` publica el
avance para que el operador no este mirando un "cargando" sin saber mas, y el
feed que publico durante mucho tiempo llevaba **cinco campos**: ruta, importe,
estado, motor y accion.

Con eso, una fila del panel decia:

    [revisar]  IMG_4222.jpeg                                   $0.00

Y eso **no distingue dos situaciones que piden acciones opuestas**:

  - `total 0.00` con `total_not_positive` → el sistema **no supo leer** el
    papel. Corregir exige volver a la foto.
  - `total 119.97` con `subtotal_plus_tax_mismatch` → el sistema **si leyo**
    algo, pero el gate vio que los numeros no cuadran. Ahi el trabajo es
    comparar dos numeros, no volver a leer la foto.

Con solo el total, las dos se ven como "$X, pendiente". El operador no sabe cual
es, y por lo tanto no sabe que hacer. Ese es el defecto: no faltaba un dato
bonito, faltaba el dato que convierte una fila en una tarea.

LO QUE ESTA FIJADO AQUI
=======================

1. El feed lleva el encabezado completo del ticket, leido de la FILA.
2. Los motivos del gate viajan y no se pierden.
3. El conteo de lineas es un conteo, no la lista: 60 archivos x 28 partidas son
   1 700 objetos en memoria para pintar un numero.
4. `Decimal` y `date` salen como texto: el feed se serializa con `json.dumps` y
   un `Decimal` crudo revienta ahi, en el SERIALIZADO, que es donde no se ve que
   el problema era el feed sino la respuesta entera.
5. `_registrar_en_el_feed` los pasa. Si el caller deja de pasarlos, el feed
   vuelve a estar vacio aunque el registro los soporte: por eso el test del
   servicio tambien esta, y no solo el del registro.

El ultimo punto es el que hace util este archivo. Un modulo que "tiene los
campos" y un caller que "los pasa" son dos verdades, y basta que la segunda se
desincronice para que la pantalla vuelva a estar muda sin que nada falle.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services import scan_registry


# ---------------------------------------------------------------------------
# El registro: lo que guarda
# ---------------------------------------------------------------------------


class TestElFeedGuardaElEncabezado:
    """`registrar_procesado` tiene que poder llevar los datos del ticket."""

    def _registrar(self, **extra):
        corrida = scan_registry.abrir_corrida("/tickets", "tester", False)
        # `extraction_status` va en `extra` y no fijo, para que un test pueda
        # fijarlo; el default es el de un ticket que el gate no aprobo.
        extra.setdefault("extraction_status", "PENDIENTE")
        scan_registry.registrar_procesado(
            corrida,
            relative_path="IMG_4222.jpeg",
            monto=Decimal("48.00"),
            **extra,
        )
        return corrida.procesados[0]

    def test_lleva_proveedor_rfc_y_fecha(self):
        entrada = self._registrar(
            proveedor="MERCASTAR",
            rfc="MLP950101ABC",
            fecha=date(2026, 10, 1),
        )
        assert entrada["proveedor"] == "MERCASTAR"
        assert entrada["rfc"] == "MLP950101ABC"
        assert entrada["fecha"] == date(2026, 10, 1)

    def test_lleva_el_resto_del_encabezado(self):
        entrada = self._registrar(
            proveedor="NUEVA WAL MART DE MEXICO S DE RL DE CV",
            subtotal=Decimal("217.27"),
            iva=Decimal("8.14"),
            ieps=Decimal("8.59"),
            lineas=28,
        )
        assert entrada["subtotal"] == Decimal("217.27")
        assert entrada["iva"] == Decimal("8.14")
        assert entrada["ieps"] == Decimal("8.59")
        assert entrada["lineas"] == 28

    def test_lleva_los_motivos_del_gate(self):
        """Sin esto, el feed dice "pendiente" y no por que.

        Es el campo que convierte una fila en una tarea. `date_missing` dice
        "volver a mirar la fecha"; `total_not_positive` dice "el papel no se
        pudo leer". Son trabajos distintos y el operador no puede distinguirlos
        sin el motivo.
        """
        entrada = self._registrar(
            extraction_status="REQUIERE_REVISION",
            validation_errors="subtotal_plus_tax_mismatch(leido=234.00,esperado=217.27); date_missing",
        )
        assert "subtotal_plus_tax_mismatch" in entrada["validation_errors"]
        assert "date_missing" in entrada["validation_errors"]

    def test_las_lineas_son_un_conteo_y_no_la_lista(self):
        """El techo de memoria del feed es real.

        `MAX_PROCESADOS` es 60. Un ticket de supermercado trae 28 partidas, asi
        que copiar `items` entero son 1 700 objetos guardados para pintar un
        numero. Quien quiera las lineas las pide en el ticket.
        """
        entrada = self._registrar(lineas=28)
        assert isinstance(entrada["lineas"], int)
        assert not isinstance(entrada["lineas"], list)

    def test_sin_lineas_es_none_y_no_cero(self):
        """`None` y `0` son afirmaciones distintas.

        La ruta OCR nunca trae detalle de partidas: `tickets.items` queda a
        `NULL` y `registrar_compra` devuelve `None` sin error, que es el caso
        normal. Pintar `0 líneas` afirmaria que el papel no trae partidas, que
        es otra cosa —y con el OCR al 60% seria una afirmacion falsa—.
        """
        entrada = self._registrar(lineas=None)
        assert entrada["lineas"] is None

    def test_los_campos_nuevos_son_opcionales(self):
        """La firma no se rompe para quien no los pasa.

        Un `ACTUALIZADO` o un duplicado pueden no tener nada que decir del
        encabezado, y una firma que los exige seria una razon para no llamar.
        """
        corrida = scan_registry.abrir_corrida("/tickets", "tester", False)
        scan_registry.registrar_procesado(
            corrida, relative_path="x.pdf", monto=Decimal("1.00")
        )
        entrada = corrida.procesados[0]
        assert entrada["proveedor"] is None
        assert entrada["lineas"] is None
        assert entrada["validation_errors"] is None


class TestElFeedSeSerializa:
    """`a_dict()` es lo que sale por la API, y ahi es donde un `Decimal` revienta."""

    def test_los_importes_y_la_fecha_salen_como_texto(self):
        corrida = scan_registry.abrir_corrida("/tickets", "tester", False)
        scan_registry.registrar_procesado(
            corrida,
            relative_path="walmart.jpg",
            monto=Decimal("234.00"),
            proveedor="NUEVA WAL MART",
            fecha=date(2026, 9, 26),
            subtotal=Decimal("217.27"),
            iva=Decimal("8.14"),
            lineas=28,
        )
        salida = corrida.a_dict()
        entrada = salida["procesados"][0]

        # Texto y no numero: es dinero, y `json.dumps` no sabe serializar Decimal.
        assert entrada["monto"] == "234.00"
        assert entrada["subtotal"] == "217.27"
        assert entrada["iva"] == "8.14"
        # La fecha sale en ISO, que es lo que el front ya espera de todos los
        # demas `expense_date` de la API.
        assert entrada["fecha"] == "2026-09-26"
        assert isinstance(entrada["lineas"], int)

    def test_a_dict_no_revienta_con_la_fecha(self):
        """El fallo que este test previene es de SERIALIZADO, no de lectura.

        Sin `_json_safe` tocando `date`, `a_dict()` revienta con `TypeError` al
        construir el dict —y el operador ve la pantalla de error del escaneo en
        vez de ver el progreso, que es justo lo que el feed existe para evitar.
        """
        import json

        corrida = scan_registry.abrir_corrida("/tickets", "tester", False)
        scan_registry.registrar_procesado(
            corrida, relative_path="x.jpg", fecha=date(2026, 10, 1)
        )
        # Si esto revienta, el feed esta roto aunque la pantalla no se queje.
        json.dumps(corrida.a_dict())


class TestElTechoNoSeRompio:
    """`MAX_PROCESADOS` es lo que hace el feed acotado. No se puede relajar.

    EL 60 VA ESCRITO A MANO, y no como `scan_registry.MAX_PROCESADOS`, por una
    razon concreta: la primera version de este test comparaba contra la
    constante del modulo. Mutar `MAX_PROCESADOS = 60` a `100000` —que es
    exactamente el cambio que rompe el techo de memoria— dejaba el test en
    verde, porque las dos partes de la comparacion se movian juntas.

    Un test que se valida a si mismo no es un test. Aqui el numero es la
    especificacion, y la constante del modulo es lo que se comprueba contra ella.
    """

    TOPO_ESPERADO = 60

    def test_el_techo_es_el_declarado(self):
        assert scan_registry.MAX_PROCESADOS == self.TOPO_ESPERADO

    def test_el_techo_sigue_aplicandose(self):
        corrida = scan_registry.abrir_corrida("/tickets", "tester", False)
        for i in range(self.TOPO_ESPERADO + 25):
            scan_registry.registrar_procesado(
                corrida,
                relative_path=f"f{i}.jpg",
                monto=Decimal("1.00"),
                proveedor=f"PROVEEDOR {i}",
            )
        assert len(corrida.procesados) == self.TOPO_ESPERADO
        # Y lo que se conserva son los ULTIMOS, no los primeros: el operador
        # quiere ver donde va, no donde empezo hace diez minutos.
        assert corrida.procesados[-1]["relative_path"] == (
            f"f{self.TOPO_ESPERADO + 24}.jpg"
        )


# ---------------------------------------------------------------------------
# El caller: que los pase de verdad
# ---------------------------------------------------------------------------


class TestElEscanerLosPasa:
    """El registro los soporta; falta que el ESCANER se los pase.

    Es el punto donde el feed se puede volver a quedar mudo sin que nada falle:
    `registrar_procesado` tiene los parametros, y si `_registrar_en_el_feed` no
    se los pasa, todas las llamadas son validas y la pantalla sale con las
    columnas en blanco. Por eso el test del modulo solo no alcanza.
    """

    @pytest.fixture
    def ticket_falso(self):
        """Un ticket con los campos que el feed lee de la fila.

        No se importa `TicketModel` a proposito: lo que se prueba es que el
        servicio lee estos ATRIBUTOS, y un objeto que los tiene de verdad
        demuestra eso sin necesitar una base.
        """

        class _Ticket:
            total_amount = Decimal("97.56")
            extraction_status = "PENDIENTE"
            provider_name = "CARNE MART"
            provider_tax_id = "OCO030116UR4"
            expense_date = date(2026, 9, 28)
            subtotal = Decimal("97.56")
            tax_amount = Decimal("0.00")
            ieps_amount = None
            items = None
            validation_errors = "date_missing"

        return _Ticket()

    def _correr(self, ticket, accion="CREADO", origen=None,
                 modelo_ia=None, proveedor_ia=None):
        """Ejecuta `_registrar_en_el_feed` con la sesion y el detalle falsos."""
        import uuid
        from types import SimpleNamespace

        from app.services.scan_service import _registrar_en_el_feed

        # `SimpleNamespace` y no una `class` local: el cuerpo de una clase no
        # captura el cierre, asi que `accion = accion` dentro de una `class`
        # busca el nombre en el ambito de la clase y no en el de la funcion, y
        # falla con `NameError`.
        detalle = SimpleNamespace(
            relative_path="AA4D0E8F.JPG",
            ticket_id=uuid.UUID(int=1),
            accion=accion,
            origen=origen,
            # `modelo_ia` y `proveedor_ia` no son opcionales en `ResumenArchivo`,
            # asi que el doble los trae. `None` es el caso real de una foto leida
            # por OCR o por reglas: no hubo modelo que responder.
            modelo_ia=modelo_ia,
            proveedor_ia=proveedor_ia,
        )

        class _Sesion:
            async def execute(self, _query):
                class _R:
                    def scalar_one_or_none(self_inner):
                        return ticket

                return _R()

        corrida = scan_registry.abrir_corrida("/tickets", "tester", False)
        import asyncio

        asyncio.run(_registrar_en_el_feed(corrida, _Sesion(), detalle))
        return corrida.procesados[0] if corrida.procesados else None

    def test_pasa_el_encabezado_completo(self, ticket_falso):
        entrada = self._correr(ticket_falso)
        assert entrada is not None
        assert entrada["proveedor"] == "CARNE MART"
        assert entrada["rfc"] == "OCO030116UR4"
        assert entrada["fecha"] == date(2026, 9, 28)
        assert entrada["subtotal"] == Decimal("97.56")
        assert entrada["iva"] == Decimal("0.00")
        assert entrada["validation_errors"] == "date_missing"

    def test_cuenta_las_lineas_solo_si_hay_lista(self, ticket_falso):
        """`items` a NULL es la ruta OCR, y `None` no es `0`."""
        ticket_falso.items = None
        assert self._correr(ticket_falso)["lineas"] is None

        ticket_falso.items = [
            {"description": "MILANESA DE PECHU", "quantity": "1.028"},
            {"description": "OTRA", "quantity": "1"},
        ]
        assert self._correr(ticket_falso)["lineas"] == 2

    def test_sin_ticket_no_escribe_nada(self):
        """Un ERROR o un NO_SOPORTADO no tienen encabezado que pintar.

        Y no es un caso raro: de 8 comprobantes, los que no producen ticket son
        los duplicados y los ilegibles, y son justo los que el operador necesita
        ver rapido en la lista.

        ## POR QUE ESTE TEST CAMBIO DE ASERCION, Y NO POR CAPRICHO

        Antes afirmaba `corrida.procesados == []`, o sea: **un archivo sin ticket
        no se escribe en el feed**. Y su propio docstring de arriba dice lo
        contrario —que son "justo los que el operador necesita ver rapido"—, asi
        que el test y su justificacion se contradician desde que se escribio.

        MEDIDO el defecto en la pantalla: escaneando SIN empresa, el panel de
        Escaner mostraba al terminar

            Esaneo terminado / 9 archivos vistos en 3m 6s
            **0 leidos   0 con ticket**
            **Ningun archivo se leyo en esta corrida.**

        Tres minutos y medio de OCR reportados como que no paso nada, y el
        operador relance el escaneo sobre los mismos archivos. El contador de
        "leidos" contaba TICKETS, no lecturas.

        Ahora el feed registra **todo** archivo que paso por `procesar_archivo`,
        con o sin ticket. Sin ticket los importes y el encabezado van en `None`
        —que es la verdad— y el `accion` dice por que. El panel pinta "leido,
        sin ticket" en vez de inventar un estado de lectura.

        Lo que SIGUE valiendo, y por eso el test no se borro sino que se
        corrigio: **sin ticket no se consulta la base**. La sesion de este test
        revienta si alguien la toca, y con `ticket_id=None` no se toca: pedir el
        encabezado de una fila que no existe seria una consulta por archivo para
        devolver nada.
        """
        from types import SimpleNamespace

        from app.services.scan_service import _registrar_en_el_feed

        detalle = SimpleNamespace(
            relative_path="roto.pdf",
            ticket_id=None,
            accion="ERROR",
            origen=None,
            modelo_ia=None,
            proveedor_ia=None,
        )

        class _Sesion:
            async def execute(self, _query):
                raise AssertionError("no debe consultar si no hay ticket_id")

        corrida = scan_registry.abrir_corrida("/tickets", "tester", False)
        import asyncio

        asyncio.run(_registrar_en_el_feed(corrida, _Sesion(), detalle))

        # SI se escribe: el operador tiene que ver el archivo roto.
        assert len(corrida.procesados) == 1, (
            "un archivo sin ticket NO debe desaparecer del feed: se leyo, y "
            "medido en pantalla, omitirlo hacia que 3m36s de OCR se reportaran "
            "como 'Ningun archivo se leyo'"
        )
        fila = corrida.procesados[0]
        assert fila["relative_path"] == "roto.pdf"
        assert fila["accion"] == "ERROR"
        # Los importes en `None` y NO en cero: un 0.00 afirmaria que se leyo un
        # total de cero, que es una afirmacion que el dato no sostiene.
        assert fila["monto"] is None
        assert fila["ticket_id"] is None
        # La sesion `_Sesion` revienta si se toca, y no se toco: esa parte del
        # test sigue probando lo que probaba.