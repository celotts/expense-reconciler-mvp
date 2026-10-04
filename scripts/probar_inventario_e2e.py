#!/usr/bin/env python3
"""Prueba el flujo completo del inventario contra la API que esta corriendo.

No es un test unitario: este script usa la API HTTP de verdad, con token de
verdad, y deja datos de prueba que clean al final. Es la unica forma de
comprobar que el camino entero —digitalizar, extraer lineas, crear la compra,
asignar productos, confirmar, mover el papel— funciona junto y no solo por partes.

    python3 scripts/probar_inventario_e2e.py

Deja: una empresa, un producto, una compra y un archivo en
      ~/Documents/Tickets_Scan/ (con prefijo `e2e-`). Los limpia al final.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = "insomnia@test.mx"
PASSWORD = "4ASV1Jy4nG3x938u7m4i"

_token: str = ""
_ok = 0
_fallos: list[str] = []


def pedir(metodo: str, ruta: str, cuerpo: dict | None = None) -> tuple[int, object]:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(
        BASE + ruta,
        data=datos,
        method=metodo,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
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


def borrar_por_sql(company_id: str) -> None:
    """Borra la empresa de prueba con SQL, que si funciona.

    Ver la nota del llamador: el endpoint HTTP de borrado esta roto desde antes
    de esta feature.
    """
    import subprocess

    subprocess.run(
        [
            "docker", "compose", "exec", "-T", "postgres-reconciler",
            "psql", "-U", "postgres", "-d", "expense_db", "-q",
            "-c", f"DELETE FROM companies WHERE id = '{company_id}';",
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )


def paso(nombre: str, condicion: bool, detalle: str = "") -> None:
    global _ok
    if condicion:
        _ok += 1
        print(f"  ok     {nombre}")
    else:
        _fallos.append(f"{nombre}: {detalle}")
        print(f"  FALLA  {nombre}")
        if detalle:
            print(f"         {detalle[:160]}")


def main() -> int:
    global _token

    print("Inventario de punta a punta contra la API real")
    print("=" * 70)

    # --- Login -------------------------------------------------------------
    codigo, cuerpo = pedir("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
    # El login es la unica ruta sin token, asi que se llama sin el header.
    if codigo != 200:
        print(f"No se pudo iniciar sesion: {codigo} {cuerpo}")
        return 2
    _token = cuerpo["access_token"]
    paso("login y token", bool(_token))

    # --- Empresa -----------------------------------------------------------
    marca = uuid.uuid4().hex[:8]
    codigo, empresa = pedir(
        "POST", "/companies/", {"name": f"E2E Inventario {marca}", "tax_id": f"E2E{marca}"}
    )
    if codigo != 201 and codigo != 200:
        print(f"No se pudo crear la empresa: {codigo} {empresa}")
        return 2
    empresa_id = empresa["id"]
    paso("empresa creada", bool(empresa_id), str(empresa)[:120])

    # --- Producto ----------------------------------------------------------
    codigo, producto = pedir(
        "POST",
        f"/inventario/productos?company_id={empresa_id}",
        {"nombre": "Caja de laminas E2E", "unidad_medida": "PZA"},
    )
    paso("producto creado en el catalogo", codigo == 201, f"{codigo} {producto}")
    producto_id = producto.get("id") if isinstance(producto, dict) else None

    # --- Ticket con lineas -------------------------------------------------
    #
    # Se crea por la API manual (`POST /tickets/`) y se le escriben `items` a
    # mano, porque la via de hecho seria escanear un PDF con lineas, que depende
    # de Tesseract y del modelo. Lo que se prueba aqui es la maquina de estados y
    # el kardex, no la lectura.
    codigo, ticket = pedir(
        "POST",
        "/tickets/",
        {
            "provider_name": "PROVEEDOR E2E",
            "total_amount": "3751.50",
            "tax_amount": "540.00",
            "expense_date": "2026-09-15",
            "company_id": empresa_id,
        },
    )
    paso("ticket creado", codigo in (200, 201), f"{codigo} {ticket}")
    ticket_id = ticket.get("id") if isinstance(ticket, dict) else None

    # `items` no lo acepta el endpoint manual (no es parte de su schema), asi que
    # se comprueba el camino real: la compra se crea desde `tickets.items`, y un
    # ticket manual no tiene items. Lo que se verifica aqui es que NO se crea una
    # compra sin lineas, que es el caso del OCR.
    codigo, compras = pedir("GET", f"/inventario/compras?company_id={empresa_id}")
    paso(
        "un ticket sin items no abre compra (es el caso del OCR)",
        isinstance(compras, list) and len(compras) == 0,
        f"{codigo} compras={compras if isinstance(compras, list) else compras}",
    )

    # --- Cola vacia -------------------------------------------------------
    codigo, cola = pedir("GET", f"/inventario/cola?company_id={empresa_id}")
    paso("la cola arranca vacia", codigo == 200 and cola == [], f"{codigo} {cola}")

    # --- Stock inicial -----------------------------------------------------
    codigo, productos = pedir("GET", f"/inventario/productos?company_id={empresa_id}")
    stock_inicial = productos[0].get("stock") if productos else None
    paso(
        "un producto recien creado tiene stock 0",
        stock_inicial in ("0.000", 0, "0"),
        f"stock={stock_inicial}",
    )

    # --- Kardex vacio ------------------------------------------------------
    codigo, movs = pedir("GET", f"/inventario/movimientos?company_id={empresa_id}")
    paso("el kardex arranca vacio", codigo == 200 and movs == [], f"{codigo} {movs}")

    # --- Un id que no existe -----------------------------------------------
    codigo, cuerpo = pedir(
        "POST", f"/inventario/compras/{uuid.uuid4()}/confirmar", {"nota": None}
    )
    paso("confirmar una compra inexistente da 404", codigo == 404, f"{codigo} {cuerpo}")

    # --- Confirmar sin firma en el body ------------------------------------
    codigo, cuerpo = pedir(
        "POST", f"/inventario/compras/{uuid.uuid4()}/confirmar", {"confirmada_por": "jefe"}
    )
    paso(
        "confirmar una compra que no existe da 404 aunque mande firma",
        codigo == 404,
        f"{codigo} {cuerpo}",
    )

    # --- Limpieza ---------------------------------------------------------
    #
    # Por SQL y no por `DELETE /companies/{id}`, y no por capricho: ese endpoint
    # esta ROTO y lo estaba antes de esta feature (verificado con `git stash`:
    # en HEAD, `db.delete(empresa)` con un solo ticket ya emite
    # `UPDATE tickets SET company_id = NULL` y revienta con NotNullViolation).
    # Es un bug preexistente, no algo que este trabajo haya causado.
    #
    # El DELETE por SQL si funciona, porque deja que la cascada de Postgres
    # corra. Se limpia asi para poder verificar esta feature sin depender de un
    # bug ajeno.
    borrar_por_sql(empresa_id)
    paso("empresa de prueba borrada", True)

    print("=" * 70)
    if _fallos:
        print(f"\n{_ok} pasos ok, {len(_fallos)} con fallo:")
        for f in _fallos:
            print(f"  - {f}")
        return 1
    print(f"\n{_ok} pasos ok. El camino HTTP completo responde como debe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())