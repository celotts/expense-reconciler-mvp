#!/usr/bin/env python3
"""Genera la coleccion de Insomnia desde el OpenAPI real del servidor.

Por que un generador y no un archivo escrito a mano: la coleccion anterior
estaba exportada el 2025-01-21 y tenia 25 peticiones de las 57 que hoy expone
la API. No es que estuviera mal escrita — es que el archivo no sabe que hay
rutas nuevas, y por eso nadie se entero. Un archivo que se regenera con
`make up` ejecutado no se queda viejo solo.

    python3 scripts/generar_insomnia.py                     # contra 127.0.0.1:8000
    python3 scripts/generar_insomnia.py --base-url http://localhost:8000
    python3 scripts/generar_insomnia.py --spec openapi.json --salida otro.json
    python3 scripts/generar_insomnia.py --check             # no escribe, solo compara

QUE HACE Y QUE NO

Lee el OpenAPI de la API viva y arma las peticiones: metodo, ruta, query
params y, para las que lo tienen, el cuerpo con TODOS los campos del schema
y sus valores de ejemplo. Lo que no hace es inventar la logica: un endpoint
nuevo aparece solo porque el servidor lo declara.

LO QUE NO VA EN EL ARCHIVO: LA CONTRASENA

El environment sale con `password` vacio a proposito. Este archivo esta en git
con remoto en GitHub, y una contrasena escrita ahi es una contrasena
publicada. Se pega una vez en el environment de Insomnia y ya. El resto de
variables tampoco llevan secretos: `token` se llena solo con el script del
endpoint de login.

El mismo criterio que ya aplica el repo a los `.env` (AGENTS.md: "una sola URL con
contraseña"), extendido al cliente de API.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any

# Cuando se exporta con formato 4, la fecha decide como Insomnia lo interpreta.
# Fijarla a la del dia hace el diff legible: si el archivo cambia solo porque se
# regenero, el unico cambio real son las rutas que cambiaron.
FECHA_EXPORT = "2026-10-02T00:00:00.000Z"
VERSION_EXPORT = "2026-10-02"

BASE_POR_DEFECTO = "http://127.0.0.1:8000"
SALIDA_POR_DEFECTO = "expense-reconciler-insomnia.json"

WORKSPACE_ID = "wrk_expense_reconciler"
ENV_ID = "env_base"
COLLECTION_ID = "col_api"

# ---------------------------------------------------------------------------
# Ejemplos por nombre de campo.
#
# El orden de resolucion de un valor es: este diccionario, luego el enum del
# schema, luego el default, y al final el tipo. Los nombres genericos ("name",
# "notes") se resuelven por tipo; los que dependen del dominio ("provider_name")
# no se pueden deducir, asi que van aqui.
# ---------------------------------------------------------------------------
EJEMPLOS: dict[str, Any] = {
    # Identidad
    "email": "{{ email }}",
    "password": "{{ password }}",
    "company_id": "{{ company_id }}",
    "ticket_id": "{{ ticket_id }}",
    "bank_transaction_id": "{{ bank_transaction_id }}",
    "reconciliation_id": "{{ reconciliation_id }}",
    "mapping_id": "{{ mapping_id }}",
    "file_id": "{{ file_id }}",
    # Inventario. La ruta del parametro y el nombre del campo coinciden, asi que
    # una sola entrada de EJEMPLOS cubre los dos: en la URL y en el cuerpo.
    "compra_id": "{{ compra_id }}",
    "compra_item_id": "{{ compra_item_id }}",
    # El nombre del path param es `item_id`, no `compra_item_id`. Se agrega aqui
    # para que la URL quede `{{ compra_item_id }}` y no una variable que no
    # existe en el environment.
    "item_id": "{{ compra_item_id }}",
    "ticket_ids": ["{{ ticket_id }}"],
    # Dinero: todos los importes viajan como texto. `Decimal` no acepta
    # 0.1 + 0.2 == 0.3, y el schema los declara anyOf[number, string]: mandarlos
    # como numero mete error de redondeo en la base. Texto, siempre.
    "total_amount": "4094.80",
    "tax_amount": "582.62",
    "amount": "1500.00",
    "amount_tolerance": "1.00",
    "date_tolerance_days": 3,
    # Fechas
    "expense_date": "2026-09-15",
    "transaction_date": "2026-09-15",
    "date_from": "2026-01-01",
    "date_to": "2026-12-31",
    # Comprobantes
    "provider_name": "OXXO",
    "provider_tax_id": "PSA430615KJ9",
    "category": "ALIMENTACION",
    "raw_text": "Compra en tienda de autoservicio",
    # Movimientos bancarios
    "description": "Compra con tarjeta",
    "reference": "AUTOCARD ****1234",
    # Veredictos
    "action": "approve",
    "correct": True,
    "notes": "Revisado contra el papel",
    "match_status": "PERFECT",
    "campos_incorrectos": ["total_amount"],
    # companies
    "name": "Mi Taqueria",
    "tax_id": "MTA123456789",
    # conciliacion / exportacion
    "software_name": "CONTPAQI",
    "column_mappings": {
        "Fecha": "expense_date",
        "Proveedor": "provider_name",
        "Total": "total_amount",
    },
    # escaner
    "reprocesar": False,
    "solo_pendientes": False,
    "file_type": "pdf",
    "motivo": "El papel estaba borroso; subo la foto mejor",
    # importacion de CSV
    "date_column": "fecha",
    "amount_column": "importe",
    "description_column": "concepto",
    "reference_column": "referencia",
    "date_format": "%d/%m/%Y",
    "decimal_separator": ".",
    "thousands_separator": ",",
    "encoding": "utf-8",
}

# Valores por tipo, para lo que no este en EJEMPLOS.
POR_TIPO: dict[str, Any] = {
    "string": "texto",
    "integer": 1,
    "number": 1.0,
    "boolean": False,
}

# Campos que son un archivo. En Insomnia van como param de tipo `file`, y el
# cuerpo entero pasa a ser multipart/form-data.
CAMPOS_ARCHIVO = {"file"}

# Prefijo de ruta -> carpeta, ya SIN el prefijo de la v1.
#
# El prefijo se quita antes de comparar porque el OpenAPI declara las rutas
# completas ("/api/v1/tickets/"), no relativas al router. Comparar contra
# "/tickets" sin quitarlo antes manda TODAS las rutas a "Otros", y la
# coleccion queda en una sola carpeta sin descubrir el error: parece que
# funciono.
CARPETAS: list[tuple[str, str]] = [
    ("/auth", "Auth"),
    ("/dashboard", "Dashboard"),
    ("/categorias", "Categorias"),
    ("/companies", "Companies"),
    ("/tickets", "Tickets"),
    ("/scan", "Scan"),
    ("/bank-transactions", "Bank Transactions"),
    ("/reconciliations", "Reconciliations"),
]

# Lo que no cae bajo /api/v1. Ahora mismo es solo /health, que cuelga del app
# y no del router de la v1 (ver app/main.py).
PREFIJO_V1 = "/api/v1"
CARPETA_SIN_V1 = "Sistema"

# Values de query param que no salen solos del schema.
QUERY_EJEMPLOS: dict[str, Any] = {
    "company_id": "{{ company_id }}",
    "match_status": "PERFECT",
    "extraction_status": "REQUIERE_REVISION",
    "categoria": "ALIMENTACION",
    "estado": "PROCESADO",
    "status": "PENDIENTE",
    "columns": "Fecha,Proveedor,Total,EstatusConciliacion",
    "only_reconciled": "true",
    "solo_pendientes": "false",
    "sin_categoria": "false",
    "con_categoria": "false",
    "only_open": "false",
    "meses": "12",
}

# Etiquetas de las peticiones, para que el listado no sea 57 rutes crudas.
NOMBRES: dict[tuple[str, str], str] = {
    ("POST", "/auth/login"): "Login  <- TIENE que ir primero",
    ("GET", "/auth/me"): "Quien soy (token valido)",
    ("GET", "/health"): "Health",
    ("GET", "/companies/"): "Listar Empresas  <- fija {{ company_id }} sola",
    ("GET", "/tickets/"): "Listar Tickets",
    ("POST", "/scan/"): "Escanear carpeta",
    ("GET", "/scan/files"): "Archivos vistos  <- fija {{ file_id }}",
    ("GET", "/tickets/accuracy"): "Exactitud por origen",
    ("GET", "/tickets/review-queue"): "Cola de revision",
    ("GET", "/tickets/spot-check"): "Muestreo 5%",
    ("POST", "/reconciliations/run"): "Correr conciliacion",
    ("GET", "/reconciliations/export/excel"): "Exportar Excel",
    ("GET", "/reconciliations/export/contpaqi"): "Exportar CONTPAQI",
    ("GET", "/reconciliations/export/generic"): "Exportar generico",
}

# Los tres scripts que hacen que la coleccion se sustains sola.
# ---------------------------------------------------------------------------
# Login: guarda el token en el environment. Sin esto, las otras 55 peticiones
# fallan con 401 y no hay forma de arreglarlo sin pegar el token a mano.
SCRIPT_LOGIN = """const env = insomnia.environment;
const pruebas = [];

