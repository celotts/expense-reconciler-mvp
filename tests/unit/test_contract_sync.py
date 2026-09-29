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
SPOTCHECK_TS = RAIZ / "front" / "src" / "utils" / "spotcheck.ts"


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


class TestElMuestreoHablaElMismoIdioma:
    """Los enums y la lista de campos del muestreo, de los dos lados.

    Aqui la divergencia no es cosmetics: cada una rompe el conteo de la
    exactitud en silencio.

    - Un veredicto que el backend emite y el frontend no conoce se pinta como
      desconocido, lo cual esta bien, pero un veredicto que el frontend
      RECONOCE y el backend nunca emite hace que una pantalla anuncie un
      resultado que nadie midio.
    - Un campo que el frontend ofrece y el backend rechaza se pierde con un
      422 en el momento de marcarlo: el revisor dejo todo el trabajo hecho y
      no se guardo.
    - Un campo que el backend acepta y el frontend no ofrece nunca se puede
      marcar como mal leido, y el reporte lo subcuenta en silencio. Este es el
      peor de los tres, porque no da ningun error: la exactitud sale un poco
      mejor de lo que es.
    """

    def test_los_estados_de_la_muestra_coinciden(self):
        from app.core.enums import SpotCheckStatus

        texto = _leer(TYPES_TS)
        m = re.search(r"export type SpotCheckStatus\s*=(.*?);", texto, re.DOTALL)
        assert m, "no se encontro `export type SpotCheckStatus` en api.ts"
        front = set(re.findall(r"'([A-Z_]+)'", m.group(1)))
        back = {s.value for s in SpotCheckStatus}
        assert front == back, (
            f"divergen. Solo en frontend: {front - back}. Solo en backend: {back - front}."
        )

    def test_los_veredictos_coinciden(self):
        from app.services.accuracy_service import Veredicto

        texto = _leer(TYPES_TS)
        m = re.search(r"export type Veredicto\s*=(.*?);", texto, re.DOTALL)
        assert m, "no se encontro `export type Veredicto` en api.ts"
        front = set(re.findall(r"'([A-Z_]+)'", m.group(1)))
        back = {
            v for v in vars(Veredicto).values()
            if isinstance(v, str) and v.isupper()
        }
        assert front == back, (
            f"divergen. Solo en frontend: {front - back}. Solo en backend: {back - front}. "
            f"Un veredicto que solo existe en el frontend se anuncia como un "
            f"resultado que nadie midio."
        )

    def test_los_campos_marcables_son_los_mismos_y_en_el_mismo_conjunto(self):
        """La lista que ofrece la pantalla es la que acepta el backend.

        Se compara el CONJUNTO y no el orden: el orden es presentacion, y el
        backend no lo usa. Lo que no puede diferir es el conjunto, porque cada
        campo que se cuela o se va rompe el conteo del reporte.
        """
        from app.schemas.ticket import CAMPOS_VERIFICABLES

        texto = _leer(SPOTCHECK_TS)
        m = re.search(r"CAMPOS_MUESTRABLES[^=]*=\s*\[(.*?)\];", texto, re.DOTALL)
        assert m, "no se encontro `CAMPOS_MUESTRABLES` en spotcheck.ts"
        front = set(re.findall(r"campo:\s*'([a-z_]+)'", m.group(1)))
        back = set(CAMPOS_VERIFICABLES)
        assert front == back, (
            f"divergen.\n"
            f"  La pantalla ofrece y el backend rechaza: {sorted(front - back)}\n"
            f"  El backend acepta y la pantalla no ofrece: {sorted(back - front)}\n"
            f"El primero se pierde con un 422 al marcar. El segundo no da ningun "
            f"error: el campo nunca se puede marcar mal leido y la exactitud sale "
            f"un poco mejor de lo que es."
        )

    def test_categoria_no_se_puede_marcar_ni_aunque_se_agregue_al_frente(self):
        """La categoria no la lee la IA: la elige una persona.

        Un papel no dice "esto es alimento". Si `category` apareciera en la
        lista, el muestreo mediria una decision humana con la metrica del
        automatismo, y el numero de exactitud dejaria de significar lo que dice.
        Este test falla si alguien la agrega a `CAMPOS_VERIFICABLES` sin
        pensar que es "un campo mas". Lo es, y no debe estar.
        """
        from app.schemas.ticket import CAMPOS_VERIFICABLES

        assert "category" not in CAMPOS_VERIFICABLES, (
            "`category` no viene del documento: la elige quien clasifica el gasto. "
            "Marcarla haria que la exactitud mida a una persona y no al extractor."
        )


