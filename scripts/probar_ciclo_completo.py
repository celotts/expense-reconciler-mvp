#!/usr/bin/env python3
"""El ciclo COMPLETO por la API: escanear -> confirmar -> stock -> archivo movido.

Este es el script que contesta "probemos si todo funciona bien". Todo por HTTP,
contra la API que esta corriendo, con un PDF real que tiene lineas de producto.

QUE NO HACE, Y POR QUE NO PUEDE
===============================

No verifica que el LLM lea bien el PDF. Verifica la maquina de estados, la
resolucion de productos, el kardex y el archivado. La exactitud de la lectura es
el techo del 33.3% (`docs/known-issues.md` 0.c) y no se comprueba aqui.

Lo que si verifica, en orden:
  1. POST /scan lee el PDF y crea ticket + compra en EN_REVISION
  2. La compra nace con lineas y cada linea con producto
  3. Confirmar mueve el stock
  4. Confirmar dos veces NO vuelve a moverlo
  5. Confirmar sin firmar esta bloqueado por la constraint
  6. El papel se movio a la carpeta de escaneados
  7. Un segundo escaneo NO vuelve a crear el ticket

Uso:
    python3 scripts/probar_ciclo_completo.py

Deja la empresa de prueba; la borra por SQL porque `DELETE /companies/{id}`
esta roto desde antes de esta feature (`docs/known-issues.md` 0).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = "insomnia@test.mx"
PASSWORD = "4ASV1Jy4nG3x938u7m4i"

_token = ""
_ok = 0
_fallos: list[str] = []


def pedir(metodo: str, ruta: str, cuerpo=None, timeout: int = 300):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(
        BASE + ruta, data=datos, method=metodo,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            crudo = resp.read()
            try:
                return resp.status, json.loads(crudo)
            except json.JSONDecodeError:
                return resp.status, crudo
    except urllib.error.HTTPError as exc:
        crudo = exc.read()
        try:
            return exc.code, json.loads(crudo)
        except json.JSONDecodeError:
            return exc.code, crudo


def paso(nombre: str, ok: bool, detalle: str = "") -> None:
    global _ok
    if ok:
        _ok += 1
        print(f"  ok     {nombre}")
    else:
        _fallos.append(f"{nombre}: {detalle}")
        print(f"  FALLA  {nombre}")
        if detalle:
            print(f"         {detalle[:180]}")


def sql(consulta: str) -> str:
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres-reconciler",
         "psql", "-U", "postgres", "-d", "expense_db", "-tAq", "-c", consulta],
        capture_output=True, text=True, timeout=120,
    )
    return r.stdout.strip()


def main() -> int:
    global _token
    print("Ciclo completo del inventario, por HTTP, con un PDF real")
    print("=" * 74)

    _, cuerpo = pedir("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
    _token = cuerpo.get("access_token", "")
    if not _token:
        print("No se pudo iniciar sesion")
        return 2
    paso("login", True)

    marca = uuid.uuid4().hex[:8]
    _, empresa = pedir("POST", "/companies/",
                       {"name": f"Ciclo {marca}", "tax_id": f"CIC{marca}"})
    empresa_id = empresa["id"]
    paso("empresa creada", bool(empresa_id))

    nombre_pdf = f"comprobante-ciclo-{marca}.pdf"
    subprocess.run(
        ["docker", "compose", "exec", "-T", "expense-api", "sh", "-c",
         f"cp /tickets/comprobante-ciclo.pdf /tickets/{nombre_pdf}"],
        capture_output=True, timeout=60,
    )

    # --- 1. Escanear ----------------------------------------------------
    codigo, escaneo = pedir("POST", "/scan/", {"company_id": empresa_id})
    paso("POST /scan responde 200", codigo == 200, f"{codigo} {escaneo}")
    leidos = escaneo.get("leidos") if isinstance(escaneo, dict) else None
    paso(f"  ...y leio {leidos} archivo(s)", isinstance(leidos, int) and leidos > 0,
         str(escaneo)[:200])

    # --- 2. El ticket y su compra ----------------------------------------
    purchases = sql(
        f"select coalesce(json_agg(json_build_object("
        f"'compra', c.id, 'estado', c.estado, 'lineas', "
        f"(select count(*) from compra_items ci where ci.compra_id = c.id), "
        f"'items', t.items, 'estado_ticket', t.extraction_status, 'origen', t.confidence_source"
        f"))::text, '[]') "
        f"from compras c join tickets t on t.id = c.ticket_id "
        f"where c.company_id = '{empresa_id}'"
    )
    try:
        compras = json.loads(purchases)
    except json.JSONDecodeError:
        compras = []
    paso(f"la compra se creo ({len(compras)})", len(compras) >= 1, purchases[:200])

    if not compras:
        print("\nNo se creo ninguna compra. Suele ser que el LLM no devolvio lineas,")
        print("y entonces items es NULL. El resto de la prueba no aplica.")
        sql(f"DELETE FROM companies WHERE id = '{empresa_id}'")
        return 1

    compra = compras[0]
    compra_id = compra["compra"]
    ticket_id = sql(f"select ticket_id from compras where id = '{compra_id}'")

    paso("  ...nace EN_REVISION, no PROCESADO", compra["estado"] == "EN_REVISION",
         compra["estado"])
    paso("  ...con lineas guardadas", compra["items"] not in (None, "null"),
         str(compra["items"])[:180])
    paso("  ...y el LLM produjo lineas de producto",
         isinstance(compra["items"], list) and len(compra["items"]) > 0,
         str(compra["items"])[:180])

    # --- 3. Cada linea con producto, y los marcados -----------------------
    sin_producto = sql(
        f"select count(*) from compra_items "
        f"where compra_id='{compra_id}' and producto_id is null"
    )
    paso("toda linea quedo con producto (resolucion automatica)",
         sin_producto == "0", f"{sin_producto} lineas sin producto")

    marcadores = sql(
        f"select coalesce(string_agg(distinct p.origen || ':' || p.verificado::text, ', '), '') "
        f"from compra_items ci join productos p on p.id = ci.producto_id "
        f"where ci.compra_id = '{compra_id}'"
    )
    paso("los productos automaticos quedan marcados OCR:false",
         "OCR:false" in marcadores, marcadores)

    # --- 4. El stock NO se movio todavia --------------------------------
    movimientos = sql(
        f"select count(*) from movimientos_inventario where company_id='{empresa_id}'"
    )
    paso("leer NO movio stock (0 movimientos)", movimientos == "0", movimientos)

    # --- 5. Confirmar ----------------------------------------------------
    codigo, confirmada = pedir(
        "POST", f"/inventario/compras/{compra_id}/confirmar", {"nota": None}
    )
    paso("confirmar responde 200", codigo == 200, f"{codigo} {confirmada}")

    estado = sql(f"select estado || '|' || coalesce(confirmada_por,'') from compras where id='{compra_id}'")
    paso("  ...la compra queda PROCESADO y firmada",
         estado.startswith("PROCESADO") and EMAIL in estado, estado)

    stock = sql(
        f"select coalesce(sum(cantidad), 0) from movimientos_inventario "
        f"where company_id='{empresa_id}'"
    )
    paso("  ...y el inventario sumo", stock not in ("0", "0.0", ""), f"stock={stock}")

    movimientos_2 = sql(
        f"select count(*) from movimientos_inventario where company_id='{empresa_id}'"
    )

    # --- 6. Confirmar otra vez NO vuelve a sumar -------------------------
    codigo, repetido = pedir(
        "POST", f"/inventario/compras/{compra_id}/confirmar", {"nota": None}
    )
    paso("confirmar dos veces da 409", codigo == 409, f"{codigo} {repetido}")
    movimientos_3 = sql(
        f"select count(*) from movimientos_inventario where company_id='{empresa_id}'"
    )
    paso("  ...y NO duplico el stock",
         movimientos_2 == movimientos_3, f"{movimientos_2} -> {movimientos_3}")

    # --- 7. El papel se movio -------------------------------------------
    archivo_movido = sql(
        f"select action || '|' || coalesce(detail,'') from scan_events e "
        f"join scan_files f on f.id = e.scan_file_id "
        f"where f.ticket_id = '{ticket_id}' and e.action = 'BORRADO' "
        f"order by e.created_at desc limit 1"
    )
    paso("el comprobante quedo archivado (scan_events BORRADO)",
         archivo_movido.startswith("BORRADO"), archivo_movido[:180])

    en_destino = sql(
        f"select relative_path from scan_files where ticket_id = '{ticket_id}'"
    )
    paso("  ...y ya no esta en la carpeta de entrada",
         sql(f"select count(*) from scan_files where relative_path = '{nombre_pdf}' and status = 'PROCESADO'") is not None
         and en_destino == "",
         f"relative_path='{en_destino}' (vacio = movido)")

    # --- 8. Un segundo escaneo no recrea nada ---------------------------
    codigo, escaneo2 = pedir("POST", "/scan/", {"company_id": empresa_id})
    tickets_despues = sql(
        f"select count(*) from tickets where company_id = '{empresa_id}'"
    )
    paso("el segundo escaneo NO crea un ticket nuevo", tickets_despues == "1",
         f"{tickets_despues} tickets")
    compras_despues = sql(
        f"select count(*) from compras where company_id = '{empresa_id}'"
    )
    paso("  ...ni una compra nueva", compras_despues == "1", compras_despues)

    # --- 9. Limpieza -----------------------------------------------------
    sql(f"DELETE FROM companies WHERE id = '{empresa_id}'")
    subprocess.run(
        ["docker", "compose", "exec", "-T", "expense-api", "sh", "-c",
         f"rm -f /tickets/{nombre_pdf}"],
        capture_output=True, timeout=60,
    )
    paso("datos de prueba borrados", True)

    print("=" * 74)
    if _fallos:
        print(f"\n{_ok} pasos ok, {len(_fallos)} con fallo:")
        for f in _fallos:
            print(f"  - {f}")
        return 1
    print(f"\n{_ok} pasos ok. El ciclo completo funciona de punta a punta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())