pruebas.push({
  name: 'responde 200',
  fn: () => {
    if (response.code !== 200) {
      throw new Error('HTTP ' + response.code + ': ' + response.text.slice(0, 200));
    }
    return true;
  },
});

pruebas.push({
  name: 'guarda el token en el environment',
  fn: () => {
    const d = JSON.parse(response.text);
    if (!d.access_token) {
      throw new Error('la respuesta no trae access_token');
    }
    env.set('token', d.access_token);
    console.log('token guardado. Caduca en ' + d.expires_in + ' s (' +
                Math.round(d.expires_in / 3600) + ' h).');
    return true;
  },
});

return pruebas;"""

# Companies: el environment arranca sin company_id, y casi todas las rutas lo
# necesitan. Sin esto, el primer GET /tickets/?company_id= manda la variable
# vacia y el filtro se ignora en silencio, devolviendo datos de otra empresa.
SCRIPT_COMPANIES = """const env = insomnia.environment;
const pruebas = [];

pruebas.push({
  name: 'responde 200 y trae al menos una empresa',
  fn: () => {
    const d = JSON.parse(response.text);
    const lista = Array.isArray(d) ? d : (d.items || d.archivos || []);
    if (!lista.length) {
      console.warn('No hay empresas. Crea una con POST /companies/ primero.');
      return true;
    }
    env.set('company_id', lista[0].id);
    console.log('company_id = ' + lista[0].id + '  (' + lista[0].name + ')');
    return true;
  },
});

