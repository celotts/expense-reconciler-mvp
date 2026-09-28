"""Defensa contra inyeccion de formulas en lo que se exporta.

El ataque
---------

Un archivo de Excel o CSV no es solo texto: una celda que empieza por `=`,
`+`, `-` o `@` es una FORMULA, y el programa que abre el archivo la calcula. Es
la forma de inyeccion que mas se gana de la lista, porque no necesita una
base de datos ni un servidor: necesita que alguien abra un archivo.

El recorrido aqui es corto y no requiere ser un atacante con muchos recursos:

1. Cualquiera con un token puede crear un gasto con
   `POST /api/v1/tickets/` y `provider_name` a placer. `TicketBase` no valida el
   contenido de ese campo, solo que no este vacio y no sea "Unknown Provider".
2. La `description` de un movimiento bancario sale de un CSV que se SUBE. O sea
   que el dato de fuera entra solo, sin que nadie tenga que escribir a mano.
3. La conciliacion empareja ambos y las tres exportaciones (`excel`, `contpaqi`,
   `generic`) los escriben en la hoja, celda por celda, sin saneamiento.
4. El contador abre el archivo en Excel o lo sube a CONTPAQI. La formula se
   ejecuta en la maquina de el, con los permisos de el.

`=cmd|'/c calc.exe'!A1` abre una calculadora. Las variantes que se han visto en
la practica llegan a descargar y ejecutar un binario, o a exfiltrar mediante
una peticion HTTP a un servidor del atacante. Y en un archivo de contabilidad el
destino no es un desconocido: es la persona que lleva la empresa.

Por que el guard va en la escritura y no en la lectura
------------------------------------------------------

Podria neutralizar al leer de la base, y seria un error de colocacion. Un
campo mas que se anada a la exportacion dentro de tres meses pasaria por alto
el saneamiento, y el fallo seria invisible: el archivo se sigue generando, con
la celda intacta, y nadie se entera hasta que alguien lo abre. Puesto en el
`write`, el archivo entero pasa por el, y `forzar_texto` actua como una red
para las columnas que no pasaron por ningun filtro.

`forzar_texto` recorre `ws.iter_rows()`, o sea TODAS las celdas de la hoja, sin
importar de que columna venga cada una. Esa es la razon de que baste con el y
de que una columna anadida mañana quede cubierta sin que nadie se acuerde: la
cobertura es una propiedad del recorrido, no una disciplina del programador.

Las dos capas NO se suman en el XLSX, y esto es lo importante
-------------------------------------------------------------

`neutralizar_formula` antepone un apostrofo, que es la forma de marcar "esto es
texto" para un CSV. En un XLSX NO hay que usarlo, y usarlo arruina el dato: el
apostrofo de un CSV lo consume el programa al abrir, pero en un XLSX una celda
de tipo texto con un apostrofo inicial lo MUESTRA. El contador abriria el
archivo y veria `'=cmd|'/c calc'!A1` con una comilla fantasma pegada, en todos
los proveedores que empiezan por signo. Es decir: arreglar la seguridad
metiendo basura en los datos.

Por eso en el XLSX la garantia la da `forzar_texto`, que ademas es la mas
fuerte de las dos: no filtra por lista de caracteres, le dice a openpyxl que la
celda es texto, y openpyxl no puede escribir una formula en una celda de texto.
Ni con el valor mas hostil. `neutralizar_filas` queda para cuando exista una
exportacion a CSV de verdad, que hoy no la hay.
"""

from __future__ import annotations

import re
from typing import Any

# Los caracteres con los que Excel y LibreOffice interpretan el inicio de una
# celda como formula. Los tres ultimos no son "signos" pero hacen lo mismo: un
# tabulador o un salto al inicio de la celda hacen que certainistas versiones
# interpreten lo que sigue como formula, y hay que cubrir tambien el caso de un
# espacio inicial que el usuario copia y pega.
_INICIO_DE_FORMULA = re.compile(r"^[\s]*[=+\-@\t\r]")

# El prefijo de "esto es texto" de Excel. Es la forma de que el archivo muestre
# el dato tal cual, con el signo intacto, sin ejecutarlo. En un XLSX openpyxl lo
# consume el programa al abrir; en un CSV crudo se ve, y por eso el
# neutralizador se usa en el CSV y en el XLSX con `forzar_texto` haciendo el
# trabajo de verdad.
_PREFIJO_TEXTO = "'"


def es_formula_peligrosa(valor: Any) -> bool:
    """Si un valor, tal como lo escribiria el export, podria ejecutarse.

    Solo mira strings: un numero, una fecha o un `None` no pueden ser formula,
    y convertirlos a texto para comprobarlo daria falsos positivos en todas las
    columnas de importe, que es donde masmolesta seria.
    """
    if not isinstance(valor, str):
        return False
    return bool(_INICIO_DE_FORMULA.match(valor))


def neutralizar_formula(valor: Any) -> Any:
    """Un valor seguro para escribir.

    Antepone un apostrofo a las cadenas que empiezan por un caracter de
    formula. El resto de tipos se devuelven sin tocar: no hay nada que
    neutralizar en un `Decimal` y convertirlo a texto haria que la columna
    izquierda dejara de ser numerica en el archivo.
    """
    if not es_formula_peligrosa(valor):
        return valor
    return _PREFIJO_TEXTO + valor


def neutralizar_filas(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lo mismo, sobre las filas enteras.

    Trabaja sobre una lista de dicts para no repetir el bucle en cada uno de los
    tres exportadores, y para que las pruebas puedan ejercitar un solo camino.
    """
    return [{k: neutralizar_formula(v) for k, v in fila.items()} for fila in filas]


def forzar_texto(worksheet: Any) -> None:
    """Le dice a openpyxl que las celdas sospechosas son TEXTO.

    Es la segunda capa, y es la que de verdad sostiene la seguridad: con esto
    puesto, aunque un valor con `=` se cuele sin pasar por `neutralizar_filas`,
    la celda se guarda como texto y no como formula. Sin esto, el archivo
    depende por completo de que nadie olvido el filtro.

    Recorre `ws.iter_rows()` y no las columnas calculadas de pandas porque
    openpyxl necesita ver cada celda para poder cambiarle el tipo. `data_type`
    es lo que decide si openpyxl escribe un `<f>` (formula) o un `<v>` con
    `t="s"` (cadena). Ponerlo a mano DESPUES de asignar el valor es lo unico
    que funciona: si se hiciera antes, la asignacion lo volveria a calcular.
    """
    for fila in worksheet.iter_rows():
        for celda in fila:
            if celda.value is None:
                continue
            if not isinstance(celda.value, str):
                continue
            # Solo se toca lo que openpyxl habria guardado como formula. Un
            # texto normal se deja igual, y un numero no se toca.
            if celda.data_type != "f" and not es_formula_peligrosa(celda.value):
                continue
            celda.data_type = "s"
