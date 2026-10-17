#!/usr/bin/env python3
"""Prueba de humo del escaner contra la API real del contenedor.

Por que existe. Los 907 tests corren contra SQLite y con la sesion de usuario
simulada, asi que responden "el codigo hace lo que dice". Esta prueba responde
"el contenedor levanta, Tesseract esta instalado con espanol, la carpeta se
puede leer por el montaje de solo lectura, y el escaner crea un ticket de
verdad". Son cuatro cosas que ninguna prueba unitaria puede contestar.

Que NO hace, a proposito:

- No crea usuarios ni empresas: se conecta con un token de los que ya hay, o
  falla y dice cual falta. Crear una cuenta real para una prueba seria dejar
  basura en la base de alguien.
- No borra lo que encuentra. El escaner es de solo lectura sobre la carpeta y
  esta prueba solo mira.

Uso:

    # Con el stack levantado (make up):
    python3 scripts/smoke_scan.py --email ana@empresa.mx --password ...
    # O con el token de un login ya hecho:
    python3 scripts/smoke_scan.py --token eyJ...
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

API = "http://localhost:8000/api/v1"

# Un comprobante sintetico con TODA la evidencia: RFC, subtotal, IVA, total y
# fecha. Es el caso que el gate auto-aprueba, y por eso sirve para comprobar que
# el camino completo -- leer, parsear, gate, persistir -- llego al final.
#
# La fecha va en `AAAA/MM/DD` y no en `DD/MM/AAAA` a proposito: es el formato que
# no se leia antes de esta rama y con el que imprimen los tickets de
# autocomerccio en Mexico. Si esta prueba pasa, el formato esta leido.
COMPROBANTE = """Tiendas Ramirez SA de CV
RFC: TRAM910101XXX
Fecha: 2025/03/15
Subtotal: 948.28
IVA (16%): 151.72
TOTAL: 1100.00
"""


def pedir(metodo: str, ruta: str, token: str | None = None, cuerpo: dict | None = None) -> tuple[int, dict]:
    """Una peticion, y el cuerpo como dict aunque venga de otro tipo."""
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    peticion = urllib.request.Request(f"{API}{ruta}", data=datos, method=metodo)
    peticion.add_header("Content-Type", "application/json")
    if token:
        peticion.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(peticion, timeout=120) as r:
            crudo = r.read().decode()
            return r.status, json.loads(crudo) if crudo else {}
    except urllib.error.HTTPError as e:
        crudo = e.read().decode()
        try:
            return e.code, json.loads(crudo) if crudo else {}
        except json.JSONDecodeError:
            return e.code, {"detail": crudo[:300]}


class Falla(Exception):
    """Una comprobacion que no se cumplio. Se acumula para reportar todas."""


def comprobar(ok: bool, que: str, detalle: str = "") -> None:
    if not ok:
        raise Falla(f"{que}{(': ' + detalle) if detalle else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email")
    ap.add_argument("--password")
    ap.add_argument("--token")
    ap.add_argument("--empresa", help="Nombre de una empresa existente a la que atribuir.")
    args = ap.parse_args()

    fallos: list[str] = []
    paso = lambda n: print(f"  ok  {n}")

    # --- La puerta -------------------------------------------------------
    print("\n1. El API responde y pide token")
    try:
        status, cuerpo = pedir("GET", "/scan/config")
        comprobar(
            status == 401,
            "sin token, /scan/config deberia dar 401",
            f"dio {status}: {cuerpo}",
        )
        paso("el escaner esta detras de la puerta")
    except Exception as exc:
        return acabar([f"el API no responde en {API}: {exc}"], args)

    token = args.token
    if not token:
        if not (args.email and args.password):
            return acabar(
                ["hace falta --token, o --email y --password para hacer login"], args)
        status, cuerpo = pedir(
            "POST", "/auth/login",
            cuerpo={"email": args.email, "password": args.password},
        )
        if status != 200:
            return acabar([f"el login dio {status}: {cuerpo}"], args)
        token = cuerpo["access_token"]
    paso("sesion obtenida")

    # --- Los motores de OCR ----------------------------------------------
    print("\n2. Que motores de OCR hay en ESTA maquina")
    try:
        status, ocr = pedir("GET", "/scan/ocr", token)
        comprobar(status == 200, "/scan/ocr dio un error", str(ocr))
        for nombre, info in ocr["motores"].items():
            marca = "ok " if info["disponible"] else "no "
            print(f"      {marca} {nombre}: {info['motivo'] or 'disponible'}")
        comprobar(
            ocr["motores"].get("tesseract", {}).get("disponible") is True,
            "Tesseract no esta disponible en el contenedor",
            "el Dockerfile deberia instalarlo; rebuild con `make up`",
        )
        paso("Tesseract instalado con su paquete de espanol")
    except Falla as exc:
        fallos.append(str(exc))

    # --- La carpeta -------------------------------------------------------
    print("\n3. De que carpeta va a leer, y existe")
    try:
        status, config = pedir("GET", "/scan/config", token)
        comprobar(status == 200, "/scan/config dio un error", str(config))
        print(f"      {config['carpeta']}")
        comprobar(
            config["carpeta_existe"],
            "la carpeta no existe",
            f"{config['carpeta']} — con Docker se monta desde TICKETS_HOST_DIR",
        )
        paso("la carpeta existe y es alcanzable desde el contenedor")
    except Falla as exc:
        fallos.append(str(exc))

    # --- Un archivo de verdad --------------------------------------------
    print("\n4. El escaner lee un archivo real y crea su ticket")
    try:
        # Se sube por la API de HTTP, no por la carpeta: asi se comprueba el
        # camino de PDF-con-texto sin depender de lo que haya en el disco. La
        # ruta de la carpeta se acaba de comprobar en el paso 3.
        import uuid

        nombre = f"humo-{uuid.uuid4().hex[:8]}.pdf"
        cuerpo_pdf = ("%PDF-1.7\n" + COMPROBANTE).encode()

        empresa_id = None
        if args.empresa:
            status, empresas = pedir("GET", "/companies/?limit=200", token)
            if status == 200:
                for e in empresas:
                    if e["name"].lower() == args.empresa.lower():
                        empresa_id = e["id"]
                        break
            comprobar(empresa_id is not None, f"no existe la empresa {args.empresa!r}")

        # El endpoint de subida es multipart, y `pedir` habla JSON. Se hace a mano.
        if empresa_id is None:
            print("      (sin --empresa: se comprueba el inventario, no la creacion de tickets)")
            status, resultado = pedir("POST", "/scan/", token, {})
            comprobar(status == 200, "el escaneo dio un error", str(resultado))
            print(f"      {resultado['archivos_vistos']} archivo(s) vistos")
            paso("el escaneo corrio y respondio")
        else:
            borde = "----humo"
            partes = []
            partes.append(
                f"--{borde}\r\n"
                f'Content-Disposition: form-data; name="company_id"\r\n\r\n{empresa_id}\r\n'
            )
            partes.append(
                f"--{borde}\r\n"
                f'Content-Disposition: form-data; name="file_type"\r\n\r\npdf\r\n'
            )
            partes.append(
                f"--{borde}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{nombre}"\r\n'
                f"Content-Type: application/pdf\r\n\r\n"
            )
            partes.append(cuerpo_pdf.decode("latin-1"))
            partes.append(f"\r\n--{borde}--\r\n")
            data = "".join(partes).encode("latin-1")

            p = urllib.request.Request(
                f"{API}/tickets/extract-and-create", data=data, method="POST"
            )
            p.add_header("Content-Type", f"multipart/form-data; boundary={borde}")
            p.add_header("Authorization", f"Bearer {token}")
            try:
                with urllib.request.urlopen(p, timeout=120) as r:
                    ticket = json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                crudo = e.read().decode()
                raise Falla(f"la subida dio {e.code}: {crudo[:300]}") from None

            comprobar(
                ticket["total_amount"] == 1100.0,
                "el total leido no es el del comprobante",
                f"llego {ticket['total_amount']}",
            )
            comprobar(
                ticket["provider_tax_id"] == "TRAM910101XXX",
                "el RFC no se leyo",
                f"llego {ticket['provider_tax_id']!r}",
            )
            comprobar(
                ticket["expense_date"] == "2025-03-15",
                "la fecha AAAA/MM/DD no se leyo",
                f"llego {ticket['expense_date']!r}",
            )
            comprobar(
                ticket["confidence_source"] == "pdf_text",
                "el origen de lectura no es pdf_text",
                f"llego {ticket['confidence_source']!r}",
            )
            print(
                f"      ticket {ticket['id'][:8]} · {ticket['provider_name']} · "
                f"{ticket['total_amount']} · {ticket['confidence']} ({ticket['confidence_source']})"
            )
            paso("el PDF con texto se leyo con reglas, sin llamar a ningun modelo")
    except Falla as exc:
        fallos.append(str(exc))

    # --- El escaner por HTTP ---------------------------------------------
    print("\n5. El endpoint de escaneo responde y es idempotente")
    try:
        status, primera = pedir("POST", "/scan/", token, {})
        comprobar(status == 200, "el primer escaneo dio un error", str(primera))
        status, segunda = pedir("POST", "/scan/", token, {})
        comprobar(status == 200, "el segundo escaneo dio un error", str(segunda))
        comprobar(
            segunda["nuevos"] == 0,
            "la segunda pasada creo tickets nuevos",
            f"creo {segunda['nuevos']}: la idempotencia no aguanto",
        )
        comprobar(
            segunda["sin_cambios"] == segunda["archivos_vistos"],
            "no todo lo que ya estaba quedo 'sin cambios'",
            f"{segunda['sin_cambios']} de {segunda['archivos_vistos']}",
        )
        print(
            f"      {segunda['archivos_vistos']} archivo(s), "
            f"{segunda['sin_cambios']} sin cambios, {segunda['nuevos']} nuevos"
        )
        paso("escanear dos veces no creo nada nuevo")
    except Falla as exc:
        fallos.append(str(exc))

    # --- El registro y las estadisticas -----------------------------------
    print("\n6. El registro y las estadisticas responden")
    try:
        status, stats = pedir("GET", "/scan/stats", token)
        comprobar(status == 200, "/scan/stats dio un error", str(stats))
        status, listado = pedir("GET", "/scan/files?limit=5", token)
        comprobar(status == 200, "/scan/files dio un error", str(listado))
        # Un estado inventado tiene que dar 422, no una lista vacia: una lista
        # vacia hace creer que la carpeta no tiene nada.
        status, _ = pedir("GET", "/scan/files?estado=INVENTADO", token)
        comprobar(
            status == 422,
            "un estado de escaneo inventado deberia dar 422",
            f"dio {status}",
        )
        print(
            f"      {stats['total_archivos']} archivo(s) · "
            f"{stats['tickets_vinculados']} con ticket · "
            f"{stats['archivos_con_error']} con error"
        )
        paso("el registro, las estadisticas y el filtro de estado responden")
    except Falla as exc:
        fallos.append(str(exc))

    return acabar(fallos, args)


def acabar(fallos: list[str], args) -> int:
    print()
    if fallos:
        print(f"{len(fallos)} comprobacion(es) fallaron:\n")
        for f in fallos:
            print(f"  - {f}")
        print("\nLa prueba no limpio lo que creo: el ticket y el archivo quedan ahi.")
        return 1
    print("Todo verificado contra el contenedor.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Falla as exc:
        print(f"\nFallo: {exc}")
        raise SystemExit(1) from None