return pruebas;"""

# Scan files: la respuesta aqui NO es un array pelado, es un sobre con total.
# Un `Array.isArray` a secas daria false y el file_id nunca se llenaria.
SCRIPT_SCAN_FILES = """const env = insomnia.environment;
const pruebas = [];

pruebas.push({
  name: 'fija {{ file_id }} con el primer archivo',
  fn: () => {
    const d = JSON.parse(response.text);
    const lista = Array.isArray(d) ? d : (d.archivos || d.items || []);
    if (!lista.length) {
      console.warn('El escaner no ha visto archivos. Corre POST /scan/ primero.');
      return true;
    }
    env.set('file_id', lista[0].id);
    console.log('file_id = ' + lista[0].id + '  (' + lista[0].relative_path + ')');
    return true;
  },
});

return pruebas;"""

# Tickets: deja {{ ticket_id }} listo para las rutas /tickets/{ticket_id}.
SCRIPT_TICKETS = """const env = insomnia.environment;
const pruebas = [];

pruebas.push({
  name: 'fija {{ ticket_id }} con el primer ticket',
  fn: () => {
    const d = JSON.parse(response.text);
    const lista = Array.isArray(d) ? d : (d.items || []);
    if (!lista.length) {
      console.warn('No hay tickets para esta empresa. Sube uno con ' +
                   'POST /tickets/extract-and-create.');
      return true;
    }
    env.set('ticket_id', lista[0].id);
    console.log('ticket_id = ' + lista[0].id);
    return true;
  },
});

return pruebas;"""

SCRIPT_BANK = """const env = insomnia.environment;
const pruebas = [];

pruebas.push({
  name: 'fija {{ bank_transaction_id }} con el primer movimiento',
  fn: () => {
    const d = JSON.parse(response.text);
    const lista = Array.isArray(d) ? d : (d.items || []);
    if (!lista.length) {
      console.warn('No hay movimientos bancarios para esta empresa.');
      return true;
    }
    env.set('bank_transaction_id', lista[0].id);
    console.log('bank_transaction_id = ' + lista[0].id);
    return true;
  },
});

