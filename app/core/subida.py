"""Topes al leer un archivo que viene de fuera.

El problema
-----------

Los cuatro puntos de subida hacian esto:

    content = await file.read()

Es decir, leian el archivo entero a memoria sin preguntar cuanto viene. Un
cliente autenticado puede mandar un archivo de cualquier tamano y el proceso
se lo come entero antes de que ninguna regla lo mire. No hace falta saber
nada del sistema: basta con un token valido y un `POST` con un cuerpo de
diez gigabytes. Es el negado de servicio mas barato que hay, y en un proceso
que tambien tiene que atender la pantalla de los demas.

Que el token valido no lo haga inofensivo. El limite de la API protege a
todos los usuarios, incluido el que se paso de la app y mando el archivo
grande sin querer, que es el caso de verdad comun.

Por que el tope vive en la lectura y no en el endpoint
------------------------------------------------------

La alternativa es comprobar el tamano en cada handler, con algo como
`if len(content) > MAX: raise HTTPException(413)`. Funciona, y no es lo que
hay aqui, por una razon concreta: un endpoint nuevo seriebra sin la
comprobacion y el fallo es silencioso. No hay error, no hay test rojo, el
archivo entra. Peor: un handler que hace el `read()` ANTES de comprobar ya
cargo el archivo en memoria, que es justo el problema.

Puesto en el lector, el tope es parte de la operacion de leer. No hay forma
de leer un archivo grande por este camino, porque el unico camino es este.

Y se lee a trozos, no de una vez. Un `read()` completo seguido de un `len()`
no protege la memoria: el archivo ya esta cargado cuando se comprueba. Aqui
se lee en bloques de `_TAMANO_TROZO`, se acumula el total y en cuanto se pasa
del tope se levanta la excepcion y se suelta lo leido. El pico de memoria
es el tope, no el archivo.

El `413` en vez del `400`
------------------------

`413 Content Too Large` dice la verdad: el archivo se entendio bien, no se
pudo porque pesa. Un `400` manda a la persona a buscar un error de formato
en un archivo que no tiene ningun error de formato. Cuando alguien sube las
fotos de un ticket y el sistema dice "formato incorrecto" cuatro veces, lo
que va a hacer es dejar de intentarlo, y no es la leccion que queremos.
"""

from __future__ import annotations

from fastapi import HTTPException, UploadFile, status

# Foto de un comprobante tomada con el celular: los iPhone de ultimos anos
# sacan entre 3 y 8 MB. 10 MB da margen de sobra para una foto sin comprimir y
# para un PDF de varias paginas, y sigue siendo un numero que se puede
# explicar.
TICKET_MAX_BYTES = 10 * 1024 * 1024

# CSV bancario: un ano de movimientos de una empresa mediana cabe en un par de
# MB, pero hay Extractos que vienen con years de historial o con miles de
# filas por dia. 25 MB es del orden de 200 000 filas, que es mas de lo que
# cualquiera revisa a mano.
CSV_MAX_BYTES = 25 * 1024 * 1024

# 64 KB. Suficiente para que la llamada al sistema sea barata, y lo bastante
# pequeno para que un archivo de 25 MB no se lea en 25 llamadas.
_TAMANO_TROZO = 64 * 1024

# Multiplicador de lo que se enseña al usuario. Decir "25 MB" cuando el tope
# es 26214400 no ayuda a nadie a decidir.
_MB = 1024 * 1024


def _cual(limite: int) -> str:
    if limite % _MB == 0:
        return f"{limite // _MB} MB"
    return f"{limite / _MB:.1f} MB"


async def leer_con_limite(file: UploadFile, limite: int, que: str) -> bytes:
    """Lee el archivo entero, o se niega a seguir leyendo si se pasa del tope.

    `que` es el nombre del archivo y va en el mensaje de error, para que la
    persona sepa cual de los varios archivos que subio es el que no cabe.

    Se lee a trozos a proposito. Un `read()` sin limite seguido de un `len()`
    no evita la carga en memoria: para cuando se comprueba el tamano, el
    archivo ya esta entero en el proceso, y el pico de memoria -- que es lo
    que se queria evitar -- ya ocurrio. Leyendo en bloques, el tope es de
    verdad el techo de lo que se puede llegar a ocupar.
    """
    partes: list[bytes] = []
    total = 0

    while True:
        trozo = await file.read(_TAMANO_TROZO)
        if not trozo:
            break
        total += len(trozo)
        if total > limite:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"El archivo {que} pesa mas de {_cual(limite)}. "
                    "No se procesa. Si es un PDF con imagenes muy pesadas, "
                    "exportalo de nuevo con menos resolucion."
                ),
            )
        partes.append(trozo)

    return b"".join(partes)


async def leer_ticket(file: UploadFile, nombre: str = "") -> bytes:
    """Archivo de un comprobante: foto o PDF."""
    return await leer_con_limite(file, TICKET_MAX_BYTES, nombre or "del comprobante")


async def leer_csv_bancario(file: UploadFile, nombre: str = "") -> bytes:
    """Extracto o movements de banco en CSV."""
    return await leer_con_limite(file, CSV_MAX_BYTES, nombre or "del CSV")
