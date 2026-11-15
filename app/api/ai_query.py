"""Router de consultas en lenguaje natural (NL-to-SQL seguro).

`POST /api/v1/ai-query/ask`

Lo que hace: pregunta -> SQL de solo lectura (Ollama local) -> guardia ->
ejecucion en READ ONLY -> filas.

Lo que NO hace: escribir, tocar tablas fuera de la lista blanca ni ampliar
la empresa de quien llama. El `tenant_id` del cuerpo pasa por
`resolver_empresa`: es la misma cerradura que usa el resto de la API
(P0-1) — la empresa del usuario decide, el cuerpo solo sugiere.

Un SQL rechazado por el guardia es 400 (y no 200 con `error` dentro): si el
modelo intento algo que no puede hacer, quien hizo la pregunta tiene que
enterarse. Un 200 esconderia el intento.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import UsuarioActual, resolver_empresa
from app.core.database import get_db
from app.schemas.ai_query import AiQueryRequest, AiQueryResponse
from app.services import consulta_nl
from app.services.auditoria import registrar_auditoria

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-query", tags=["AI Query"])


@router.post(
    "/ask",
    response_model=AiQueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Pregunta en lenguaje natural sobre los datos de tu empresa (solo lectura)",
)
async def ask(
    pedido: AiQueryRequest,
    usuario: UsuarioActual,
    db: AsyncSession = Depends(get_db),
) -> AiQueryResponse:
    # La empresa del cuerpo NUNCA decide: decide la del usuario autenticado.
    empresa = await resolver_empresa(usuario, pedido.tenant_id, db)

    try:
        crudo = await consulta_nl.preguntar_al_modelo(pedido.pregunta, empresa)
    except RuntimeError as exc:
        # Ollama caido o respondiendo mal: no es culpa de quien pregunta,
        # pero tampoco hay resultado. 503 con el motivo exacto.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    sql_propuesto = consulta_nl.extraer_sql(crudo)
    try:
        sql = consulta_nl.validar_sql_solo_lectura(sql_propuesto, empresa)
    except ValueError as exc:
        # 400 a proposito (y no 200): el modelo intento algo que no puede
        # hacer. El mensaje dice QUE intento.
        logger.info("ai_query.ask rechazado: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El SQL del modelo fue rechazado: {exc} | SQL generado: {sql_propuesto[:300]}",
        ) from exc

    try:
        filas = await consulta_nl.ejecutar_consulta(sql, pedido.max_filas, db)
    except Exception as exc:
        # La consulta paso el guardia y la base la rechazo igual (por
        # ejemplo SQL sintacticamente raro con un CTE recursivo). Se dice
        # que fallo la ejecucion, no se finge una respuesta vacia.
        logger.warning("ai_query.ask fallo la ejecucion: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"La consulta validada no se pudo ejecutar: {exc}",
        ) from exc

    truncado = len(filas) > pedido.max_filas
    filas = filas[: pedido.max_filas]
    columnas = list(filas[0].keys()) if filas else []

    # La auditoria dice QUE se pregunto. La pregunta puede llevar datos
    # personales; la bitacora registra la intencion, nunca la respuesta
    # cruda del modelo.
    await registrar_auditoria(
        accion="ai_query_ask",
        modulo="ai_query",
        usuario_id=str(usuario.id),
        detalles={
            "pregunta": pedido.pregunta,
            "tenant": str(empresa),
            "filas": len(filas),
            "truncado": truncado,
        },
        rol=usuario.perfil,
    )

    return AiQueryResponse(
        pregunta=pedido.pregunta,
        tenant_id=empresa,
        sql=sql,
        columnas=columnas,
        filas=filas,
        cantidad_filas=len(filas),
        truncado=truncado,
        modelo=settings.OLLAMA_MODEL,
    )
