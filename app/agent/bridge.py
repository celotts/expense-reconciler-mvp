from __future__ import annotations

from typing import Any

from app.agent.core import ExpenseAgentRunner
from app.agent.schemas import AgentDiagnosis, AgentRequest


def _leer_campo(obj: Any, nombre: str, por_defecto: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(nombre, por_defecto)
    return getattr(obj, nombre, por_defecto)


def _formatear_extraccion(extraccion: Any) -> str:
    if extraccion is None:
        return "sin extracción estructurada"

    partes: list[str] = []
    for clave, etiqueta in (
        ("provider_name", "proveedor"),
        ("proveedor", "proveedor"),
        ("subtotal", "subtotal"),
        ("tax", "impuestos"),
        ("impuestos", "impuestos"),
        ("total", "total"),
    ):
        valor = _leer_campo(extraccion, clave)
        if valor is not None and valor != "":
            partes.append(f"{etiqueta}={valor}")

    return ", ".join(partes) if partes else "extracción sin montos explícitos"


def construir_solicitud_lectura(resultado: Any) -> AgentRequest:
    """Construye un AgentRequest de solo lectura a partir de un ResultadoLectura o dict."""
    formato = _leer_campo(resultado, "formato_detectado", "desconocido")
    veredicto = _leer_campo(resultado, "veredicto_status", "sin_veredicto")
    coincide = _leer_campo(resultado, "coincide_con_guardado")
    error = _leer_campo(resultado, "error")
    checks_fallidos = _leer_campo(resultado, "checks_fallidos") or []
    pasos = _leer_campo(resultado, "pasos") or []
    extraccion = _leer_campo(resultado, "extraccion")

    resumen_pasos = ", ".join(
        f"{_leer_campo(p, 'escalon', '?')}:{_leer_campo(p, 'motor', '?')}({'ok' if _leer_campo(p, 'aceptado') else 'no'})"
        for p in pasos
    ) or "sin pasos"

    lineas = [
        "Analiza en modo de solo lectura este diagnóstico de lectura de comprobante:",
        f"- Formato detectado: {formato}",
        f"- Veredicto: {veredicto} (coincide_con_guardado={coincide})",
        f"- Checks fallidos: {', '.join(str(c) for c in checks_fallidos) if checks_fallidos else 'ninguno'}",
        f"- Pasos: {resumen_pasos}",
        f"- Extracción: {_formatear_extraccion(extraccion)}",
    ]
    if error:
        lineas.append(f"- Error reportado: {error}")

    lineas.append(
        "Si hay subtotal, impuestos y total, verifica su consistencia aritmética y resume el diagnóstico."
    )
    return AgentRequest(pregunta_o_caso="\n".join(lineas))


def diagnosticar_resultado_lectura(
    resultado: Any,
    *,
    runner: ExpenseAgentRunner | None = None,
) -> AgentDiagnosis:
    """Ejecuta el agente sobre un resultado de lectura sin realizar escrituras."""
    solicitud = construir_solicitud_lectura(resultado)
    agente = runner or ExpenseAgentRunner()
    try:
        return agente.run(solicitud)
    except Exception as exc:
        return AgentDiagnosis(
            estado="no_disponible",
            diagnostico=f"No se pudo completar el diagnóstico agéntico en lectura: {exc}",
            hallazgos=[],
            pasos_ejecutados=0,
        )
