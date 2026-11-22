from decimal import Decimal
import unittest
from unittest.mock import patch

from app.agent.config import AgentConfig
from app.agent.core import ExpenseAgentRunner
from app.agent.schemas import AgentRequest
from app.agent.tools import obtener_resumen_proveedor, verificar_consistencia_aritmetica


class FakeFunction:
    def __init__(self, name: str, arguments: dict) -> None:
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, name: str, arguments: dict) -> None:
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content: str = "", tool_calls: list | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, exclude_none: bool = True) -> dict:
        data: dict = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [
                {"function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in self.tool_calls
            ]
        return data


class FakeResponse:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message


class FakeOllamaClient:
    def __init__(self, scripted_messages: list[FakeMessage]) -> None:
        self._scripted = list(scripted_messages)
        self.calls = 0

    def chat(self, **kwargs) -> FakeResponse:
        idx = min(self.calls, len(self._scripted) - 1)
        self.calls += 1
        return FakeResponse(self._scripted[idx])


class TestAgentSuite(unittest.TestCase):
    def test_aritmetica_exacta_y_formateo(self) -> None:
        res = verificar_consistencia_aritmetica("$1,250.50", "$200.08", "$1,450.58")
        self.assertTrue(res["cuadra"])
        self.assertEqual(Decimal(res["total_calculado"]), Decimal("1450.58"))
        self.assertEqual(Decimal(res["diferencia"]), Decimal("0.00"))

    def test_aritmetica_fuera_de_tolerancia(self) -> None:
        res = verificar_consistencia_aritmetica("100.00", "16.00", "116.02", tolerancia="0.01")
        self.assertFalse(res["cuadra"])
        self.assertEqual(Decimal(res["diferencia"]), Decimal("0.02"))

    def test_proveedor_sin_bd_es_resiliente(self) -> None:
        res = obtener_resumen_proveedor("  Uber   Eats  ")
        self.assertEqual(res["modo"], "solo_lectura")
        self.assertEqual(res["proveedor"], "uber eats")
        self.assertIn(res["estado_consulta"], {"ok", "bd_no_disponible"})

    def test_fallback_json_y_respuesta_final(self) -> None:
        script = [
            FakeMessage(
                content='{"name": "verificar_consistencia_aritmetica", "arguments": {"subtotal": "100.00", "impuestos": "16.00", "total": "118.00"}}'
            ),
            FakeMessage(content="La factura no cuadra por 2.00."),
        ]
        with patch("app.agent.core.ollama.Client", return_value=FakeOllamaClient(script)):
            runner = ExpenseAgentRunner(config=AgentConfig(max_steps=4))
            diag = runner.run(AgentRequest(pregunta_o_caso="Valida 100 + 16 vs 118"))

        self.assertEqual(diag.estado, "ok")
        self.assertEqual(diag.pasos_ejecutados, 2)
        self.assertEqual(len(diag.hallazgos), 1)

    def test_guardrail_llamada_repetida(self) -> None:
        repeated = FakeMessage(
            content="",
            tool_calls=[
                FakeToolCall("obtener_resumen_proveedor", {"nombre_normalizado": "el general"})
            ],
        )
        with patch("app.agent.core.ollama.Client", return_value=FakeOllamaClient([repeated, repeated])):
            runner = ExpenseAgentRunner(config=AgentConfig(max_steps=4))
            diag = runner.run(AgentRequest(pregunta_o_caso="Consulta el general"))

        self.assertEqual(diag.estado, "requiere_revision")
        self.assertIn("repetida", diag.diagnostico.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestAgentBridge(unittest.TestCase):
    def test_bridge_resultado_lectura_solo_lectura(self) -> None:
        from types import SimpleNamespace
        from app.agent.bridge import diagnosticar_resultado_lectura

        extraccion = SimpleNamespace(
            provider_name="El General",
            subtotal="100.00",
            impuestos="16.00",
            total="118.00",
        )
        resultado = SimpleNamespace(
            formato_detectado="pdf",
            veredicto_status="observado",
            coincide_con_guardado=True,
            checks_fallidos=["suma_totales"],
            pasos=[SimpleNamespace(escalon="1", motor="pdf_texto", aceptado=True)],
            extraccion=extraccion,
            error=None,
        )
        script = [
            FakeMessage(
                content="",
                tool_calls=[
                    FakeToolCall(
                        "verificar_consistencia_aritmetica",
                        {"subtotal": "100.00", "impuestos": "16.00", "total": "118.00"},
                    )
                ],
            ),
            FakeMessage(content="El comprobante presenta diferencia aritmética de 2.00."),
        ]
        with patch("app.agent.core.ollama.Client", return_value=FakeOllamaClient(script)):
            runner = ExpenseAgentRunner(config=AgentConfig(max_steps=4))
            diag = diagnosticar_resultado_lectura(resultado, runner=runner)

        self.assertEqual(diag.estado, "ok")
        self.assertEqual(diag.pasos_ejecutados, 2)
        self.assertIn("2.00", diag.hallazgos[0])
