"""NL-to-SQL de SOLO LECTURA.

El endpoint `POST /api/v1/ai-query/ask` recibe una pregunta y un `tenant_id`,
se la traduce a SQL con el servidor local de Ollama y la ejecuta. Este
modulo es el guardia: aqui vive todo lo que impide que el modelo —ia
o autenticacion comprometida— lea lo que no debe o escriba lo que no puede.

TRES CERRADURAS, EN ORDEN
-------------------------
1. **El modelo propone, el guardia dispone.** El SQL que devuelve Ollama
   NUNCA llega a la base sin pasar `validar_sql_solo_lectura`: una sola
   sentencia, que empieza por SELECT o WITH, sin palabras de escritura, sin
   comentarios que oculten nada y con el predicado del tenant OBLIGATORIO.
2. **El predicado del tenant se exige literal.** La consulta debe contener
   `company_id = '<uuid>'` con el UUID resuelto en el servidor. Sin el, se
   rechaza: una consulta generada que "olvido" el filtro es exactamente el
   fallo que abre los datos de una empresa a otra.
3. **La sesion va en READ ONLY.** En Postgres se abre una conexion con
   `default_transaction_read_only=on` (asyncpg server_settings): aunque las
   dos cerraduras anteriores fallaran, la base misma rechaza la escritura.

Tablas permitidas: SOLO las que llevan columna `company_id`. El resto no se
puede tocar, ni para leer. Ampliar la lista es una linea, pero es una
decision: toda tabla nueva que se permita debe cumplir la misma regla.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lista blanca de tablas: SOLO tablas con columna company_id.
# ---------------------------------------------------------------------------
TABLAS_PERMITIDAS = ("tickets", "bank_transactions", "reconciliations")

# Funciones/tokens de escritura y escape peligrosos. Un token que aparezca
# aqui rechaza la consulta entera. Es una lista NEGRA sobre tokens, no
# sobre subcadenas: `updated_at` no contiene `update` como token.
PALABRAS_PROHIBIDAS = {
    # Escritura y DDL
    "insert", "update", "delete", "merge", "replace", "upsert",
    "drop", "alter", "create", "truncate", "grant", "revoke",
    "into", "set", "vacuum", "analyze", "refresh",
    # Escape al sistema / red / archivos
    "attach", "detach", "pragma", "copy", "call", "do",
    "pg_read_file", "pg_ls_dir", "pg_sleep", "lo_import", "lo_export",
    "dblink", "set_config", "current_setting",
    # Control
    "begin", "commit", "rollback", "savepoint", "abort", "start",
}

_COMENTARIO_BLOQUE_RE = re.compile(r"/\*.*?\*/", re.S)
_COMENTARIO_LINEA_RE = re.compile(r"--[^\n]*")
_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_TOKEN_RE = re.compile(r"[a-z_][a-z0-9_]*")
_FROM_JOIN_RE = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_.]*)", re.I)
_CTE_RE = re.compile(r"([a-z_][a-z0-9_]*)\s*(?:\([^)]*\))?\s+as\s*\(", re.I)


def _sin_comentarios(sql: str) -> str:
    s = _COMENTARIO_BLOQUE_RE.sub(" ", sql)
    return _COMENTARIO_LINEA_RE.sub(" ", s)


def _sin_literales(sql: str) -> str:
    """Colapsa el contenido de los strings ('...' -> '').

    Sirve para analizar ESTRUCTURA sin que un 'DROP TABLE' escrito dentro
    de un texto suelte una falsa alarma —y para que un literal malicioso no
    apague un chequeo real.
    """
    return _LITERAL_RE.sub("''", sql)


def validar_sql_solo_lectura(sql: str, tenant_id: UUID) -> str:
    """Devuelve el SQL saneado (sin `;` final) o lanza ValueError descriptivo.

    No reescribe ni "corrige" el SQL: lo RECHAZA. Un guardia que edita la
    consulta para hacerla valida esta adivinando la intencion del modelo, y
    adivinar es exactamente lo que no se puede hacer con datos de terceros.
    """
    original = (sql or "").strip()
    if not original:
        raise ValueError("El modelo no devolvio ninguna consulta.")

    # Una sola sentencia: se permite el punto y coma FINAL, nunca uno en medio.
    cuerpo = original[:-1].rstrip() if original.endswith(";") else original
    con_comentarios = _sin_comentarios(cuerpo)
    estructura = _sin_literales(con_comentarios)

    if ";" in estructura:
        raise ValueError(
            "La consulta debe ser UNA sola sentencia. "
            "Un ';' en medio de la respuesta puede esconder una segunda instruccion."
        )

    tokens = _TOKEN_RE.findall(estructura.lower())
    if not tokens:
        raise ValueError("La consulta esta vacia despues de quitar los comentarios.")

    primero = tokens[0]
    if primero not in ("select", "with"):
        raise ValueError(
            f"Solo se permiten consultas de lectura (SELECT/WITH). "
            f"La respuesta empieza por '{primero}'."
        )

    prohibidas = sorted({t for t in tokens if t in PALABRAS_PROHIBIDAS})
    if prohibidas:
        raise ValueError(
            "La consulta contiene instrucciones de escritura o de escape "
            f"({', '.join(prohibidas)}). Solo se puede leer."
        )

    # Tablas referenciadas: las de FROM/JOIN, menos los alias de CTE.
    ctes = {m.group(1).lower() for m in _CTE_RE.finditer(estructura)}
    referenciadas = {
        m.group(1).lower().split(".")[-1] for m in _FROM_JOIN_RE.finditer(estructura)
    }
    desconocidas = referenciadas - set(TABLAS_PERMITIDAS) - ctes
    if desconocidas:
        raise ValueError(
            "La consulta toca tablas fuera de la lista blanca "
            f"({', '.join(sorted(desconocidas))}). "
            f"Tablas permitidas: {', '.join(TABLAS_PERMITIDAS)}."
        )
    if not referenciadas:
        raise ValueError("La consulta no lee ninguna tabla conocida.")

    # EL PREDICADO DEL TENANT ES OBLIGATORIO Y LITERAL.
    # Se busca en `con_comentarios` y no en `estructura` porque el UUID vive
    # dentro de un string ('...') y en `estructura` los strings son ''.
    patron_tenant = rf"company_id\s*=\s*'?{re.escape(str(tenant_id))}'?"
    if not re.search(patron_tenant, con_comentarios, re.I):
        raise ValueError(
            "La consulta no incluye el filtro del tenant "
            f"(company_id = '{tenant_id}'). Sin ese predicado se rechaza aunque "
            "por lo demas sea un SELECT correcto."
        )

    return cuerpo.strip()


def construir_prompt(pregunta: str, tenant_id: UUID) -> list[dict[str, str]]:
    sistema = """Eres un traductor de preguntas a SQL de SOLO LECTURA para PostgreSQL.

