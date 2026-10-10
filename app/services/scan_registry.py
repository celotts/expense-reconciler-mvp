"""Un canal que dice que esta pasando mientras el escaneo corre.

EL PORQUE
---------

`POST /scan` es sincrono y con fotos tarda **minutos**: cada una paga OCR, y si la
cascada escala a vision son 10 segundos mas por archivo. Con 200 archivos el
cliente puede estar mirando un `POST` abierto diez minutos sin saber nada.

Eso no es un detalle de UX: es el estado en el que la gente decide que algo se
cayo y cancela, o que la carpeta esta vacia y mete el papel a mano. Sin visibilidad
las dos cosas pasan.

Y el peor caso es el silencioso: si el escaneo se traba en la foto 3 de 200, la
pesta dice "cargando" durante los 196 archivos que faltan. Un canal que diga
"voy en el 3 de 200, lleva 40 segundos" convierte una espera en algo que se
puede mirar.

LO QUE ESTE MODULO ES, Y LO QUE NO
---------------------------------

Un REGISTRO EN MEMORIA de las corridas. No es una cola de trabajos, no reinicia
trabajos caidos y no sobrevive a que el contenedor se reinicie.

Es en memoria a proposito, y por una razon concreta del proyecto: el escaner
recorre UNA CARPETA y el `ledger` (`scan_files`) ya es lo que evita repetir
trabajo. Lo que falta no es persistir el progreso —eso ya esta en
`scan_events`— sino poder preguntar "donde va ESTA corrida que empece hace un
minuto". Un registro en memoria responde eso, y si el proceso se muere, la
corrida se perdio con el y `scan_files` sigue diciendo la verdad de que archivos
ya se leyeron. Un trabajo a medias que un reinicio "retoma" es peor: el papel ya
puede haberse movido.

UN NOMBRE QUE SIGNIFICA DOS COSAS ES PEOR QUE UN NOMBRE RARO
-----------------------------------------------------------

`con_ticket` y no `leidos` en el registro, porque el resumen de la corrida ya usa
`leidos` para "nuevos + actualizados". Con los dos llamados igual, el canal decia
`leidos: 8` y el resumen `leidos: 0` en la MISMA corrida — los dos correctos— y eso
se lee como un bug del sistema. Medido, no supuesto: salio asi la primera vez que
se probaron los dos juntos.

QUE SE GUARDA Y QUE NO
----------------------

Se guarda lo accionable: cuantos archivos van, cual es el actual, cuantos salier
bien, cuantos necesitan a una persona. **No se guardan los bytes ni el texto del
comprobante**, y eso no es por privacy sino porque el resumen ya esta en
`scan_files` y `tickets`; duplicarlo aqui seria una segunda verdad que se
desincroniza.

LO QUE SI SE GUARDA DEL TICKET, Y POR QUE NO ES ESA SEGUNDA VERDAD
------------------------------------------------------------------

`procesados[]` lleva el **encabezado** del ticket: proveedor, RFC, fecha,
subtotal, IVA, IEPS, el conteo de lineas y los `validation_errors`. Antes solo
llevaba el total y el estado, y eso hacia el panel casi inutil: con OCR al 60% de
exactitud, ver "$0.00 — pendiente" no dice si fallo leer el papel o si el gate
lo freno porque la aritmetica no cuadraba, que son dos trabajos distintos para
quien esta mirando.

La razon por que esto NO es una segunda verdad que se desincroniza: son los
**mismos campos de la misma fila** que ya se leyeron para pintar `monto` y
`extraction_status`. No hay una segunda lectura ni un segundo criterio; lo que
cambio es que ahora se copian todos en vez de dos. Si el registro se desincroniza
del ticket, se desincroniza en los campos que ya se desincronizaban.

**Y va LEIDO de la fila, no del `TicketExtractionResult` del lector.** Es la misma
distincion que ya separa `ScanItemResponse.datos` de la lectura cruda: si el gate
mando la lectura a revision, aqui se ven los numeros PRODUCIDOS JUNTO al veredicto
que los rechazo. Mandarlos sueltos dejaria al operador creyendo que lo que ve es
lo que el sistema afirma.

**Las lineas NO se copian, solo se cuentan.** `tickets.items` puede traer 28
partidas de un ticket de supermercado, y el feed guarda 60 archivos: copiar el
contenido completo son hasta 1 700 objetos en memoria para pintar un numero. El
detalle de las lineas, si alguien lo quiere, esta en el ticket, que es donde esta
la verdad y donde se corrige.

CUANDO DESAPARECE UNA CORRIDA
-----------------------------

Al terminar queda en el historial. Las que llevan mas de `TTL_SEGUNDOS` se
descartan en la siguiente consulta, no por un temporizador: un temporizador en
background dentro de un contenedor que se reinicia es un hilo mas que puede
morir, y el filtro a la vista es el mismo resultado sin esa dependencia.

La consecuencia se dice en voz alta: al reiniciar el contenedor, `GET /scan/runs`
devuelve vacio aunque la carpeta tenga 200 archivos a medio leer. No es un bug,
es ladecision de no fingir que un proceso que murio sigue trabajando.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

# Cuanto se conserva el historial. Con una persona escaneando una vez al dia, dos
# horas alcanzan para comparar la corrida de hoy con la de ayer. Guardar mas es
# memoria por nada: el historico que sirve para comparar meses ya vive en
# `scan_events`, que es append-only y esta en la base.
TTL_SEGUNDOS = 2 * 60 * 60

# Cuantas corridas se guardan como maximo. Es un tope duro y no solo de tiempo: un
# escaneo en modo `simular` que se llame en bucle llenaria el registro de entradas
# viejas, y el filtro por TTL no las alcanza porque todas son recientes.
MAX_CORRIDAS = 20


@dataclass
class Corrida:
    """Una pasada del escaner, mientras corre y despues de terminar."""

    id: str
    carpeta: str
    actor: str | None = None
    iniciada_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    terminada_at: datetime | None = None
    simulado: bool = False

    # El progreso. Todos empiezan en cero y suben; no hay un estado "unknown"
    # porque el registro se crea al abrir la corrida, no cuando llega el primer
    # archivo.
    archivos_vistos: int = 0
    # `con_ticket` y NO `leidos`: el resumen de la corrida ya usa `leidos` para
    # "nuevos + actualizados", que es otra cosa. Con los dos nombres iguales, el
    # canal decia `leidos: 8` y el total `leidos: 0` en la MISMA corrida, y los
    # dos numeros eran ciertos. Un nombre que significa dos cosas es peor que un
    # nombre raro.
    con_ticket: int = 0
    con_error: int = 0
    # El archivo que se esta leyendo AHORA. Es lo que mas importa en la pantalla:
    # un contador que sube solo dice "algo pasa"; el nombre del archivo dice si
    # se trabo en uno concreto.
    actual: str | None = None

    # --- El feed de lo que ya se LEYO, con sus importes --------------------
    #
    # `actual` dice en que archivo va. Esto dice que salio de ahi, cuanto
    # encontro y si el sistema se atrevio a afirmarlo.
    #
    # Sin esto, la pantalla de un escaneo con 200 fotos muestra un contador que
    # sube y un nombre de archivo: no hay forma de saber si lo que salio era un
    # total de $4,093.80 o un cero. Y el operador no puede empezar a corregir
    # mientras corre, que es justo cuando tiene el contexto fresco.
    #
    # Es una lista ACOTADA a proposito: se queda con las ultimas
    # `MAX_PROCESADOS`. Un escaneo de 3 000 archivos no puede accumulation en
    # memoria lo que encontro de los 3 000, y el operador quiere los ultimos,
    # no el historico — el historico esta en `/scan/files` y en `scan_files`.
    #
    # `monto_confiable` NO es lo que hay: es lo que el sistema se atreve a
    # afirmar. Con OCR al 60% eso es `0.00` la mayoria de las veces, y esa
    # diferencia —lo leido contra lo afirmado— es la que hay que ensenar en la
    # pantalla. Un operador que solo ve el total cree que leyeron bien.
    #
    # Cada entrada lleva ADEMAS el encabezado del ticket (proveedor, RFC, fecha,
    # subtotal, IVA, IEPS, lineas y los `validation_errors`). Ver la nota "LO QUE
    # SI SE GUARDA DEL TICKET" en el docstring del modulo: antes el panel decia
    # "$0.00, pendiente" y no distinguia "no supe leer el papel" de "el gate lo
    # paro porque la aritmetica no cuadraba", que son dos trabajos distintos.
    procesados: list[dict] = field(default_factory=list)

    # El total, que se rellena al cerrar. `None` mientras corre, y eso es
    # intencional: un resumen a medias parece un resumen de una corrida corta.
    resumen: dict | None = None

    @property
    def terminada(self) -> bool:
        return self.terminada_at is not None

    @property
    def segundos(self) -> float:
        fin = self.terminada_at or datetime.now(timezone.utc)
        return (fin - self.iniciada_at).total_seconds()

    def a_dict(self) -> dict:
        """La forma que sale por la API.

        Los importes van como texto y no como numero: son `Decimal` y un JSON los
        serializa como float, que es justo lo que el proyecto no hace en ninguna
        otra parte.
        """
        return {
            "id": self.id,
            "carpeta": self.carpeta,
            "actor": self.actor,
            "iniciada_at": self.iniciada_at.isoformat(),
            "terminada_at": self.terminada_at.isoformat() if self.terminada_at else None,
            "terminada": self.terminada,
            "simulado": self.simulado,
            "archivos_vistos": self.archivos_vistos,
            "con_ticket": self.con_ticket,
            "con_error": self.con_error,
            "actual": self.actual,
            "procesados": _json_safe(self.procesados),
            "segundos": round(self.segundos, 1),
            "resumen": _json_safe(self.resumen),
        }


def _json_safe(valor):
    """`Decimal` y `date` a texto en cualquier profundidad, para que `json.dumps`
    no falle.

    La `date` va con los `Decimal` y por el mismo motivo: `datetime` ya lo
   Was resolved earlier with `_isoformat()` explicito en `a_dict`, pero `fecha` del
    ticket se copia a la lista sin pasar por ahi, y un `date` crudo revienta
    `json.dumps` con un `TypeError` en el SERIALIZADO — que es donde no se ve que
    el problema era el feed sino la respuesta entera.

    La fecha sale como ISO (`2026-10-01`), que es lo que el front ya espera de
    todos los demas `expense_date` de la API.
    """
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    if isinstance(valor, dict):
        return {k: _json_safe(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_json_safe(v) for v in valor]
    return valor


# El lock es OBLIGATORIO y no decorativo. FastAPI corre los handlers en un pool de
# hilos y el escaneo larga `await` entre archivo y archivo: sin lock, dos hilos
# pueden leer el dict mientras otro lo muta. En CPython un `dict.items()` durante
# un `dict[k] = v` no lanza, pero puede devolver un estado a medias, y "el
# progreso muestra 3 de 200 cuando ya va en 7" es el tipo de numero que hace que
# alguien reinicie el escaneo a la mitad.
_lock = threading.Lock()

_corridas: dict[str, Corrida] = {}
# El orden de apertura, para el tope de MAX_CORRIDAS. Un `dict` en Python 3.7+
# conserva el orden de insercion, asi que las mas viejas son las primeras.
_orden: list[str] = []


def abrir_corrida(carpeta: str, actor: str | None, simulado: bool) -> Corrida:
    """Registra que empezo una pasada. Devuelve la corrida para ir marcándola."""
    with _lock:
        _descartar_viejas()
        corrida = Corrida(
            id=str(uuid.uuid4()),
            carpeta=carpeta,
            actor=actor,
            simulado=simulado,
        )
        _corridas[corrida.id] = corrida
        _orden.append(corrida.id)
        # El tope se aplica aqui y no solo por TTL porque una tanda de
        # `simular` en bucle genera entradas todas recientes, y el filtro por
        # edad no las alcanza.
        while len(_orden) > MAX_CORRIDAS:
            # El nombre importa: `velha` en la asignacion y `vieja` en el uso es
            # un `NameError` que solo aparece en la corrida 21, porque hasta la 20
            # el `while` no entra. Un test que abre una sola corrida nunca lo ve.
            evictada = _orden.pop(0)
            _corridas.pop(evictada, None)
        return corrida


def marcar_archivo(corrida: Corrida | None, relative_path: str) -> None:
    """Un archivo mas visto, y cual es el que se esta leyendo."""
    if corrida is None:
        return
    with _lock:
        corrida.archivos_vistos += 1
        corrida.actual = relative_path


def marcar_con_ticket(corrida: Corrida | None) -> None:
    """Un archivo que produjo ticket, aunque sea de una corrida anterior.

    Cuenta los `SIN_CAMBIOS` tambien, y por eso se llama `con_ticket` y no
    `leidos`: el archivo se vio y su ticket existe, pero no se leyo nada nuevo.
    """
    if corrida is None:
        return
    with _lock:
        corrida.con_ticket += 1


# Cuantos archivos del feed se guardan. Es un techo de MEMORIA y tambien de lo
# que cabe en una pantalla: 60 filas de las ultimas es lo que un operador llega
# a mirar antes de que la lista se vuelva scroll. Lo que haya antes esta en
# `scan_files`, que es donde vive el historico de verdad.
MAX_PROCESADOS = 60


def registrar_procesado(
    corrida: Corrida | None,
    *,
    relative_path: str,
    ticket_id: str | None = None,
    monto: Decimal | None = None,
    extraction_status: str | None = None,
    motor: str | None = None,
    accion: str | None = None,
    confiable: bool = False,
    es_duplicado: bool = False,
    # --- El encabezado del ticket ---------------------------------------
    #
    # Todos opcionales y todos leidos de la FILA, no de la lectura cruda: si el
    # gate mando el comprobante a revision, el operador tiene que ver aqui los
    # numeros que produjo la lectura AL LADO del veredicto que los rechazo.
    # Mandarlos por separado permitiria leerlos como si fueran lo que el sistema
    # afirma, que es justo lo que el gate se niega a afirmar.
    #
    # El default `None` en todos es lo que hace que la firma siga siendo
    # compatible con las llamadas que no los pasan: un `ACTUALIZADO` o un
    # duplicado pueden no tener nada que decir del encabezado.
    proveedor: str | None = None,
    rfc: str | None = None,
    fecha: date | None = None,
    subtotal: Decimal | None = None,
    iva: Decimal | None = None,
    ieps: Decimal | None = None,
    lineas: int | None = None,
    validation_errors: str | None = None,
) -> None:
    """Un archivo terminado de leerse, con lo que el sistema leyo de el.

    `confiable` es lo que separa "el sistema leyo 97.56" de "el sistema se
    atreve a afirmar 97.56", y son dos afirmaciones distintas con OCR al 60%.
    El front las pinta de otra forma por eso, y por eso viajan separadas en vez
    de calcular el front si el monto "parece bien".

    `lineas` es un CONTEO y no la lista. Con 60 archivos en el feed y un ticket de
    supermercado de 28 partidas, copiar `items` entero son 1 700 objetos en
    memoria para pintar un numero. Quien quiera las lineas las pide en el ticket,
    que es donde estan y donde se pueden corregir.

    Se llama con el `Decimal` crudo y la conversion a texto pasa por
    `_json_safe`, como todos los importes de este modulo. Un `float` en un
    importe de dinero es exactamente lo que el proyecto no hace en ningun sitio.
    """
    if corrida is None:
        return
    with _lock:
        corrida.procesados.append({
            "relative_path": relative_path,
            "ticket_id": ticket_id,
            "monto": monto,
            "extraction_status": extraction_status,
            "motor": motor,
            "accion": accion,
            "confiable": bool(confiable),
            "es_duplicado": bool(es_duplicado),
            "proveedor": proveedor,
            "rfc": rfc,
            "fecha": fecha,
            "subtotal": subtotal,
            "iva": iva,
            "ieps": ieps,
            "lineas": lineas,
            "validation_errors": validation_errors,
        })
        if len(corrida.procesados) > MAX_PROCESADOS:
            del corrida.procesados[:-MAX_PROCESADOS]


def marcar_error(corrida: Corrida | None) -> None:
    """Un archivo que no se pudo leer. Es el que obliga a mirar."""
    if corrida is None:
        return
    with _lock:
        corrida.con_error += 1


def cerrar_corrida(corrida: Corrida | None, resumen: dict | None) -> None:
    """La corrida termino, con o sin error."""
    if corrida is None:
        return
    with _lock:
        corrida.terminada_at = datetime.now(timezone.utc)
        corrida.actual = None
        corrida.resumen = resumen


def consultar(corrida_id: str) -> Corrida | None:
    with _lock:
        _descartar_viejas()
        return _corridas.get(corrida_id)


def listar(limite: int = 10) -> list[Corrida]:
    """Las corridas recientes, la activa primero.

    Se ordena por inicio y no por fin: durante una corrida larga el orden por fin
    dejaria la activa al final, que es justo donde nadie mira.
    """
    with _lock:
        _descartar_viejas()
        return [_corridas[i] for i in _orden[-limite:]][::-1]


def _descartar_viejas() -> None:
    """Saca del registro lo que ya paso del TTL.

    Se llama DENTRO del lock y sin volver a tomar: si tomara el lock aqui, un
    `threading.Lock` no es reentrante y la corrida se bloquearia a si misma.
    """
    ahora = datetime.now(timezone.utc)
    while _orden:
        corrida = _corridas.get(_orden[0])
        if corrida is None:
            _orden.pop(0)
            continue
        if ahora - corrida.iniciada_at <= timedelta(seconds=TTL_SEGUNDOS):
            return
        _orden.pop(0)
        _corridas.pop(corrida.id, None)