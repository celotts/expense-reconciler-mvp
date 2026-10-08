# Resume la salida de `midir-responsive.mjs` en una tabla.
#
# POR QUE UN ARCHIVO Y NO UNA TUBERIA
# ==================================
# El corredor del navegador imprime el JSON del script y despues un resumen de
# la pagina, asi que `jq` sobre la salida entera falla con "extra data" y un
# `sed` por numeros de linea se rompe en cuanto el JSON cambia de forma. Aca se
# busca la LLAVE que abre el objeto de resultados y se decodifica desde ahi.
#
# El criterio de `OK` es `overflowPx == 0`, no "no hay culpables": una tabla
# dentro de un `overflow-x-auto` SI tiene elementos que se salen, y no es un
# defecto — por eso el script marca cada culpable con `enContenedorConScroll`.
import json
import sys

txt = sys.stdin.read()
if '"pantallas"' not in txt:
    print(txt[:3000])
    sys.exit("la salida no trae resultados: revisa que el script no haya fallado")

i = txt.index('"pantallas"')
d, _ = json.JSONDecoder().raw_decode(txt[txt.rindex("{", 0, i):])

errores = d.get("errores") or []
print("errores de navegacion:", errores if errores else "ninguno")
print()

mal = total = 0
for ancho, paginas in d["pantallas"].items():
    print(ancho)
    for clave, v in paginas.items():
        total += 1
        if v["overflowPx"] == 0:
            print(f"  OK   {clave:16} scrollWidth={v['scrollWidth']}")
        else:
            mal += 1
            print(f"  MAL  {clave:16} overflow={v['overflowPx']}px")
            for c in v["culpables"][:3]:
                if not c["enContenedorConScroll"]:
                    print(f"        {c['sel'][:88]}")
    print()

print(f"RESULTADO: {total - mal}/{total} pantallas sin desborde horizontal")
sys.exit(1 if (mal or errores) else 0)
