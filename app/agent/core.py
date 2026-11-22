import hashlib
import json
import re
from typing import Any, Callable

import ollama

from app.agent.config import AgentConfig
from app.agent.schemas import AgentDiagnosis, AgentRequest, ToolCallRecord
from app.agent.tools import (
    obtener_resumen_proveedor,
    verificar_consistencia_aritmetica,
)

SYSTEM_PROMPT = (
    "Eres un analista de conciliación de gastos y facturas. "
    "Cuando necesites verificar cálculos o consultar proveedores, invoca la herramienta correspondiente. "
    "Tras recibir el resultado de la herramienta, responde en español indicando si cuadra o no y por qué. "
    "Nunca inventes montos ni datos."
)


class ExpenseAgentRunner:
    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()
        self._seen_calls: set[str] = set()
        self._client = ollama.Client(
            host=self.config.base_url,
            timeout=self.config.timeout_seconds,
        )
        self._registry: dict[str, Callable[..., dict[str, Any]]] = {
            "verificar_consistencia_aritmetica": verificar_consistencia_aritmetica,
            "obtener_resumen_proveedor": obtener_resumen_proveedor,
        }

    def _make_hash(self, tool_name: str, payload: dict[str, Any]) -> str:
        raw = json.dumps({"tool": tool_name, "payload": payload}, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _register_or_check_repeat(self, tool_name: str, payload: dict[str, Any]) -> ToolCallRecord | None:
        call_hash = self._make_hash(tool_name, payload)
        if call_hash in self._seen_calls:
            return None
        self._seen_calls.add(call_hash)
        return ToolCallRecord(
            tool_name=tool_name,
            args_hash=call_hash,
            summary=f"{tool_name}({payload})",
        )

    def _extract_fallback_calls(self, content: str | None) -> list[tuple[str, dict[str, Any]]]:
        if not content:
            return []
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", content.strip()).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            args = data.get("arguments")
            return [(data["name"], dict(args) if isinstance(args, dict) else {})]
        return []

    def _run_single_tool(
        self,
        fn_name: str,
        fn_args: dict[str, Any],
        step: int,
        hallazgos: list[str],
        messages: list[dict[str, Any]],
    ) -> AgentDiagnosis | None:
        record = self._register_or_check_repeat(fn_name, fn_args)
        if record is None:
            return AgentDiagnosis(
                estado="requiere_revision",
                diagnostico=f"Se detuvo por llamada repetida a herramienta: {fn_name}.",
                hallazgos=hallazgos,
                pasos_ejecutados=step,
            )

        fn = self._registry.get(fn_name)
        tool_result = (
            {"error": f"Herramienta no permitida: {fn_name}"}
            if fn is None
            else fn(**fn_args)
        )

        hallazgos.append(f"Paso {step}: {fn_name} -> {json.dumps(tool_result, ensure_ascii=False)}")
        messages.append({
            "role": "tool",
            "content": json.dumps(tool_result, ensure_ascii=False),
        })
        return None

    def run(self, request: AgentRequest) -> AgentDiagnosis:
        self._seen_calls.clear()
        hallazgos: list[str] = []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": request.pregunta_o_caso},
        ]

        try:
            for step in range(1, self.config.max_steps + 1):
                response = self._client.chat(
                    model=self.config.model,
                    messages=messages,
                    tools=list(self._registry.values()),
                    options={"num_ctx": self.config.num_ctx, "temperature": 0.1},
                )
                msg = response.message
                messages.append(msg.model_dump(exclude_none=True))

                if msg.tool_calls:
                    for call in msg.tool_calls:
                        stop_diag = self._run_single_tool(
                            call.function.name,
                            dict(call.function.arguments or {}),
                            step,
                            hallazgos,
                            messages,
                        )
                        if stop_diag is not None:
                            return stop_diag
                    continue

                fallback_calls = self._extract_fallback_calls(msg.content)
                if fallback_calls:
                    for fn_name, fn_args in fallback_calls:
                        stop_diag = self._run_single_tool(
                            fn_name,
                            fn_args,
                            step,
                            hallazgos,
                            messages,
                        )
                        if stop_diag is not None:
                            return stop_diag
                    continue

                texto_final = (msg.content or "").strip() or "Sin respuesta textual del modelo."
                return AgentDiagnosis(
                    estado="ok",
                    diagnostico=texto_final,
                    hallazgos=hallazgos,
                    pasos_ejecutados=step,
                )

            return AgentDiagnosis(
                estado="limite_alcanzado",
                diagnostico=f"Se alcanzó el límite de {self.config.max_steps} pasos sin respuesta final.",
                hallazgos=hallazgos,
                pasos_ejecutados=self.config.max_steps,
            )
        except Exception as exc:
            return AgentDiagnosis(
                estado="error",
                diagnostico=f"Error ejecutando agente local: {exc}",
                hallazgos=hallazgos,
                pasos_ejecutados=len(self._seen_calls),
            )
