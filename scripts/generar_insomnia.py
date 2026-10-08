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
    "producto_id": "{{ producto_id }}",
    # La cuenta que se administra. Es una variable y no una constante porque las
    # rutas de `PATCH /usuarios/{id}` y `POST /usuarios/{id}/contrasena` se
    # prueban contra una cuenta de prueba, no contra la propia: las dos exigen la
    # contrasena Y la ultima cuenta activa no se puede dar de baja, asi que probarlo
    # sobre la unica cuenta real daria 409 y no probaria nada.
    "usuario_id": "{{ usuario_id }}",
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
    ("/usuarios", "Usuarios"),
    ("/inventario", "Inventario"),
    ("/bank-transactions", "Bank Transactions"),
    ("/reconciliations", "Reconciliations"),
    ("/reports", "Reports"),
]

# El orden de ESTA lista es el orden de las carpetas, y el de las peticiones dentro de
# cada una. No es estetico: `1. Login`, `2. Quien soy`, `3. Empresas`... son numeros que
# dicen en que orden correrlas, y el nombre de la carpeta es lo primero que se lee de
# la coleccion.
#
# `Usuarios` e `Inventario` estaban aqui desde que los routers se montaron pero no
# salieron en la coleccion, porque `_carpeta_de` devuelve `CARPETA_SIN_V1` para lo que
# no reconoce: las 12 peticiones de inventario y las 4 de usuarios se fueron al cajon de
# "Sistema", junto con `/health`. El sintoma es una coleccion donde esta todo y no esta
# en orden, y no dice nada de que falten Carpetas.
#
# Y el orden interno de cada carpeta sale de `NOMBRES`, no de esta lista: `4a` y `4c` son
# los dos diagnosticos y `5`/`6` guardan, y esa numeracion solo tiene sentido si se lee
# de arriba abajo.

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

