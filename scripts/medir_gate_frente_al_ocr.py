#!/usr/bin/env python3
"""¿El gate ATRAPA el total mal leido, o lo deja entrar a la base?

POR QUE ESTA MEDICION ES LA QUE DECIDE
======================================

La pregunta de negocio es "si el API escanea el ticket y saca los datos, el
usuario se evita transcribir". Eso NO se responde con la exactitud del campo:
se responde con **que pasa cuando la lectura falla**.

Porque hay dos worlds muy distintos:

  (a) El gate atrapa los malos -> el sistema NUNCA afirma un total falso. Lo que
      llega es "prellenado + confirmacion", y la persona solo teclea el 29% que
      salio mal. Eso es un producto: sigue siendo mas rapido que teclear el 100%.

  (b) El gate NO los atrapa -> un total falso entra a la tabla, la conciliacion
      no cuadra, y el error aparece un mes despues en un PDF firmado. Eso no es
      un producto, y ningun porcentaje de exactitud lo salva.

`scripts/medir_precision_ocr.py` mide (a)-parte: dice cuantos campos se leen
bien. NO dice que hace el sistema con los que salen mal. Por eso esta medicion
existe aparte.

QUE MIDE, EXACTAMENTE
====================

Para cada foto rotulada:
  1. el total que la VERDAD (a mano, mirando el papel) declara
  2. el total que el parser leyo
  3. el veredicto del gate con ESA lectura
  4. si el veredicto es AUTO_APROBADO

Y la pregunta final, en una linea:

  DE LOS TOTALES MAL LEIDOS, ¿CUANTOS ENTRARON COMO APROBADOS?

Si la respuesta es 0, el diseno original es defendible con su real: prellena y
hace confirmar. Si es >0, hay que decirlo sin rodeos.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "/app/scripts")
from medir_precision_ocr import CAMPOS, _comparable, compara  # noqa: E402

CACHE = Path("/tmp/_medida_ocr_cache.json")


def ocr_una_vez(fotos):
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    from app.core.archivo_real import detectar_tipo_real
    from app.services import ocr as ocr_mod

    textos = {}
    for foto in fotos:
        datos = foto.read_bytes()
        if detectar_tipo_real(datos) is None:
            continue
        leido = ocr_mod.leer_imagen(datos)
        textos[foto.name] = leido.texto
        print(f"  {foto.name}: {len(leido.texto)} chars")
    CACHE.write_text(json.dumps(textos, ensure_ascii=False))
    return textos


def main() -> int:
    carpeta = Path(sys.argv[1])
    verdad = json.loads(Path("/verdad/verdad.json").read_text())
    fotos = sorted(p for p in carpeta.iterdir() if p.name in verdad)
    textos = ocr_una_vez(fotos)

    from app.core.enums import ExtractionStatus
    from app.services.confidence_gate import gate_ticket
    from app.services.parser_service import _parse_receipt_text

    print(f"\n{'=' * 96}")
    print("EL GATE FRENTE A LOS TOTALES MAL LEIDOS")
    print(f"{'=' * 96}")
    print(f"{'foto':<34}{'verdad':>11}{'leido':>11}  {'acierto':<8}{'veredicto':<22}{'fallos'}")
    print("-" * 96)

    mal_leidos_aprobados = []
    bien_leidos_no_aprobados = []
    filas = []

    for nombre in sorted(textos):
        esperado = verdad[nombre]
        r = _parse_receipt_text(textos[nombre])
        v = gate_ticket(
            provider_name=r.provider_name,
            total_amount=r.total_amount,
            tax_amount=r.tax_amount,
            expense_date=r.expense_date,
            provider_tax_id=r.provider_tax_id,
            subtotal=r.subtotal,
            confidence=r.confidence,
            source=r.confidence_source,
        )
        veredicto_total, _ = compara(
            _comparable(esperado.get("total_amount")),
            _comparable(r.total_amount),
            "total_amount",
        )
        acierto = veredicto_total == "acierto"
        aprobado = v.status is ExtractionStatus.AUTO_APROBADO
        fallos = ",".join(v.validation.failures)[:34] or "-"

        print(f"{nombre[:33]:<34}{str(esperado.get('total_amount')):>11}"
              f"{str(r.total_amount):>11}  {'BIEN' if acierto else 'MAL':<8}"
              f"{v.status.value:<22}{fallos}")

        filas.append((nombre, acierto, aprobado))
        if not acierto and aprobado:
            mal_leidos_aprobados.append(nombre)
        if acierto and not aprobado:
            bien_leidos_no_aprobados.append(nombre)

    mal = [f for f in filas if not f[1]]
    bien = [f for f in filas if f[1]]
    print("-" * 96)
    print(f"\ntotales bien leidos   : {len(bien)} de {len(filas)}")
    print(f"totales mal leidos   : {len(mal)} de {len(filas)}")
    print(f"de los MAL leidos, cuantos entraron AUTO_APROBADOS: {len(mal_leidos_aprobados)}")
    print(f"de los BIEN leidos, cuantos NO se aprobaron solos   : {len(bien_leidos_no_aprobados)}")

    if mal:
        print("\nlos mal leidos, uno por uno:")
        for n in mal:
            print(f"  - {n}")
    if bien_leidos_no_aprobados:
        print("\nlos bien leidos quegoto a revision (el gate es mas estricto que el papel):")
        for n in bien_leidos_no_aprobados:
            print(f"  - {n}")

    print()
    if mal_leidos_aprobados:
        print("VEREDICTO: el gate DEJA ENTRAR totales falsos. El diseno original NO es")
        print("           defendible tal cual: hay que revisar antes de persistir.")
        return 1
    print("VEREDICTO: el gate NUNCA aprueba un total mal leido. El diseno original es")
    print("           defendible CON SU REAL: el sistema prellena y la persona confirma.")
    print("           El costo real es la fraction que hay que teclear a mano, no el riesgo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