Tablas permitidas (no existe ninguna otra):
- tickets(id UUID, company_id UUID, provider_name TEXT, provider_tax_id TEXT,
  total_amount NUMERIC(12,2), tax_amount NUMERIC(12,2), ieps_amount NUMERIC(12,2),
  subtotal NUMERIC(12,2), expense_date DATE, category TEXT, extraction_status TEXT,
  confidence NUMERIC(4,3), source_type TEXT, created_at TIMESTAMPTZ)
- bank_transactions(id UUID, company_id UUID, transaction_date DATE,
  amount NUMERIC(12,2), description TEXT, reference TEXT, is_reconciled BOOLEAN,
  created_at TIMESTAMPTZ)
- reconciliations(id UUID, company_id UUID, ticket_id UUID,
  bank_transaction_id UUID, match_status TEXT, matched_at TIMESTAMPTZ)

REGLAS, sin excepcion:
1. UNA sola sentencia, SELECT o WITH. Nada de INSERT/UPDATE/DELETE/DROP/
   ALTER/CREATE/TRUNCATE ni de set_config().
2. TODA tabla referenciada DEBE llevar el predicado exacto
   `company_id = '<tenant_id>'`. No lo omitas, no lo envuelvas en OR,
   no lo sustituyas por otra condicion.
3. Sin comentarios, sin punto y coma final, sin backticks, sin explicacion.
4. LIMIT 200 salvo que la pregunta pida explicitamente otra cota menor.
5. Responde UNICAMENTE con el SQL. Empieza por SELECT o WITH.

EJEMPLO:
Pregunta: cuanto se ha gastado este mes por categoria?
SQL: SELECT category, sum(total_amount) AS total FROM tickets WHERE company_id = '<tenant_id>' AND expense_date >= date_trunc('month', current_date) GROUP BY category ORDER BY total DESC"""

    usuario = f"""tenant_id: {tenant_id}
