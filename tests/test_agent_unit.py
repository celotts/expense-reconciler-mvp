from pathlib import Path
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

    def test_diagnosticar_lectura_incluir_agente(self) -> None:
        import asyncio
        import importlib.util
        import sys
        import types
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from app.agent.schemas import AgentDiagnosis

        if "app.services" not in sys.modules:
            pkg = types.ModuleType("app.services")
            pkg.__path__ = [str(Path("app/services").resolve())]
            sys.modules["app.services"] = pkg

        if "sqlalchemy" not in sys.modules:
            sa = types.ModuleType("sqlalchemy")
            sa.select = lambda *a, **kw: None
            sa.__getattr__ = lambda name: type(name, (), {"__init__": lambda self, *a, **kw: None})
            sa_ext = types.ModuleType("sqlalchemy.ext")
            sa_async = types.ModuleType("sqlalchemy.ext.asyncio")
            sa_async.AsyncSession = object
            sa_async.__getattr__ = lambda name: type(name, (), {"__init__": lambda self, *a, **kw: None})
            sys.modules.update(
                {"sqlalchemy": sa, "sqlalchemy.ext": sa_ext, "sqlalchemy.ext.asyncio": sa_async}
            )

        if "pydantic_settings" not in sys.modules:
            ps = types.ModuleType("pydantic_settings")
            ps.BaseSettings = object
            ps.SettingsConfigDict = lambda **kw: kw
            sys.modules["pydantic_settings"] = ps

        if "app.models" not in sys.modules:
            mpkg = types.ModuleType("app.models")
            mpkg.__path__ = []
            tmod = types.ModuleType("app.models.ticket")
            tmod.TicketModel = object
            sys.modules.update({"app.models": mpkg, "app.models.ticket": tmod})

        if "app.services.parser_service" not in sys.modules:
            psvc = types.ModuleType("app.services.parser_service")
            psvc.__getattr__ = lambda name: (lambda *a, **kw: None)
            sys.modules["app.services.parser_service"] = psvc

        spec = importlib.util.spec_from_file_location(
            "app.services.lectura_diagnostico",
            Path("app/services/lectura_diagnostico.py").resolve(),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["app.services.lectura_diagnostico"] = mod
        spec.loader.exec_module(mod)

        diag_falso = AgentDiagnosis(
            estado="ok",
            diagnostico="Diferencia de 2.00 entre total calculado y reportado.",
            hallazgos=["Paso 1: verificar_consistencia_aritmetica -> cuadra=false"],
            pasos_ejecutados=2,
        )
        extraccion = SimpleNamespace(
            provider_name="El General",
            subtotal="100.00",
            impuestos="16.00",
            total="118.00",
        )

        async def _run() -> tuple:
            with (
                patch.object(
                    mod,
                    "_explorar_texto",
                    side_effect=lambda t, s, *, exigir_items: s.pasos.append(
                        mod.PasoLectura(
                            escalon="extraccion",
                            motor="stub",
                            dio={"len": len(t)},
                            aceptado=True,
                            motivo=None,
                        )
                    ),
                ),
                patch.object(mod, "capture_ticket", new=AsyncMock(return_value=extraccion)),
                patch.object(
                    mod,
                    "_anotar_veredicto",
                    side_effect=lambda s, e: setattr(s, "veredicto_status", "observado"),
                ),
                patch(
                    "app.agent.bridge.diagnosticar_resultado_lectura",
                    return_value=diag_falso,
                ) as mock_bridge,
            ):
                sin_ag = await mod.diagnosticar_lectura(
                    SimpleNamespace(),
                    b"SUBTOTAL 100.00 IVA 16.00 TOTAL 118.00",
                    declarado="txt",
                    nombre="ticket.txt",
                    incluir_agente=False,
                )
                con_ag = await mod.diagnosticar_lectura(
                    SimpleNamespace(),
                    b"SUBTOTAL 100.00 IVA 16.00 TOTAL 118.00",
                    declarado="txt",
                    nombre="ticket.txt",
                    incluir_agente=True,
                )
            return sin_ag, con_ag, mock_bridge.call_count

        sin_agente, con_agente, calls = asyncio.run(_run())
        self.assertIsNone(sin_agente.diagnostico_agente)
        self.assertEqual(calls, 1)
        self.assertIsInstance(con_agente.diagnostico_agente, dict)
        self.assertEqual(con_agente.diagnostico_agente["estado"], "ok")
        self.assertEqual(con_agente.diagnostico_agente["pasos_ejecutados"], 2)

