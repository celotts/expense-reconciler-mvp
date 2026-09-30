"""La interfaz tiene que ofrecer el camino que pasa por el confidence gate.

Contexto. El backend ya sabia hacer lo correcto: `POST /tickets/extract-and-create`
corre el gate de verdad, deja el ticket con `confidence` y `confidence_source`, y si
sale AUTO_APROBADO lo mete al muestreo del 5%. La interfaz, en cambio, solo llamaba
a `POST /tickets/extract` (preview) y luego a `POST /tickets/`, que es captura manual:
nacian con `confidence=NULL` y sin muestrear.

El efecto no era un error visible. Era que el SLO de exactitud se calculaba sobre
lecturas que la aplicacion no producia, y el usuario no veia nunca si lo que habia
leido la IA habia pasado la verificacion o no.

Estos tests leen el fuente del frontend porque el contrato esta escrito en los dos
lados, en dos lenguajes, sin que nada los relacione. Mismo criterio que los demas
tests de este archivo.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
TICKETS_TSX = RAIZ / "front" / "src" / "pages" / "Tickets.tsx"
API_TS = RAIZ / "front" / "src" / "services" / "api.ts"
VEREDICTO_TS = RAIZ / "front" / "src" / "utils" / "veredicto.ts"
COMPONENTE = RAIZ / "front" / "src" / "components" / "VeredictoTicket.tsx"


def _leer(path: Path) -> str:
    if not path.exists():
        pytest.fail(
            f"No existe {path}. Este test protege el camino que lleva al confidence "
            f"gate; si se movio el archivo, actualiza la ruta aqui."
        )
    return path.read_text(encoding="utf-8")


def _bloque(texto: str, inicio: str) -> str:
    """El cuerpo de una funcion `const X = ... => { ... };`."""
    m = re.search(rf"const {inicio}[\s\S]*?\n  \}};", texto)
    assert m, f"No se encontro la funcion {inicio}"
    return m.group(0)


class TestLaInterfazOfreceElCaminoDelGate:
    def test_existe_una_via_que_pasa_por_el_gate(self):
        """Sin esto, la UI nunca produce lecturas automaticas y el 96% no es medible."""
        tickets = _leer(TICKETS_TSX)
        assert "extractAndCreate" in tickets, (
            "La pantalla no llama a extractAndCreate. Con eso, todo ticket nace por "
            "captura manual: sin confidence, sin confidence_source y fuera del "
            "muestreo. El SLO de exactitud queda sin evidencia por este camino."
        )

    def test_la_via_del_gate_es_una_llamada_real_del_cliente(self):
        api = _leer(API_TS)
        m = re.search(r"extractAndCreate:[\s\S]*?\n  \},", api)
        assert m, "ticketsApi.extractAndCreate desaparecio del cliente"
        cuerpo = m.group(0)
        assert "/tickets/extract-and-create" in cuerpo, (
            "extractAndCreate tiene que pegarle al endpoint que corre el gate. Si "
            "apunta a otro, vuelve el problema sin que se note."
        )
        assert "company_id" in cuerpo, "extract-and-create necesita company_id"

    def test_el_boton_que_acepta_la_lectura_es_distinto_del_que_permite_editar(self):
        """Las dos vias terminan en tickets con significado distinto.

        Aceptar sin tocar -> pasa por el gate, es medible.
        Editar -> captura manual, porque si una persona corrigio a la IA el resultado
        ya no es lectura automatica.
        """
        tickets = _leer(TICKETS_TSX)
        assert re.search(r"aceptarLecturaDeIA", tickets), "Falta la via que acepta"
        assert re.search(r"editarAntesDeGuardar", tickets), "Falta la via que edita"
        assert "Aceptar y verificar con IA" in tickets, (
            "El boton que pasa por el gate tiene que decirlo. Elegir entre dos "
            "botones sin explicar la diferencia es elegir al azar."
        )

    def test_editar_avisa_que_deja_de_contar_para_la_exactitud(self):
        """Sin este aviso, elegir 'revisar y corregir' parece la version incompleta."""
        tickets = _leer(TICKETS_TSX)
        bloque = _bloque(tickets, "editarAntesDeGuardar")
        assert re.search(r"setAvisoEdicionManual\(", bloque), (
            "Editar sin avisar que el ticket dejara de contar como lectura "
            "automatica hace que el usuario piense que hay un fallo."
        )
        assert "medición de exactitud" in _leer(TICKETS_TSX) or "medicion de exactitud" in _leer(
            TICKETS_TSX
        ), "El aviso tiene que decir de que se trata: la medicion de exactitud."

    def test_no_se_pierde_la_lectura_si_la_via_del_gate_falla(self):
        """Un ticket guardado con su comprobante vale mas que una lectura perdida."""
        bloque = _bloque(_leer(TICKETS_TSX), "aceptarLecturaDeIA")
        assert re.search(r"catch", bloque), "aceptarLecturaDeIA no tiene manejo de error"
        assert re.search(r"abrirFormularioConExtraccion\(", bloque), (
            "Si extractAndCreate falla, hay que caer al formulario con los datos ya "
            "llenos. Sin esto, un 4xx deja al usuario sin ticket y sin explicacion."
        )
        # El error no puede tragarse el exito: el aviso de "se guardo" va aparte.
        assert not re.search(r"catch\s*\{\s*\}", bloque), (
            "Un catch vacio hides el motivo por el que cayo a la via manual."
        )


class TestElComprobanteSigueGuardandose:
    def test_la_via_del_gate_no_rompe_el_adjunto_del_comprobante(self):
        """extract-and-create guarda los bytes del server.

        Antes de que existiera esa ruta, el unico modo de tener comprobante era
        crearlo a mano y adjuntarlo despues con subirDocumento. Este test existe
        para que nadie lo lea como "el archivo ya no se guarda" y lo quite.
        """
        tickets = _leer(TICKETS_TSX)
        # El camino manual (handleSubmit) sigue adjuntando.
        assert "subirDocumento" in _bloque(tickets, "handleSubmit"), (
            "handleSubmit debe seguir adjuntando el comprobante en la via manual."
        )
        # Y la via del gate no lo hace a mano, porque el endpoint ya lo guarda.
        bloque = _bloque(tickets, "aceptarLecturaDeIA")
        assert "subirDocumento" not in bloque, (
            "extract-and-create ya guarda los comprobante en el servidor. Subirlo "
            "otra vez desde aqui seria una segunda peticion que ademas reemplaza "
            "los bytes (document_service borra el anterior antes de insertar)."
        )

    def test_el_archivo_sigue_disponible_para_la_via_del_gate(self):
        tickets = _leer(TICKETS_TSX)
        assert re.search(r"setPendingFile\(\s*file\s*\)", _bloque(tickets, "handleExtract")), (
            "El File se suelta en la extraccion. extractAndCreate lo necesita."
        )


class TestElVeredictoSeEnsenA:
    def test_existe_un_traductor_de_veredicto(self):
        veredicto = _leer(VEREDICTO_TS)
        for simbolo in ["AUTO_APROBADO", "REQUIERE_REVISION", "PENDIENTE", "APROBADO", "RECHAZADO"]:
            assert simbolo in veredicto, (
                f"El traductor no conoce {simbolo}. Un estado que el backend emite y "
                f"la pantalla no traduce aparece crudo, o peor, no aparece."
            )

    def test_cada_estado_tiene_texto_propio(self):
        """Que el switch tenga una rama por estado, no un default que lo tape todo."""
        veredicto = _leer(VEREDICTO_TS)
        bloque = re.search(r"export function lineaVeredicto[\s\S]*?\n}", veredicto)
        assert bloque, "No se encontro lineaVeredicto"
        cuerpo = bloque.group(0)
        ramas = len(re.findall(r"case '", cuerpo))
        assert ramas >= 5, (
            f"lineaVeredicto solo tiene {ramas} ramas para 5 estados. Un estado que "
            f"cae en default es un estado que el usuario no entiende."
        )
        assert "Estado no reconocido" in cuerpo, (
            "El default tiene que mostrarse crudo y decir que no se reconoce. "
            "Inventar una etiqueta para un estado desconocido hace que el usuario "
            "no sepa que hay algo que no se le esta explicando."
        )

    def test_la_confianza_nunca_se_muestra_sin_su_origen(self):
        """Un 0.97 del modelo y un 0.97 de la aritmetica no son la misma evidencia."""
        veredicto = _leer(VEREDICTO_TS)
        assert re.search(r"etiquetaOrigen", veredicto), "Falta la etiqueta de origen"
        bloque = re.search(r"export function lineaVeredicto[\s\S]*?\n}", veredicto).group(0)
        assert "origen" in bloque, (
            "El texto del veredicto tiene que decir de donde salio la confianza."
        )
        for origen in ["pdf_text", "rules", "llm", "manual"]:
            assert origen in veredicto, f"Falta traducir el origen {origen}"

    def test_se_muestran_los_motivos_del_gate_con_sus_numeros(self):
        """`validation_errors` trae los numeros comparados, para eso se arman."""
        veredicto = _leer(VEREDICTO_TS)
        assert re.search(r"motivosDelGate", veredicto), "Falta el helper de motivos"
        assert re.search(r"split\('\;'\)|split\(\";\"\)", veredicto), (
            "Los motivos vienen separados por '; '. Si no se parten, el usuario ve "
            "una sola cadena larga en vez de una lista."
        )
        assert re.search(r"join\('\. '\)|join\(\"\. \"\)", veredicto), (
            "Los motivos se vuelven a unir para el texto, con punto entre ellos."
        )

    def test_el_componente_se_usa_en_la_pantalla(self):
        tickets = _leer(TICKETS_TSX)
        assert "VeredictoTicket" in tickets, (
            "La pantalla de tickets no muestra ningun veredicto. Era el punto: la IA "
            "podia auto-aprobar un ticket sin que nadie se enterara."
        )
        assert _leer(COMPONENTE).count("VeredictoTicket") >= 1


class TestElGateNoSeHaTocado:
    """La Tarea 1 es de frontend. El gate es la defensa y no se toca."""

    def test_el_umbral_de_auto_aprobacion_sigue_siendo_el_del_backend(self):
        from app.core.enums import AUTO_APPROVE_CONFIDENCE

        tickets = _leer(TICKETS_TSX)
        assert "AUTO_APROBADO" in tickets
        # El frontend no debe decidir que es auto-aprobado por su cuenta.
        assert not re.search(r"confidence\s*>=\s*0\.\d", tickets), (
            "El frontend no compara la confianza contra ningun umbral para decidir "
            f"el estado. El unico umbral valido es {AUTO_APPROVE_CONFIDENCE} y vive "
            f"en app/core/enums.py."
        )