Pregunta: {pregunta}"""

    return [
        {"role": "system", "content": sistema},
        {"role": "user", "content": usuario},
    ]


def extraer_sql(respuesta: str) -> str:
    """Saca el SQL de la respuesta del modelo.

    Los modelos devuelven a veces ```sql ... ``` o una frase alrededor. Si hay
    un bloque de codigo se toma ESE (la prosa puede mentir); sin bloque se
    toma el texto entero y el guardia decide.
    """
    texto = (respuesta or "").strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", texto, re.S | re.I)
    if m:
        return m.group(1).strip()
    return texto


async def preguntar_al_modelo(pregunta: str, tenant_id: UUID) -> str:
    """Llama al Ollama local (compatible con /v1) y devuelve el SQL crudo."""
    base = settings.OLLAMA_BASE_URL.rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    url = f"{base}/chat/completions"

    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": construir_prompt(pregunta, tenant_id),
        # SQL directo al grano: temperatura casi nula.
        "temperature": 0.1,
        "max_tokens": 500,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as cliente:
            r = await cliente.post(url, json=payload)
            r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        cuerpo = exc.response.text[:500] if exc.response is not None else ""
        logger.warning("Ollama respondio %s: %s", exc.response.status_code, cuerpo)
        raise RuntimeError(
            f"El servidor local de Ollama respondio {exc.response.status_code}. "
            f"Cuerpo: {cuerpo}"
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("No se pudo contactar a Ollama en %s: %s", url, exc)
        raise RuntimeError(
            f"No se pudo contactar al servidor local de Ollama en {url}. "
            "Comprueba que esta levantado (ver docker-compose.ollama.yml) y "
            "que OLLAMA_BASE_URL apunta a el."
        ) from exc

    try:
        return r.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, ValueError) as exc:
        raise RuntimeError(
            "La respuesta de Ollama no trae choices[0].message.content: "
            f"{str(r.text)[:200]}"
        ) from exc


def _serializable(valor: Any) -> Any:
    if isinstance(valor, (Decimal, float)):
        # str y no float: son importes. En Postgres llegan como Decimal; en
        # SQLite el NUMERIC llega como float de todas formas y str(float)
        # da la forma mas corta que redondea bien (str(123.45) == '123.45').
        return str(valor)
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, UUID):
        return str(valor)
    return valor


_engine_solo_lectura = None


def _engine_readonly(bind):
    """Conexion asyncpg en READ ONLY, la tercera cerradura.

    `default_transaction_read_only=on` lo impone el servidor en CADA
    transaccion de estas conexiones: aunque el guardia tuviera un agujero,
    una escritura devolveria error 25P02 en vez de ejecutar.

    Se construye con la URL del bind de la sesion, no con
    `settings.DATABASE_URL`: en tests la sesion apunta a SQLite (y esta
    funcion no se llama), y en produccion apunta al mismo Postgres que el
    resto de la peticion. Dos fuentes de verdad sobre "a que base me
    conecto" son dos bases que divergen.
    """
    global _engine_solo_lectura
    if _engine_solo_lectura is None:
        from sqlalchemy.ext.asyncio import create_async_engine

        _engine_solo_lectura = create_async_engine(
            str(bind.url),
            connect_args={
                "server_settings": {
                    "default_transaction_read_only": "on",
                    "statement_timeout": "6000",
                }
            },
            pool_size=2,
            max_overflow=0,
        )
    return _engine_solo_lectura


def _es_postgres(db) -> bool:
    """True si la SESION (no el settings global) habla con Postgres.

    El bind es la verdad: la app puede apuntar a un `DATABASE_URL` de
    Postgres mientras el test monta SQLite sobre un settings que dice
    postgres — mirar el settings ejecutaria contra la base real en la
    suite.
    """
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    return dialect is not None and dialect.name == "postgresql"


async def ejecutar_consulta(sql: str, max_filas: int, db) -> list[dict[str, Any]]:
    """Ejecuta el SQL ya validado y devuelve hasta max_filas+1 diccionarios.

    La cota se aplica envolviendo la consulta en un subselect con LIMIT:
    cortar en Python dejaria pasar un SELECT sin cota que barra la tabla
    entera antes de que nadie mire el resultado.
    """
    cota = max_filas + 1
    encajonada = f"SELECT * FROM (\n{sql}\n) AS nlq LIMIT {int(cota)}"

    from sqlalchemy import text

    if _es_postgres(db):
        async with _engine_readonly(db.bind).connect() as conn:
            resultado = await conn.execute(text(encajonada))
            filas = resultado.mappings().all()
    else:
        # SQLite (tests): no hay server_settings. El guardia sigue mandando.
        resultado = await db.execute(text(encajonada))
        filas = resultado.mappings().all()

    return [{k: _serializable(v) for k, v in dict(f).items()} for f in filas]