class TestLosCamposDelTicketCoinciden:
    """El conjunto completo de campos, de los dos lados, comparado campo a campo.

    Los tests anteriores comprueban valores sueltos que ya divergieron una vez
    (el proveedor desconocido). Este comprueba la forma ENTERA, y asi la
    siguiente divergencia sale sola en vez de cuando alguien mire en pantalla.

    No se comparan campo a campo uno por uno contra una lista escrita a mano:
    esa lista se desactualiza sin avisar y el test sigue en verde vigilando un
    contrato viejo. Las dos listas se leen de los archivos.
    """

    @staticmethod
    def _campos_del_frontend() -> dict[str, str]:
        """Los campos declarados en `interface Ticket`, con su tipo textual."""
        texto = re.sub(r"/\*.*?\*/", "", _leer(TYPES_TS), flags=re.DOTALL)
        texto = re.sub(r"//[^\n]*", "", texto)
        m = re.search(r"export interface Ticket\s*\{(.*?)\n\}", texto, re.DOTALL)
        assert m, "no se encontro `export interface Ticket` en api.ts"
        campos = {}
        for linea in m.group(1).split("\n"):
            declaracion = re.match(
                r"\s*([a-z_][a-z0-9_]*)\??\s*:\s*(.+?);?\s*$", linea
            )
            if declaracion:
                campos[declaracion.group(1)] = declaracion.group(2)
        assert campos, "el parser de la interface no encontro ningun campo"
        return campos

    def test_no_sobra_ni_falta_un_campo(self):
        front = set(self._campos_del_frontend())
        back = set(TicketResponse.model_fields)

        assert front == back, (
            f"divergen.\n"
            f"  El backend emite y el frontend no declara: {sorted(back - front)}\n"
            f"  El frontend espera y el backend no emite: {sorted(front - back)}"
        )

    def test_la_pantalla_muestra_el_documento_original(self):
        """Las dos pantallas que revisan un documento tienen que abrirlo.

        La cola de revision dice "corrige contra el documento original" y el
        muestreo dice "contrasta cada fila contra el documento original". Las dos
        instrucciones eran mentira hasta que el documento se empezo a guardar:
        el sistema lo leia y lo tiraba, y lo unico que se podia ver era el texto
        que el modelo habia transcrito. Contrastar la transcripcion del modelo
        contra si misma no verifica nada.

        Se comprueban las dos pantallas porque el fallo es por omision: si una
        tiene el enlace y la otra no, la que no lo tiene sigue funcionando
        (el `raw_text` sigue ahi), y nadie se da cuenta de que esa pantalla
        # Fallo grave y silencioso: la pantalla sigue "funcionando" con el
        # `raw_text`, asi que nadie se da cuenta de que esa pantalla revisa a
        # ciegas.
        """
        cola = _leer(RAIZ / "front" / "src" / "pages" / "ReviewQueue.tsx")
        muestreo = _leer(RAIZ / "front" / "src" / "pages" / "SpotCheck.tsx")
        componente = _leer(
            RAIZ / "front" / "src" / "components" / "TicketDocumento.tsx"
        )

        for nombre, texto in (("la cola de revision", cola), ("el muestreo", muestreo)):
            assert "VerDocumento" in texto, (
                f"{nombre} no muestra el comprobante original. Sin el, se revisa "
                f"contra el `raw_text`, que es lo que el sistema transcribio: "
                f"contrastar eso contra si mismo no verifica la lectura."
            )

        # El enlace tiene que salir del token, no de un `src` pelado. Un
        # `<img src={documento_url}>` contra un endpoint que exige cabecera
        # `Authorization` se ve como un recuadro vacio, sin error, y el revisor
        # concluye que el documento no existe.
        assert "ticketsApi.documento" in componente, (
            "el comprobante tiene que descargarse con el token; un `src` a la "
            "URL de la API se ve vacio porque no manda la cabecera de "
            "autorizacion, y eso no se distingue de un documento que no esta"
        )

    def test_el_frontend_declara_los_campos_del_documento(self):
        """El contrato del documento tiene que existir en los dos lados.

        No por simetria con el test de arriba, sino por lo que pasa si falta: el
        backend emite `tiene_documento` y el frontend no lo declara, la pantalla
        no puede distinguir un ticket de captura manual de uno al que se le perdio
        el archivo, y quien revisa decide sin ver el papel. El muestreo sigue
        dando numeros y nadie se entera de que se estan midiendo a ciegas.

        Se comprueban los tres y no solo el booleano: la URL sin el booleano no
        alcanza para decidir si pintar el enlace, y el tamano sin la URL no deja
        avisar cuanto pesa antes de descargarlo.
        """
        front = self._campos_del_frontend()
        for campo in ("tiene_documento", "documento_url", "documento_tamano"):
            assert campo in front, (
                f"`{campo}` lo emite el backend y el frontend no lo declara. "
                f"Sin el, la cola de revision y el muestreo no pueden mostrar el "
                f"comprobante original, y el veredicto se decide sin verlo."
            )

    def test_lo_que_el_backend_puede_dejar_nulo_tambien_se_declara_nulo(self):
        """Un `T | None` del backend que el frontend tipa sin `| null` miente.

        No revienta al compilar, porque el JSON llega como `any` en la practica.
        Revienta al pintar: `new Date(null)` no da error, da "Invalid Date", y
        una fecha invalida en la columna de antiguedad se lee como dato
        legitimo. La fila con `created_at` nulo es justamente la que mas
        importa ver, asi que el caso no es teorico.
        """
        front = self._campos_del_frontend()
        mentirosos = []
        for nombre, tipo in front.items():
            # `undefined` no sirve: el JSON no lo produce, y ademas distingue
            # "falta" de "es null", que para esta API es la misma cosa.
            if "null" in tipo:
                continue
            if not str(TicketResponse.model_fields[nombre].annotation).endswith("None"):
                continue
            mentirosos.append((nombre, tipo))
        assert not mentirosos, (
            "el backend puede devolver NULL y el frontend no lo contempla: "
            f"{mentirosos}. O se marca `| null` en api.ts, o el backend deja de "
            "poder devolverlo; lo que no puede ser es que los dos digan cosas "
            "distintas."
        )