# Etiquetas de las peticiones, para que el listado no sean 76 rutas crudas.
#
# EL ORDEN DE ESTE DICCIONARIO ES EL ORDEN DEL FLUJO DE TRABAJO, y no por gusto:
# el que lee la coleccion nueva no sabe por donde empezar, y la respuesta correcta es
# "por aqui". Los `<-` marcan las peticiones que ademas fijan una variable de la que
# dependen las siguientes, que es el otro modo de romperse sin querer: correr el
# listado de tickets antes del de empresas y que `ticket_id` salga vacio.
NOMBRES: dict[tuple[str, str], str] = {
    ("POST", "/auth/login"): "1. Login  <- TIENE que ir primero",
    ("GET", "/auth/me"): "2. Quien soy (token valido)",
    ("GET", "/health"): "Health",
    # --- Informe de cierre mensual ----------------------------------------
    # El JSON va antes que el PDF aunque el PDF sea el que se lleva, y el motivo
    # es el inverso del habitual: el PDF no se puede leer en pantalla. El preview
    # es el que se revisa ANTES de decidir, y el PDF es lo que sale despues de esa
    # misma decision. estan en la carpeta "Reports" y en ese orden.
    ("GET", "/reports/cierre-mensual"): "r1. Informe de cierre (JSON, para revisar antes de firmar)",
    ("GET", "/reports/cierre-mensual.pdf"): "r2. Descargar el PDF  <- lo que se entrega al cliente",
    ("GET", "/companies/"): "3. Listar Empresas  <- fija {{ company_id }} sola",
    ("POST", "/companies/"): "c1. Crear empresa",
    ("GET", "/companies/{company_id}"): "c2. Ver una empresa",
    ("PATCH", "/companies/{company_id}"): "c3. Corregir una empresa",
    ("DELETE", "/companies/{company_id}"): "c4. Borrar una empresa (CASCADE: se lleva todo)",
    # --- Leer un comprobante ---------------------------------------------
    # Los dos endpoints de diagnostico van antes de los que guardan, y no por
    # preferencia: es el orden en el que uno no ensucia la base. Un
    # `extract-and-create` con un archivo que se lee mal deja un ticket en la cola
    # que despues hay que revisar a mano, y repetirlo 10 veces deja 10.
    ("POST", "/tickets/extract-diagnostico"): "4a. DIAGNOSTICO: lee y explica, NO guarda",
    ("GET", "/scan/files"): "4b. Archivos vistos  <- fija {{ file_id }}",
    ("POST", "/scan/files/{file_id}/diagnostico"): "4c. DIAGNOSTICO de un archivo ya escaneado, NO guarda",
    ("POST", "/scan/"): "5. Escanear carpeta (SI guarda)",
    ("POST", "/tickets/extract-and-create"): "6. Subir un comprobante (SI guarda)  <- fija {{ ticket_id }}",
    # --- Ver y corregir lo leido -----------------------------------------
    ("GET", "/tickets/"): "7. Listar Tickets  <- fija {{ ticket_id }}",
    ("GET", "/tickets/review-queue"): "8. Cola de revision (lo que el sistema NO pudo leer)",
    ("GET", "/tickets/{ticket_id}"): "8b. Ver un ticket",
    ("PATCH", "/tickets/{ticket_id}/review"): "8c. Aprobar / rechazar en la cola",
    # --- Lo demas -------------------------------------------------------
    ("POST", "/tickets/"): "t1. Alta manual (sin archivo)",
    ("PATCH", "/tickets/{ticket_id}"): "t2. Corregir un ticket",
    ("GET", "/tickets/{ticket_id}/documento"): "t3. Ver el comprobante original",
    ("GET", "/tickets/{ticket_id}/documentos"): "t4. Historial de versiones del comprobante",
    ("PUT", "/tickets/{ticket_id}/documento"): "t5. Subir version nueva (NO borra la anterior)",
    ("POST", "/tickets/extract"): "t6. Leer SIN gate ni persistencia (solo el resultado)",
    ("PATCH", "/tickets/categoria"): "t7. Clasificar varios de una vez",
    ("DELETE", "/tickets/{ticket_id}"): "t8. Borrar un ticket",
    ("GET", "/scan/config"): "s1. Configuracion del escaner",
    ("GET", "/scan/ocr"): "s2. Estado del OCR (hay Tesseract?)",
    ("GET", "/scan/stats"): "s3. Resumen del escaneo",
    ("GET", "/scan/runs"): "s4. Corridas del escaner",
    ("GET", "/scan/runs/{corrida_id}"): "s5. Detalle de una corrida",
    ("POST", "/scan/files/{file_id}/reprocess"): "s6. Releer UN archivo (SI sobrescribe)",
    ("GET", "/scan/files/{file_id}"): "s7. Detalle de un archivo",
    ("GET", "/tickets/spot-check"): "9. Muestreo del 5% (cuenta la exactitud)",
    ("PATCH", "/tickets/{ticket_id}/spot-check"): "9b. Registrar veredicto del muestreo",
    ("GET", "/tickets/accuracy"): "9c. Exactitud por origen (medida, no prometida)",
    ("POST", "/reconciliations/run"): "Correr conciliacion",
    ("GET", "/reconciliations/"): "r1. Conciliaciones  <- fija {{ reconciliation_id }}",
    ("POST", "/reconciliations/"): "r1b. Crear una conciliacion a mano",
    ("POST", "/reconciliations/run"): "r2. Correr el motor de conciliacion",
    ("GET", "/reconciliations/{reconciliation_id}"): "r2b. Ver una conciliacion",
    ("PATCH", "/reconciliations/{reconciliation_id}"): "r3. Corregir un veredicto a mano",
    ("DELETE", "/reconciliations/{reconciliation_id}"): "r4. Borrar una conciliacion",
    ("GET", "/bank-transactions/"): "r5. Movimientos bancarios  <- fija {{ bank_transaction_id }}",
    ("POST", "/bank-transactions/"): "r5b. Alta manual de un movimiento",
    ("POST", "/bank-transactions/import-csv"): "r5c. Subir CSV (SOLO previsualiza, no guarda)",
    ("POST", "/bank-transactions/import-csv-and-create"): "r5d. Subir CSV y guardar",
    ("GET", "/bank-transactions/{transaction_id}"): "r5e. Ver un movimiento",
    ("PATCH", "/bank-transactions/{transaction_id}"): "r5f. Corregir un movimiento",
    ("DELETE", "/bank-transactions/{transaction_id}"): "r5g. Borrar un movimiento",
    ("GET", "/reconciliations/export/excel"): "r6. Exportar Excel",
    ("GET", "/reconciliations/export/contpaqi"): "r7. Exportar CONTPAQI",
    ("GET", "/reconciliations/export/generic"): "r8. Exportar generico",
    ("GET", "/reconciliations/mappings"): "r9. Mapeos contables  <- fija {{ mapping_id }}",
    ("POST", "/reconciliations/mappings"): "r10. Crear mapeo contable",
    ("GET", "/reconciliations/mappings/{mapping_id}"): "r11. Ver un mapeo",
    ("PATCH", "/reconciliations/mappings/{mapping_id}"): "r12. Corregir un mapeo",
    ("DELETE", "/reconciliations/mappings/{mapping_id}"): "r13. Borrar un mapeo",
    ("GET", "/categorias"): "z1. Categorias de gasto",
    ("GET", "/categorias/sin-clasificar"): "z2. Sin clasificar",
    ("GET", "/dashboard/"): "z3. Dashboard",
    ("GET", "/bank-transactions/"): "Movimientos bancarios",
    # Inventario. El numero NO es correlativo con el de Tickets y Scan, y es a
    # proposito: el inventario es una etapa distinta, no el paso 9 de leer un
    # comprobante. Encadenarlos haria creer que autorizar una compra es lo siguiente
    # de subir un ticket, cuando entre medias hay que extraer las lineas.
    #
    # Y las que SUMAN STOCK llevan `<<`, porque es el unico dato de toda la API que
    # no se puede deshacer: `confirmar_compra` y `registrar_movimiento` escriben en
    # un kardex append-only. Un 409 ahi no se arregla con otro 409.
    ("GET", "/inventario/cola"): "i1. Lineas sin producto (la cola de trabajo)",
    ("GET", "/inventario/compras"): "i2. Compras  <- fija {{ compra_id }}",
    ("GET", "/inventario/compras/{compra_id}"): "i3. Ver una compra",
    ("POST", "/inventario/compras/items/{item_id}/producto"): "i4. Asignar producto a una linea",
    ("POST", "/inventario/compras/{compra_id}/confirmar"): "i5. Autorizar  << SUMA STOCK, no se deshace",
    ("POST", "/inventario/compras/{compra_id}/rechazar"): "i6. Rechazar: no es una compra",
    ("POST", "/inventario/compras/{compra_id}/reabrir"): "i7. Deshacer el rechazo",
    ("GET", "/inventario/movimientos"): "i8. El kardex",
    ("POST", "/inventario/movimientos"): "i9. Venta o ajuste  << SUMA/RESTA STOCK, no se deshace",
    ("GET", "/inventario/productos"): "i10. Catalogo de productos (con stock)",
    ("POST", "/inventario/productos"): "i11. Alta manual de producto",
    ("GET", "/inventario/productos/{producto_id}"): "i12. Ver un producto",
    ("PATCH", "/inventario/productos/{producto_id}"): "i13. Renombrar / verificar / dar de baja",
    # Cuentas. Sin numero correlativo tampoco: es otra etapa.
    ("GET", "/usuarios"): "u1. Cuentas del sistema",
    ("GET", "/usuarios/{usuario_id}"): "u2. Ver una cuenta",
    ("PATCH", "/usuarios/{usuario_id}"): "u3. Renombrar / dar de baja  (pide X-Contrasena-Actual)",
    ("POST", "/usuarios/{usuario_id}/contrasena"): "u4. Cambiar contrasena  (pide X-Contrasena-Actual)",
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

# Subir un comprobante: deja `{{ ticket_id }}` y dice que paso con el, sin inventar
# nada.
#
# POR QUE DICE EL ESTADO Y NO SOLO FIJA EL ID
# ===========================================
#
# Porque `extract-and-create` NO falla cuando la lectura es mala: guarda el ticket en
# `PENDIENTE` o `REQUIERE_REVISION` y responde 201 igual. Un script que solo mirara el
# codigo de respuesta daria "ok" sobre un ticket que hay que ir a corregir a mano, que
# es el peor momento para creer que salio bien.
#
# Y por que dice "el sistema leyo esto" y no "el sistema leyo bien": la respuesta no
# lleva la confianza de la lectura como campo propio, sino `confidence_source` y
# `extraction_status`. Traducirlos aqui es juntar informacion de dos sitios en una
# linea que se lee sin conocer los enums.
SCRIPT_EXTRACT_CREATE = """const env = insomnia.environment;
const pruebas = [];

pruebas.push({
  name: 'responde 201 y trae el ticket',
  fn: () => {
    if (response.code !== 201) {
      throw new Error('HTTP ' + response.code + ': ' + response.text.slice(0, 300));
    }
    const d = JSON.parse(response.text);
    env.set('ticket_id', d.id);
    console.log('ticket_id = ' + d.id);
    return true;
  },
});

pruebas.push({
  name: 'digo como quedo, y si hay que revisarlo',
  fn: () => {
    const d = JSON.parse(response.text);

    console.log('  proveedor: ' + d.provider_name);
    console.log('  total:     ' + d.total_amount);
    console.log('  fecha:     ' + (d.expense_date || '(sin leer)'));
    console.log('  origen:    ' + d.confidence_source +
                '  confianza: ' + (d.confidence === null ? 'n/a' : d.confidence));

    if (d.extraction_status === 'AUTO_APROBADO') {
      console.log('');
      console.log('AUTO_APROBADO: el sistema responde por esta lectura y ya entra');
      console.log('a conciliacion. Va a aparecer en el muestreo del 5%.');
    } else {
      console.log('');
      console.log(d.extraction_status + ': NO esta listo para conciliar.');
      if (d.validation_errors && d.validation_errors.length) {
        console.log('  Checks que fallaron:');
        d.validation_errors.forEach(v => console.log('    - ' + v));
      }
      console.log('  Corrigelo con PATCH /tickets/' + d.id + '/review');
    }
    return true;
  },
});

return pruebas;"""

# El diagnostico NO guarda nada, asi que aqui no hay nada que 'dejar listo' para las
# siguientes: su salida se mira, no se encadena. El script solo avisa si el paso
# anterior fallo, que es cuando la respuesta se mira en vacio y no se entiende por que.
#
# POR QUE AVISA Y NO VALIDA
# ==========================
#
# Un diagnostico que "falla" a proposito —un PDF escaneado que no se pudo leer— es el
# caso mas comun, y un script que lo marque como error haria que Insomnia lo mostrara
# en rojo de una forma que se lee como "el endpoint esta roto" cuando es la respuesta
# correcta. Lo que se marca es el 500, que si es un fallo real.
SCRIPT_DIAGNOSTICO = """const env = insomnia.environment;
const pruebas = [];

pruebas.push({
  name: 'no es un error de servidor',
  fn: () => {
    if (response.code >= 500) {
      throw new Error('HTTP ' + response.code + ': ' + response.text.slice(0, 300));
    }
    return true;
  },
});

pruebas.push({
  name: 'digo que escalones hubo y por que',
  fn: () => {
    const d = JSON.parse(response.text);

    if (!d.pasos || !d.pasos.length) {
      console.warn('Sin pasos. Un documento de texto sin comprobante no recorre la ' +
                   'cascada de escalones, y eso no es un fallo.');
      return true;
    }

    console.log('Formato: ' + d.formato_detectado +
                (d.formato_corregido ? '  (corregido: ' + d.formato_corregido + ')'
                                     : ''));
    console.log('');

    d.pasos.forEach(p => {
      const marca = p.aceptado ? 'ok  ' : 'FALLA';
      console.log('  [' + marca + '] ' + p.escalon + ' (' + p.motor + ')' +
                  (p.costo ? '  ' + p.costo : ''));
      if (p.dio && p.dio.proveedor !== undefined) {
        console.log('         proveedor=' + JSON.stringify(p.dio.proveedor) +
                    ' total=' + p.dio.total +
                    ' caracteres=' + (p.dio.caracteres !== undefined
                                        ? p.dio.caracteres : '-'));
      } else if (p.dio && p.dio.caracteres !== undefined) {
        console.log('         caracteres=' + p.dio.caracteres);
      }
      if (p.motivo) console.log('         -> ' + p.motivo);
    });

    if (d.error) {
      console.log('');
      console.log('ERROR DE LECTURA: ' + d.error);
      return true;
    }

    if (d.datos) {
      console.log('');
      console.log('LEYO: ' + d.datos.proveedor + '  total ' + d.datos.total +
                  '  confianza ' + d.datos.confianza + '  (' + d.datos.origen + ')');
    }

    if (d.veredicto) {
      const v = d.veredicto;
      console.log('GATE: ' + v.status + '  confianza ' + v.confidence +
                  '  (' + v.confidence_source + ')');
      if (v.checks_fallidos.length) {
        console.log('  Checks que fallan:');
        v.checks_fallidos.forEach(c => console.log('    - ' + c));
      }
      if (v.coincide_con_guardado === false) {
        console.log('');
        console.log('OJO: releer DARIA otro estado que el guardado (' +
                    d.ticket_status_actual + ' vs ' + v.status + ').');
        console.log('      El problema no es el lector: mira los checks de arriba.');
      }
    }

    console.log('');
    console.log('No se guardo nada (guardado=' + d.guardado + ').');
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
  producto_id            <- GET /inventario/productos
  usuario_id             <- GET /usuarios

El orden importa: login, companies, y despues todo lo demas. El token dura 8
horas (ACCESS_TOKEN_EXPIRE_MINUTES); cuando devuelva 401 por caducado, repite
el login y solo eso.

DOS ENDPOINTS QUE PIDEN UNA CABECERA, Y NO UNA VARIABLE
======================================================

`PATCH /usuarios/{id}` y `POST /usuarios/{id}/contrasena` exigen
`X-Contrasena-Actual` con la contrasena de quien llama. Si la mandas vacia o no la
mandas, la respuesta es 401 con el mensaje de que falta — y no es un problema de
autenticacion: el token puede estar bien.

La cabecera ya viene puesta en esas peticiones, apuntando a `{{ contrasena_actual }}`.
Rellena esa variable una vez y las dos funcionan. Se deja vacia a proposito, y por
la misma razon que `password`: este archivo esta versionado y una contrasena
escrita ahi queda publicada.

Y `usuario_id` no es la cuenta propia a proposito: la ultima cuenta activa no se
puede dar de baja (409), asi que probarlo sobre la unica cuenta real no probaria
nada. Crea una cuenta de prueba con `scripts/crear_usuario.py` y pega aqui su id.

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
    ("POST", "/tickets/extract-diagnostico"): SCRIPT_DIAGNOSTICO,
    ("POST", "/scan/files/{file_id}/diagnostico"): SCRIPT_DIAGNOSTICO,
    # `extract-and-create` fija `ticket_id` porque su respuesta ES el ticket. Sin
    # esto, correr la subida y luego ir a `/tickets/{ticket_id}` daria 404 con la
    # variable vacia, y el sintoma parece un id mal escrito.
    ("POST", "/tickets/extract-and-create"): SCRIPT_EXTRACT_CREATE,
}

# Rutas sin auth. Todo lo demas lo exige el router entero
# (app/api/api_router.py), asi que en la coleccion se pone a nivel de carpeta y
# estas dos lo desactivan.
SIN_AUTH: set[tuple[str, str]] = {
    ("POST", "/auth/login"),
    ("GET", "/health"),
}

# Rutas que exigen `X-Contrasena-Actual` ademas del token.
#
# Es `deps.get_current_user_verificado`, y son las que comprometen a OTRO usuario:
# dar de baja una cuenta o rotar su contrasena. Un token robado —de un
# portapapeles, de un `.env` filtrado— no basta para ninguna de las dos, que es
# justo lo que un token robado si alcanza para todo lo demas.
#
# La lista esta escrita a mano y NO se deduce del OpenAPI porque el header es un
# parametro de dependencia, y FastAPI no lo declara como parametro de la
# operacion: aparece en la cadena de dependencias del endpoint, que el spec no
# expone. Consecuencia de esa limitacion: anadir una ruta aqui es manual, y si se
# olvida, la peticion sale sin la cabecera y el 401 no dice que le falta.
# La clave es (metodo, ruta) y NO solo la ruta, porque dentro de
# `/usuarios/{usuario_id}` hay una lectura que NO la pide: `GET` va con
# `UsuarioActual` a proposito, porque ver una cuenta no compromete a nadie y
# obligar a escribir la contrasena para mirar haria que la defensa se dejara de
# usar. Ponerla tambien en el GET seria aplicar la regla de mas, que es como una
# defensa se vuelve decorativa.
RUTAS_CON_CONTRASENA: set[tuple[str, str]] = {
    ("PATCH", "/usuarios/{usuario_id}"),
    ("POST", "/usuarios/{usuario_id}/contrasena"),
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
    """El nombre legible de la peticion.

    `path` llega SIN `/api/v1` —quitarlo es cosa del llamador, en un solo sitio— y los
    tres diccionarios estan escritos asi. El `fallback` es el `summary` del OpenAPI,
    que es el docstring del endpoint, y es un nombre decente cuando no hay entrada en
    `NOMBRES`: es peor que un numero de ruta, y mejor que nada.
    """
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
                "producto_id": "",
                "usuario_id": "",
                # Se escribe A MANO y no se pega de ningun lado: es la contrasena
                # del actor, y este archivo esta versionado. Igual que `password`.
                "contrasena_actual": "",
            },
            "dataOrder": {
                key: idx for idx, key in enumerate(
                    [
                        "api_root", "base_url", "email", "password", "token",
                        "company_id", "ticket_id", "bank_transaction_id",
                        "reconciliation_id", "mapping_id", "file_id",
                        "compra_id", "compra_item_id", "producto_id",
                        "usuario_id", "contrasena_actual",
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
    # Dentro de cada router, el orden sale de `NOMBRES` —los numeros que el nombre
    # lleva puestos— y no de la forma de la ruta.
    #
    # POR QUE NO EL ORDEN DE FASTAPI
    # ==============================
    #
    # Antes se ordenaba por `path.count("{")`: literales antes que placeholders, que es
    # el orden con el que el servidor resuelve. Es cierto —y por eso `review-queue` esta
    # antes que `/{ticket_id}`— pero es el orden para el SERVIDOR, no para quien lee la
    # coleccion. Con el, "Listar Tickets" salia antes que "Subir un comprobante", y para
    # probar a leer un comprobante habia que saber cual de las 20 era.
    #
    # El numero del nombre es el orden de trabajo: 1, 2, 3... y `4a`/`4b` para las dos
    # ramas del mismo paso. Lo que no esta en `NOMBRES` cae al final de su carpeta, por
    # ruta, que es lo mejor que se puede hacer con algo que nadie nombro.
    #
    # "Sistema" va al final: /health no pertenece a la v1.
    orden_carpetas = {nombre: i for i, (_, nombre) in enumerate(CARPETAS)}
    orden_carpetas[CARPETA_SIN_V1] = len(orden_carpetas)

    def orden_de(nombre: str) -> tuple[str, int]:
        """`(letra, numero)` del nombre de `NOMBRES`, o `("", 0)` si no lo tiene.

        El prefijo de una letra es la ETAPA: `4a`/`4b` son el mismo paso —letra vacia,
        numero 4— y `i1`/`u1`/`r1` son etapas que cuentan por su cuenta. Ordenar por el
        par y no solo por el numero es lo que mantiene el inventario en orden sin que sus
        numeros choquen con los de tickets, que es el motivo de que sean `i1..i13` y no
        `9..21`.

        Un nombre sin numero devuelve `("", 0)` y cae al final de su carpeta, por ruta.
        """
        m = re.match(r"^([a-z]?)(\d+)", nombre.strip())
        if not m:
            return ("", 0)
        return (m.group(1), int(m.group(2)))

    def clave(path: str, metodo: str) -> tuple:
        carpeta = _carpeta_de(path)
        letra, numero = orden_de(NOMBRES.get((metodo, ruta_de(path)), ""))
        return (
            orden_carpetas.get(carpeta, 999),
            1 if numero else 0,   # las numeradas antes que las sueltas
            letra,
            numero,
            path,
        )

    def ruta_de(path: str) -> str:
        return path[len(PREFIJO_V1):] if path.startswith(PREFIJO_V1) else path

    # (carpeta, ruta, metodo, operacion) en orden de aparicion
    plano: list[tuple[str, str, str, dict]] = []
    for path in spec.get("paths", {}):
        ops = spec["paths"][path]
        for metodo in ("get", "post", "patch", "put", "delete"):
            if metodo in ops:
                plano.append((_carpeta_de(path), path, metodo.upper(), ops[metodo]))

    # El orden se aplica DESPUES de armar el plano, y no sobre las rutas del spec,
    # porque `NOMBRES` se indexa por (metodo, ruta): `GET /tickets/{id}` y
    # `DELETE /tickets/{id}` son peticiones distintas con nombres distintos, y ordenar
    # por ruta antes de saber el metodo las mezclaria.
    plano.sort(key=lambda t: clave(t[1], t[2]))

    salida: list[dict] = []
    carpeta_actual = None
    carpeta_actual_id = None
    carpeta_counter = 0
    request_counter = 0

    for nombre_carpeta, path, metodo, op in plano:
        # Los tres diccionarios —`NOMBRES`, `SCRIPT_POR_RUTA`, `SIN_AUTH`— usan la ruta
        # SIN `/api/v1`, porque se escriben a mano y nadie quiere teclear el prefijo
        # en 70 entradas. El OpenAPI si lo trae; `ruta_de` lo quita, y el mismo helper
        # lo usa `clave` para ordenar.
        #
        # Sin esto, las entradas nuevas se ignoran en silencio: el `get` devuelve None
        # y la peticion sale sin nombre bonito ni script. Que es exactamente lo que
        # paso con las tres rutas de diagnostico al escribirlas.
        ruta = ruta_de(path)

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
            "name": _nombre_de(metodo, ruta, op),
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

        if (metodo, ruta) in SIN_AUTH:
            peticion["authentication"] = {"type": "none"}

        if multipart:
            # multipart/form-data lo pone Insomnia solo cuando hay params de
            # tipo file. El boundary es suyo; ponerlo a mano rompe la cabecera.
            peticion["headers"] = []

        # El header `Authorization` va DESPUES del borrado de arriba, no antes.
        #
        # ## POR QUE ESTA ESTA LINEA Y HACE FALTA
        #
        # `AGENTS.md` decia que el archivo anterior estaba "sin un solo header
        # `Authorization`" y que por eso 51 de 52 rutas respondian 401, y que por
        # eso la coleccion es un artefacto generado. Medido en el archivo que
        # este script genera: **76 de 78 peticiones no tenian ni el header ni
        # `authentication`**. El script guardaba el token en el environment con
        # el script de login, y nunca lo mandaba.
        #
        # O sea: la causa raiz se documento, se genero el archivo para
        # arreglarla, y el arreglo no estaba. La coleccion era tan inutil como
        # la anterior, pero ahora con 78 peticiones en vez de 25 y la promesa de
        # que ya no pasaba.
        #
        # Lo que lo hacia invisible: `make insomnia-check` compara el archivo
        # con lo que el script produce, y como los dos coincidian, todo bien. El
        # check responde a "esta al dia", no a "esta bien". Por eso el
        # defecto no se veia al correr el comando, y por eso hace falta el test
        # `tests/unit/test_insomnia_tiene_token.py`, que si responde a lo segundo.
        #
        # ## POR QUE DESPUES DEL BORRADO
        #
        # Arriba, las peticiones multipart hacen `headers = []` porque el
        # `Content-Type` con el boundary lo pone Insomnia y ponerlo a mano lo
        # rompe. Si el header de autorizacion se anadiera antes, esas peticiones
        # lo perderian: serian las de subir comprobantes y mover archivos, que
        # son justo las que de verdad se prueban.
        #
        # ## POR QUE UNA VARIABLE Y NO EL TOKEN PEGADO
        #
        # Por lo mismo que `password`: el environment se versiona con este
        # archivo en un repositorio. El token lo pone el script de login del
        # primer envio y dura 8 horas, asi que pegarlo aqui no solo publica un
        # secreto, publica uno que caduca.
        if (metodo, ruta) not in SIN_AUTH:
            peticion["headers"].append(
                {"name": "Authorization", "value": "Bearer {{ token }}"}
            )

        # Los endpoints que exigen `X-Contrasena-Actual` Salea PRECARGADO CON LA
        # VARIABLE DEL ENVIRONMENT, y con valor vacio.
        #
        # Por que variable y no la contrasena: el environment se versiona con este
        # archivo y una contrasena escrita ahi queda publicada en el repositorio.
        # Mismo criterio que `password`, que sale vacio a proposito.
        #
        # Y por que se pre-carga en vez de dejarlo ausente: sin la cabecera, estos
        # dos devuelven 401 con un mensaje que habla de autenticacion, y parece
        # que el token esta mal. Con la cabecera puesta y vacia, el mensaje dice
        # exactamente lo que falta. La diferencia entre un 401 que se diagnostica
        # solo y uno que hay que interpretar.
        if (metodo, ruta) in RUTAS_CON_CONTRASENA:
            peticion["headers"].append(
                {"name": "X-Contrasena-Actual", "value": "{{ contrasena_actual }}"}
            )

        script = SCRIPT_POR_RUTA.get((metodo, ruta))
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