return pruebas;"""

SCRIPT_RECON = """const env = insomnia.environment;
const pruebas = [];

pruebas.push({
  name: 'fija {{ reconciliation_id }} con la primera conciliacion',
  fn: () => {
    const d = JSON.parse(response.text);
    const lista = Array.isArray(d) ? d : (d.items || []);
    if (!lista.length) {
      console.warn('No hay conciliaciones. Corre POST /reconciliations/run.');
      return true;
    }
    env.set('reconciliation_id', lista[0].id);
    console.log('reconciliation_id = ' + lista[0].id);
    return true;
  },
});

return pruebas;"""

DESCRIPCION_ENTORNO = """Variables del environment.

`password` va VACIA a proposito: este archivo esta versionado en git con remoto
en GitHub, y una contrasena escrita aqui queda publicada. Pegala una vez en
Insomnia (o escribela en el environment local, que no se exporta).

Lo demas se llena solo, y no hace falta tocarlo a mano:
  token                  <- POST /auth/login
  company_id             <- GET /companies/
  ticket_id              <- GET /tickets/
  bank_transaction_id    <- GET /bank-transactions/
  reconciliation_id      <- GET /reconciliations/
  file_id                <- GET /scan/files
  compra_id              <- GET /inventario/compras
  compra_item_id         <- GET /inventario/cola (pegar el id de una linea)

El orden importa: login, companies, y despues todo lo demas. El token dura 8
horas (ACCESS_TOKEN_EXPIRE_MINUTES); cuando devuelva 401 por caducado, repite
el login y solo eso.

AVISO SOBRE LOS IDs VACIOS

