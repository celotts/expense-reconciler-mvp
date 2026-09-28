"""El contrato entre el backend y el frontend no puede romperse en silencio.

Estos valores estan escritos en los dos lados, en dos lenguajes distintos, sin
que ningun compilador relacione ambos archivos. Nada falla si divergen: el
frontend simplemente deja de reconocer lo que el backend emite.

Que es exactamente lo que paso con UNKNOWN_PROVIDER: la cola de revision
mostraba "Unknown Provider" como si fuera el nombre del comercio, y el revisor
podia aprobar un ticket sin proveedor real creyendo que si lo habia leido.

Los otros dos son la clase de dato que hace fallar la pantalla en silencio:
un estado que el frontend no reconoce revienta el render de la cola entera, y
una cola en blanco no da error, da "no hay nada pendiente".
"""

import ast
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.enums import AUTO_APPROVE_CONFIDENCE, REVIEW_CONFIDENCE, UNKNOWN_PROVIDER
from app.schemas.ticket import TicketResponse

RAIZ = Path(__file__).resolve().parents[2]
VALIDATION_TS = RAIZ / "front" / "src" / "utils" / "validation.ts"
EXTRACTION_TS = RAIZ / "front" / "src" / "utils" / "extraction.ts"
TYPES_TS = RAIZ / "front" / "src" / "types" / "api.ts"


def _leer(path: Path) -> str:
    if not path.exists():
        pytest.fail(
            f"No existe {path}. Este test protege el contrato entre las dos "
            f"capas; si se movio el archivo, actualiza la ruta aqui."
        )
    return path.read_text(encoding="utf-8")


def _constante_ts(texto: str, nombre: str) -> str:
    m = re.search(rf"export const {nombre}\s*=\s*'([^']*)'", texto)
    assert m, f"No se encontro `export const {nombre} = '...'` en el frontend"
    return m.group(1)


class TestUnknownProvider:
    def test_el_frontend_usa_la_misma_cadena(self):
        assert _constante_ts(_leer(VALIDATION_TS), "UNKNOWN_PROVIDER") == UNKNOWN_PROVIDER

    def test_la_cola_de_revision_no_trae_una_copia_propia(self):
        """La constante se importa, no se reescribe.

        Si alguien pega el literal en la cola para "no depender de validation",
        la siguiente vez que cambie el contrato esa pantalla se queda vieja y
        nadie se entera hasta ver tickets mal clasificados.
        """
        texto = _leer(EXTRACTION_TS)
        assert "UNKNOWN_PROVIDER" in texto, "la cola debe usar la constante compartida"
        assert f"'{UNKNOWN_PROVIDER}'" not in texto, (
            "la cola de revision tiene una copia del literal; debe importar "
            "UNKNOWN_PROVIDER de validation.ts"
        )

    def test_la_cola_trata_ausencia_de_proveedor_como_ausencia(self):
        texto = _leer(EXTRACTION_TS)
        assert "nombreProvisorLegible" in texto
        assert "UNKNOWN_PROVIDER" in texto.split("nombreProvisorLegible")[-1], (
            "nombreProvisorLegible tiene que comparar contra UNKNOWN_PROVIDER; "
            "si no, un ticket ilegible se muestra con un proveedor inventado"
        )

    def test_ningun_archivo_del_backend_repite_el_literal(self):
        """Consolidar la constante no basta: hay que obligar a usarla.

        El motivo de mover UNKNOWN_PROVIDER a core.enums fue que el parser, el
        gate, los schemas y la API compararan contra la misma cadena. Eso solo
        se cumple si nadie pega el texto otra vez. Sin este test, una sola linea
        con el literal reintroduce el fallo original y la suite sigue en verde.

        Se recorre el AST y no el texto: los comentarios y los docstrings
        mencionan la cadena a proposito (para explicarla), y un grep los
        contaria como usos.
        """
        definicion = RAIZ / "app" / "core" / "enums.py"


        infractores: list[str] = []
        for archivo in sorted((RAIZ / "app").rglob("*.py")):
            if "__pycache__" in archivo.parts or archivo == definicion:
                continue
            arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
            for nodo in ast.walk(arbol):
                # Un str que es docstring es un Expr, no una comparacion ni una
                # asignacion. Se salta: es documentacion, no uso.
                if isinstance(nodo, ast.Expr) and isinstance(nodo.value, ast.Constant):
                    continue
                if isinstance(nodo, ast.Constant) and nodo.value == UNKNOWN_PROVIDER:
                    infractores.append(f"{archivo.relative_to(RAIZ)}:{nodo.lineno}")

        assert not infractores, (
            f"el literal {UNKNOWN_PROVIDER!r} esta escrito a mano en: {infractores}. "
            f"Importa UNKNOWN_PROVIDER de app.core.enums."
        )