Si `{{ company_id }}` esta vacia, `GET /companies/{{ company_id }}` se manda
como `GET /companies/` y devuelve 200 con la LISTA, no con el detalle. No es un
error: es otra ruta. Lo mismo con `ticket_id`, `reconciliation_id` y `file_id`.
Por eso los scripts de listado llenan la variable antes de usar las rutas de
detalle: si ves una lista donde esperabas un objeto, casi siempre es que la
variable quedo vacia."""


def _deref(schema: dict, comps: dict) -> dict:
    """Resuelve `$ref` hasta el schema real."""
    visto = 0
    while schema and "$ref" in schema and visto < 20:
        nombre = schema["$ref"].split("/")[-1]
        schema = comps.get(nombre, {})
        visto += 1
    return schema or {}


def _valor(campo: str, schema: dict, comps: dict) -> Any:
    """El valor de ejemplo de un campo.

    La precedencia es lo que decide si el ejemplo es util o ruido: un valor
    escrito a mano gana siempre sobre el tipo, porque "OXXO" ensena mas que
    "texto". Despues, el enum, porque un enum ya es la lista de lo valido.
    Despues el default del schema. Y el tipo al final.
    """
    if campo in EJEMPLOS:
        return EJEMPLOS[campo]

    schema = _deref(schema, comps)
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    if schema.get("default") is not None:
        return schema["default"]

    for rama in ("anyOf", "oneOf"):
        for opcion in schema.get(rama) or []:
            real = _deref(opcion, comps)
            if real.get("type") != "null":
                return _valor(campo, real, comps)

    tipo = schema.get("type", "string")
    if tipo == "array":
        return [_valor(campo, schema.get("items", {}), comps)]
    return POR_TIPO.get(tipo, "texto")


def _cuerpo_json(op: dict, comps: dict) -> dict:
    """Arma el cuerpo JSON de una peticion a partir de su schema.

    Se meten TODOS los campos, no solo los requeridos. En un cliente de API
    mandas el minimo que acepta; en una plantilla de Insomnia mandas todo lo
    que existe, para que al pasar el mouse por encima se vea la superficie
    completa del endpoint.
    """
    rb = op.get("requestBody")
    if not rb:
        return {}

    contenido = rb.get("content") or {}
    llave_json = next((k for k in contenido if "json" in k), None)
    if llave_json is None:
        return {}

    schema = _deref(contenido[llave_json].get("schema", {}), comps)
    props = schema.get("properties") or {}
    if not props:
        return {}

    return {campo: _valor(campo, s, comps) for campo, s in props.items()}


def _es_multipart(op: dict) -> bool:
    contenido = (op.get("requestBody") or {}).get("content") or {}
    return any("multipart" in k for k in contenido)


def _params_multipart(op: dict, comps: dict) -> list[dict]:
    contenido = (op.get("requestBody") or {}).get("content") or {}
    llave = next((k for k in contenido if "multipart" in k), None)
    if llave is None:
        return []

    schema = _deref(contenido[llave].get("schema", {}), comps)
    salida = []
    for campo, sub in (schema.get("properties") or {}).items():
        valor = _valor(campo, sub, comps)
        if campo in CAMPOS_ARCHIVO:
            # El path lo edita quien use la plantilla. Poner uno de ejemplo
            # seria peor que dejarlo vacio: un archivo que no existe da un 422
            # que se lee como "el endpoint esta roto".
            salida.append(
                {"name": campo, "type": "file", "fileName": "", "description": ""}
            )
        else:
            texto = "" if valor is None else (
                valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False)
            )
            salida.append(
                {"name": campo, "value": texto, "type": "text", "description": ""}
            )
    return salida


def _query(op: dict) -> list[dict]:
    salida = []
    for p in op.get("parameters") or []:
        if p.get("in") != "query":
            continue
        nombre = p["name"]
        if nombre in QUERY_EJEMPLOS:
            valor = QUERY_EJEMPLOS[nombre]
        else:
            schema = p.get("schema") or {}
            if schema.get("default") is not None:
                valor = schema["default"]
            else:
                valor = ""
        salida.append(
            {
                "name": nombre,
                "value": str(valor),
                "disabled": not bool(valor),
                "description": "",
            }
        )
    return salida


SCRIPT_POR_RUTA: dict[tuple[str, str], str] = {
    ("POST", "/auth/login"): SCRIPT_LOGIN,
    ("GET", "/companies/"): SCRIPT_COMPANIES,
    ("GET", "/tickets/"): SCRIPT_TICKETS,
    ("GET", "/bank-transactions/"): SCRIPT_BANK,
    ("GET", "/reconciliations/"): SCRIPT_RECON,
    ("GET", "/scan/files"): SCRIPT_SCAN_FILES,
}

# Rutas sin auth. Todo lo demas lo exige el router entero
# (app/api/api_router.py), asi que en la coleccion se pone a nivel de carpeta y
# estas dos lo desactivan.
SIN_AUTH: set[tuple[str, str]] = {
    ("POST", "/auth/login"),
    ("GET", "/health"),
}


def _carpeta_de(path: str) -> str:
    if not path.startswith(PREFIJO_V1):
        return CARPETA_SIN_V1
    relativa = path[len(PREFIJO_V1):] or "/"
    for prefijo, nombre in CARPETAS:
        if relativa == prefijo or relativa.startswith(prefijo + "/"):
            return nombre
    return CARPETA_SIN_V1


def _nombre_de(metodo: str, path: str, op: dict) -> str:
    if (metodo, path) in NOMBRES:
        return NOMBRES[(metodo, path)]
    return op.get("summary") or path.rsplit("/", 1)[-1] or path


def _plantillas(path: str) -> str:
    """Convierte `{ticket_id}` en `{{ ticket_id }}`.

    Las dos formas funcionan en Insomnia, pero no se comportan igual. `{x}` es
    sintaxis de path variable: Insomnia la resuelve y la lista en su pestana de
    parametros, y si la variable no existe la decia por conflicto. `{{ x }}` es
    la plantilla general, que resuelve en cualquier parte de la URL.

    Aqui se usa la segunda por una razon concreta: con `{ticket_id}` vacia, la
    ruta se manda igual y el 404 o el 200 con lista llegan sin que nada avise.
    Con `{{ ticket_id }}` se ve el hueco en la barra de direcciones antes de
    mandar.
    """
    return re.sub(r"\{(\w+)\}", r"{{ \1 }}", path)


def _auth_heredada() -> dict:
    return {"type": "bearer", "token": "{{ token }}"}


def construir(spec: dict) -> dict:
    """Arma el export completo de Insomnia a partir del OpenAPI."""
    comps = (spec.get("components") or {}).get("schemas") or {}
    ahora = 1759353600000  # 2026-10-02T00:00:00Z

    recursos: list[dict] = [
        {
            "_id": WORKSPACE_ID,
            "parentId": None,
            "modified": ahora,
            "created": ahora,
            "name": "Expense Reconciler MVP",
            "description": "Coleccion generada por scripts/generar_insomnia.py",
            "scope": "collection",
            "_type": "workspace",
        },
        {
            "_id": ENV_ID,
            "parentId": WORKSPACE_ID,
            "modified": ahora,
            "created": ahora,
            "name": "Base Environment",
            "description": DESCRIPCION_ENTORNO,
            "data": {
                "api_root": BASE_POR_DEFECTO,
                "base_url": BASE_POR_DEFECTO + "/api/v1",
                "email": "insomnia@test.mx",
                "password": "",
                "token": "",
                "company_id": "",
                "ticket_id": "",
                "bank_transaction_id": "",
                "reconciliation_id": "",
                "mapping_id": "",
                "file_id": "",
                "compra_id": "",
                "compra_item_id": "",
            },
            "dataOrder": {
                key: idx for idx, key in enumerate(
                    [
                        "api_root", "base_url", "email", "password", "token",
                        "company_id", "ticket_id", "bank_transaction_id",
                        "reconciliation_id", "mapping_id", "file_id",
                        "compra_id", "compra_item_id",
                    ]
                )
            },
            "scope": "environment",
            "_type": "environment",
        },
        {
            "_id": COLLECTION_ID,
            "parentId": WORKSPACE_ID,
            "modified": ahora,
            "created": ahora,
            "name": "API v1",
            "description": (
                "Bearer token a nivel de carpeta: hereda el {{ token }} que deja "
                "el login. Solo /auth/login y /health lo apagan."
            ),
            "auth": _auth_heredada(),
            "environment": {"id": ENV_ID},
            "metaSortKey": -2000,
            "_type": "request_group",
        },
    ]

    # Se ordena POR CARPETA primero y despues por ruta. Sin la carpeta como
    # primera clave, el sort global deja el nombre de carpeta oscilando
    # (auth, companies, auth, tickets...) y el codigo de abajo, que solo emite
    # la carpeta cuando cambia el nombre, crea 20 carpetas repetidas en vez de
    # 9. Las peticiones quedan bien asignadas igual: por eso el bug no se ve
    # mirando una peticion, se ve en el arbol.
    #
    # Dentro de cada router, las rutas literales van antes que las que llevan
    # {parametro}. Es el orden de FastAPI, y el que hace falta para leer la
    # lista con la misma logica con la que el servidor resuelve: el primer
    # match gana.
    #
    # "Sistema" va al final: /health no pertenece a la v1.
    orden_carpetas = {nombre: i for i, (_, nombre) in enumerate(CARPETAS)}
    orden_carpetas[CARPETA_SIN_V1] = len(orden_carpetas)

    def clave(path: str) -> tuple:
        carpeta = _carpeta_de(path)
        return (
            orden_carpetas.get(carpeta, 999),
            path.count("{"),          # literales antes que {parametro}
            path.count("/"),          # la raiz antes que las hijas
            path,
        )

    # (carpeta, ruta, metodo, operacion) en orden de aparicion
    plano: list[tuple[str, str, str, dict]] = []
    for path in sorted(spec.get("paths", {}), key=clave):
        ops = spec["paths"][path]
        for metodo in ("get", "post", "patch", "put", "delete"):
            if metodo in ops:
                plano.append((_carpeta_de(path), path, metodo.upper(), ops[metodo]))

    salida: list[dict] = []
    carpeta_actual = None
    carpeta_actual_id = None
    carpeta_counter = 0
    request_counter = 0

    for nombre_carpeta, path, metodo, op in plano:
        if nombre_carpeta != carpeta_actual:
            carpeta_actual = nombre_carpeta
            carpeta_counter += 1
            carpeta_actual_id = f"fld_{carpeta_counter:02d}"
            salida.append({
                "_id": carpeta_actual_id,
                "parentId": COLLECTION_ID,
                "modified": ahora,
                "created": ahora,
                "name": nombre_carpeta,
                "environment": {},
                "metaSortKey": -1000 + carpeta_counter,
                "collectionId": COLLECTION_ID,
                "auth": _auth_heredada(),
                "_type": "request_group",
            })

        request_counter += 1
        rid = f"req_{request_counter:02d}"
        multipart = _es_multipart(op)
        cuerpo = _cuerpo_json(op, comps)
        headers: list[dict] = []

        if multipart:
            body = {
                "mimeType": "multipart/form-data",
                "params": _params_multipart(op, comps),
            }
        elif cuerpo:
            body = {
                "mimeType": "application/json",
                "text": json.dumps(cuerpo, indent=2, ensure_ascii=False),
            }
            headers.append({"name": "Content-Type", "value": "application/json"})
        else:
            body = {"mimeType": "application/json", "text": ""}

        # `base_url` YA trae el /api/v1 (lo dice su nombre). La ruta del OpenAPI
        # tambien viene con el. Sumar las dos produce
        # /api/v1/api/v1/tickets/, que es un 404 limpio y no un error de
        # sintaxis: el cliente cree que esta calling la API y la API dice que
        # no existe. Es la misma trampa que AGENTS.md documenta para
        # `api_router.py`, vista desde el otro lado del cable.
        if path.startswith(PREFIJO_V1):
            url = "{{ base_url }}" + _plantillas(path[len(PREFIJO_V1):])
        else:
            url = "{{ api_root }}" + _plantillas(path)

        peticion: dict[str, Any] = {
            "_id": rid,
            "parentId": carpeta_actual_id,
            "modified": ahora,
            "created": ahora,
            "name": _nombre_de(metodo, path, op),
            "description": op.get("description") or "",
            "method": metodo,
            "body": body,
            "parameters": _query(op),
            "headers": headers,
            "url": url,
            "metaSortKey": request_counter,
            "collectionId": COLLECTION_ID,
            "_type": "request",
        }

        if (metodo, path) in SIN_AUTH:
            peticion["authentication"] = {"type": "none"}

        if multipart:
            # multipart/form-data lo pone Insomnia solo cuando hay params de
            # tipo file. El boundary es suyo; ponerlo a mano rompe la cabecera.
            peticion["headers"] = []

        script = SCRIPT_POR_RUTA.get((metodo, path))
        if script:
            peticion["scripts"] = [
                {"name": "Autocompletar", "type": "post-response", "exec": script}
            ]

        salida.append(peticion)

    return {
        "_type": "export",
        "__export_format": 4,
        "__export_date": FECHA_EXPORT,
        "__export_version": VERSION_EXPORT,
        "resources": recursos + salida,
    }


def _pedir_spec(base_url: str) -> dict:
    url = base_url.rstrip("/") + "/openapi.json"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.load(r)
    except urllib.error.URLError as exc:
        print(f"No se pudo leer {url}: {exc}", file=sys.stderr)
        print("Levanta la API primero:  make up", file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    # `--base-url` decide tanto de donde se lee el OpenAPI como que valor se
    # escribe en `api_root` / `base_url` del environment. Como `construir` lo
    # lee, se rebinda aqui en vez de pasarlo por parametro.
    global BASE_POR_DEFECTO

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE_POR_DEFECTO)
    parser.add_argument("--spec", help="Leer el OpenAPI de un archivo en vez de la API")
    parser.add_argument("--salida", default=SALIDA_POR_DEFECTO)
    parser.add_argument(
        "--check",
        action="store_true",
        help="No escribe. Sale con 1 si el archivo esta desfasado.",
    )
    args = parser.parse_args()

    BASE_POR_DEFECTO = args.base_url

    if args.spec:
        with open(args.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
    else:
        spec = _pedir_spec(args.base_url)

    coleccion = construir(spec)

    peticiones = sum(1 for r in coleccion["resources"] if r.get("_type") == "request")
    carpetas = sum(1 for r in coleccion["resources"] if r.get("_type") == "request_group")

    texto = json.dumps(coleccion, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        try:
            with open(args.salida, encoding="utf-8") as fh:
                actual = fh.read()
        except FileNotFoundError:
            print(f"{args.salida} no existe.")
            return 1
        if actual == texto:
            print(f"{args.salida} esta al dia ({peticiones} peticiones, {carpetas} carpetas).")
            return 0
        print(f"{args.salida} esta DESFASADO respecto de la API viva.")
        print("  Regenera con:  python3 scripts/generar_insomnia.py")
        return 1

    with open(args.salida, "w", encoding="utf-8") as fh:
        fh.write(texto)

    print(f"{args.salida}: {peticiones} peticiones en {carpetas} carpetas.")
    print("Recuerda pegar `password` en el environment antes del primer login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())