class TestEstadosDeExtraccion:
    def test_la_union_type_coincide_con_los_estados_del_backend(self):
        from app.core.enums import ExtractionStatus

        texto = _leer(TYPES_TS)
        m = re.search(r"export type ExtractionStatus\s*=(.*?);", texto, re.DOTALL)
        assert m
        front = set(re.findall(r"'([A-Z_]+)'", m.group(1)))
        backend = {s.value for s in ExtractionStatus}
        assert front == backend, (
            f"divergen. Solo en frontend: {front - backend}. "
            f"Solo en backend: {backend - front}. "
            f"Un estado que solo existe en el backend revienta el render de la "
            f"cola; uno que solo existe en el frontend produce filtros que "
            f"siempre salen vacios."
        )

    def test_la_pantalla_tiene_una_etiqueta_para_cada_estado(self):
        from app.core.enums import ExtractionStatus

        texto = _leer(EXTRACTION_TS)
        faltan = [s.value for s in ExtractionStatus if f"{s.value}:" not in texto]
        assert not faltan, f"estados sin etiqueta en la cola: {faltan}"

    def test_la_pantalla_tiene_traduccion_para_cada_check_del_gate(self):
        """Un check sin traducir se muestra como codigo crudo.

        No es cosmetico: el revisor no puede corregir `tax_exceeds_total`. Es
        el motivo por el que existe el archivo.
        """
        texto = _leer(EXTRACTION_TS)
        # Los codigos se extraen del propio gate en vez de mantener una lista
        # aqui: una lista se olvida de actualizar en cuanto el gate crece, y el
        # test pasaria mientras la pantalla se queda vieja.
        #
        # Se cubren las dos formas que el gate emite de verdad: literales
        # ("tax_negative") y f-strings con argumentos entre parentesis
        # ("date_in_future(...)", "subtotal_plus_tax_mismatch(...)").
        fuente = (RAIZ / "app" / "services" / "confidence_gate.py").read_text(encoding="utf-8")
        encontrados = set(re.findall(r"""failures\.append\(\s*f?["']([a-z_]+)""", fuente))
        assert len(encontrados) >= 8, (
            f"solo se detectaron {len(encontrados)} checks en confidence_gate.py; "
            f"el patron de extraccion dejo de funcionar y este test estaria "
            f"vigilando el vacio"
        )
        faltan = sorted(c for c in encontrados if f"case '{c}'" not in texto)
        assert not faltan, (
            f"checks del gate sin traducir en la cola: {faltan}. "
            f"Se mostraran como codigo crudo, que es justo lo que un revisor "
            f"no puede corregir."
        )

    def test_la_cola_no_acepta_que_un_estado_desconocido_la_reviente(self):
        """Del enum del gate a la cadena que ve el frontend.

        Si alguien agrega un estado al enum y no lo agrega a la union type del
        frontend, la cola revienta al renderizar ese ticket. Y la pantalla mas
        importante del producto no puede caerse por un enum nuevo: caerse ahi
        deja la lista en blanco, y una lista en blanco no da error, dice "no hay
        nada pendiente".
        """
        texto = _leer(EXTRACTION_TS)
        assert "statusMeta" in texto, (
            "la cola debe pasar por statusMeta(), no por STATUS_META[estado] "
            "directo: un estado desconocido revienta el render entero"
        )
        assert "ESTADO_DESCONOCIDO" in texto, (
            "falta el estado de reserva para un status que la pantalla no conoce"
        )

    def test_umbral_del_gate_coincide_con_el_que_declara_el_servicio(self):
        """AUTO_APPROVE_CONFIDENCE decide que entra directo a conciliacion."""
        fuente = (RAIZ / "app" / "services" / "confidence_gate.py").read_text(encoding="utf-8")
        assert str(AUTO_APPROVE_CONFIDENCE) in fuente or "AUTO_APPROVE_CONFIDENCE" in fuente
        assert "REVIEW_CONFIDENCE" in fuente


class TestRespuestaDeTicket:
    def test_la_respuesta_no_arrastra_los_validadores_de_escritura(self):
        """Un ticket PENDIENTE existe precisamente por violar las reglas.

        Si TicketResponse heredara de TicketBase, devolver la cola de revision
        daria 422 por el primer ticket que este roto, y la cola quedaria
        inutilizable justo cuando hace falta. Esto ya paso una vez.
        """
        from app.schemas.ticket import TicketBase

        assert TicketResponse is not TicketBase
        assert not issubclass(TicketResponse, TicketBase)

    def test_la_respuesta_sabe_responder_sobre_datos_rotos(self):
        from app.schemas.ticket import TicketResponse

        # Total 0, proveedor sin identificar y sin fecha de creacion: el peor
        # caso que puede haber en la base, y el unico que importa para la cola.
        r = TicketResponse(
            id="00000000-0000-0000-0000-000000000000",
            company_id="00000000-0000-0000-0000-000000000000",
            provider_name=UNKNOWN_PROVIDER,
            total_amount="0.00",
            tax_amount="0.00",
            expense_date="2025-01-15",
            extraction_status="PENDIENTE",
            created_at=None,
        )
        # Decimal, no float: un total de este pasa por float sin que se note
        assert r.total_amount == Decimal("0.00")
        assert r.confidence is None, "un documento ilegible no tiene confianza"
        assert r.validation_errors is None

    def test_created_at_anulable_no_reviienta_la_situacion(self):
        """La columna admite NULL; la respuesta tambien.

        Sin esto, una fila con created_at nulo hace que la cola entera devuelva
        500 al serializar. Y el calculo de antiguedad, que esta a tres lineas de
        ahi, si habia sido endurecido: el mismo campo rompia en un lado y no en
        el otro.
        """
        from app.schemas.ticket import TicketResponse

        r = TicketResponse(
            id="00000000-0000-0000-0000-000000000000",
            company_id="00000000-0000-0000-0000-000000000000",
            provider_name="X",
            total_amount="10.00",
            tax_amount="1.00",
            expense_date="2025-01-15",
            extraction_status="PENDIENTE",
            created_at=None,
        )
        assert r.created_at is None

    def test_el_mensaje_de_aprobacion_incluye_los_motivos(self):
        """El 422 tiene que decir QUE esta roto, no solo que no se puede.

        Sin los codigos, quien recibe el error tiene que adivinar que corregir, y
        el unico camino que queda es probar al azar.
        """
        from app.services.confidence_gate import validate_extraction
        from decimal import Decimal as D
        from datetime import date

        out = validate_extraction(
            provider_name=UNKNOWN_PROVIDER,
            total_amount=D("0"),
            tax_amount=D("0"),
            expense_date=date.today(),
        )
        assert out.ok is False
        assert out.failures, "un rechazo sin motivos no es revisable"
