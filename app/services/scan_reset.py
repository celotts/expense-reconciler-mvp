"""Dejar la base limpia para volver a probar el escaneo.

POR QUE ESTE MODULO ES EL MAS PELIGROSO DEL REPO
================================================

Borra datos. No "marca", no "archiva", no "mueve": **borra**. Y todo lo que este
proyecto sostiene —el kardex, la cadena de custodia del cierre, el historial de
quienaprobo que— esta adentro. Por eso el modulo tiene cuatro cerrojos, y cada uno
esta para que un accidente NO lo atraviese.

Los cuatro, en el orden en que se_evalúan:

1. **`TICKETS_SCAN_RESET_HABILITADO` tiene que estar en `true`.** Por omision es
   `false`, o sea que el endpoint **no existe** hasta que alguien lo prende a
   proposito. No es un parametro del request: es una decision de arranque. Alguien
   que despliegue esto sin leerlo tiene la base intacta, que es lo unico que
   importa cuando hay datos reales detras.

2. **La contrasena del que llama** (`UsuarioVerificado`, o sea token **y**
   `X-Contrasena-Actual`). Es el mismo cerrojo que ya protege dar de baja una
   cuenta o cambiar una contrasena, y por el mismo motivo: un token robado —de un
   portapapeles, de un `.env` filtrado, del historial de un navegador— no debe
   alcanzar para borrar el historico contable. Se paga con ~100 ms de scrypt, que
   es el precio de que un atacante con un token no pueda hacerlo.

3. **Una frase de confirmacion literal en el cuerpo.** No un booleano: un
   `{"confirmar": true}` se manda con un clic de mas, y un booleano no distingue
   "me confirmo a mi mismo" de "el sistema me lo pregunto". La frase es
   `BORRAR_YA` y tiene que venir EXACTA. Es la difeencia entre "escribi BORRAR_YA"
   y "pulse Continuar".

4. **Se niega a borrar trabajo humano.** Si hay **cualquier** ticket con
   `reviewed_at`, o cualquier compra `PROCESADO`, no borra nada y responde por que.
   Ese es el cerrojo que mas importa, porque es el unico que protege lo que no se
   puede recuperar: una persona que corrigio un comprobante mirando el papel, o
   una compra que alguien autorizo para que el stock empezara a contar. Todo lo
   demas —una lectura automatica que se puede volver a hacer— es recuperable.
   Borrarlo es incomodo. Borrar eso es perder trabajo.

LO QUE SE BORRA Y LO QUE NO, Y POR QUE
=======================================

Se borra, y solo esto:

    reconciliations · compra_items · compras · movimientos_inventario
    tickets · scan_files · scan_events   (+ ticket_documents, en cascada)

NO se borra, nunca:

- **`users`.** La cuenta con la que estas operando. Borrarla te deja sin puerta de
  entrada, y la unica recuperacion es entrar a Postgres a mano.
- **`companies`.** El escaneo necesita una para atribuir los tickets. Sin ella,
  `POST /scan` solo inventaria archivos y no crearia nada: la limpieza te dejaria
  sin poder probar justo lo que quieres probar.
- **`productos`.** Es el catalogo. Va aparte de `compras` a proposito: vaciar las
  compras es una prueba de escaneo, y vaciar el catalogo es perder el trabajo de
  dar de alta mil referencias. Son cosas distintas y el endpoint solo hace la
  primera. Hay un endpoint aparte para lo otro.
- **`schema_migrations`.** El esquema, no los datos.

EL ORDEN DE LOS `DELETE`, Y POR QUE ESTA EN ESTE ORDEN
======================================================

`tickets` va **antes** que `ticket_documents`, y no es un detalle de estilo:

`ticket_documents` es **append-only por trigger** (`db/migrations/
0009_documento_inmutable.sql`). El trigger rechaza borrar un documento
**mientras su ticket exista**, y permite el borrado solo cuando ya no existe:

    CREATE OR REPLACE FUNCTION ticket_documents_no_borrar() RETURNS trigger AS $$
    BEGIN
        IF EXISTS (SELECT 1 FROM tickets WHERE id = OLD.ticket_id) THEN
            RAISE EXCEPTION 'ticket_documents es append-only: no se borra un
                documento mientras su ticket exista. Ticket %', OLD.ticket_id;
        END IF;
        RETURN OLD;
    END; $$;

Es la regla 18 de `AGENTS.md`: un comprobante no se altera, y borrarlo es una
decision sobre el ticket que lo contiene.

Por eso el orden es: primero `tickets`, y el papel se va en la **cascada**, que es
la excepcion que el propio trigger contempla.

MEDIDO CONTRA POSTGRES REAL, NO INFERIDO
========================================

No es una regla deducida del DDL. Se ejecutaron los dos ordenes contra la base de
este proyecto:

**El orden malo** (borrar el papel con su ticket de pie) falla:

    ERROR:  ticket_documents es append-only: no se borra un documento mientras su
    ticket exista. Ticket 38a1c061-...

Ocurrio de verdad: la primera limpieza de la base de este trabajo se hizo a mano
con el papel primero, reviento en mitad de la transaccion y hizo `ROLLBACK`. Los 7
tickets quedaron intactos. La segunda, con este orden, borro los 7.

**El orden bueno** (ticket primero, papel en cascada) no solo no falla: es el
unico que puede. Se comprobó tambien con una tabla de dos niveles y el mismo
trigger, para aislar el mecanismo de las demas tablas:

    orden malo  -> ERROR: ... append-only ...
    orden bueno -> el hijo desaparece con el padre, 0 filas

Y por que el trigger **sigue rejecting** despues: un endpoint que lo apagara —con
`session.bypass_trigger_rules()` o deshabilitandolo— haria que su borrado pasara
y habria quitado la regla 18 sin que nada lo notara. `tests/unit/test_scan_reset.py`
comprueba el orden de los `DELETE` leyendo el codigo; el trigger en si solo se
puede probar contra Postgres, y la comprobacion de que sigue puesto se hace a
mano contra la base viva con `DELETE FROM ticket_documents` sobre un ticket de
pie: tiene que dar `append-only`.

Y por que esto va en una **transaccion**: si algo falla a la mitad, no queda la
base en un estado que ninguna pantalla sabe mostrar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import CompanyModel
from app.models.inventario import CompraModel, MovimientoInventarioModel
from app.models.reconciliation import ReconciliationModel
from app.models.scan_file import ScanEventModel, ScanFileModel
from app.models.ticket import TicketModel
from app.models.ticket_document import TicketDocumentModel
from app.models.user import UserModel

# La frase que hay que escribir para que borre.
#
# Es una CONSTANTE y no un parametro de la funcion: si el endpoint la tomara del
# request, el cliente podria mandar lo que el servidor acaba de decir, y dejaria
# de ser una confirmacion para ser un eco. Aqui el servidor decide que palabra es,
# y el cliente tiene que saberla.
FRASE_CONFIRMACION = "BORRAR_YA"


@dataclass
class Conteo:
    """Cuantas filas hay de cada cosa ANTES de borrar.

    Se responde en el `dry_run` para que quien va a borrar vea el numero antes de
    borrarlo, no despues. Un endpoint que responde "ok" sin decir que borro es el
    que produce la sorpresa.
    """

    tickets: int = 0
    tickets_revisados: int = 0
    compras: int = 0
    compras_procesadas: int = 0
    documentos: int = 0
    archivos: int = 0
    conciliaciones: int = 0
    movimientos: int = 0

    @property
    def hay_trabajo_humano(self) -> bool:
        """Si hay algo que una persona hizo y que no se puede rehacer.

        Es la condicion del cerrojo 4. Se cuenta sobre `tickets` y `compras` y no
        sobre los otros, porque:
        - un ticket `reviewed_at` es una persona corrigiendo contra el papel;
        - una compra `PROCESADO` es una persona autorizando que el stock cuente.
        """
        return self.tickets_revisados > 0 or self.compras_procesadas > 0

    def a_dict(self) -> dict:
        return {
            "tickets": self.tickets,
            "tickets_revisados": self.tickets_revisados,
            "compras": self.compras,
            "compras_procesadas": self.compras_procesadas,
            "documentos": self.documentos,
            "archivos": self.archivos,
            "conciliaciones": self.conciliaciones,
            "movimientos": self.movimientos,
            # El total, que es lo que se compara con el numero borrado. Si no
            # coinciden, algo se borro que no se conto, y eso hay que verlo.
            "total_borrable": (
                self.tickets
                + self.compras
                + self.documentos
                + self.archivos
                + self.conciliaciones
                + self.movimientos
            ),
        }


@dataclass
class ResultadoReset:
    """Lo que se borro, o lo que se va a borrar en el `dry_run`."""

    simulado: bool = True
    conteo: Conteo = field(default_factory=Conteo)
    borrado: dict = field(default_factory=dict)
    empresas_conservadas: int = 0
    usuarios_conservados: int = 0


async def contar(db: AsyncSession) -> Conteo:
    """Lo que hay, sin tocar nada.

    Una sola pasada de consultas y sin ninguna escritura: se puede llamar antes
    de decidir, y tambien desde el endpoint para el `dry_run`. Que el conteo sea
    una operacion aparte del borrado es lo que permite previsualizar sin riesgo.
    """

    async def n(model, *where) -> int:
        q = select(func.count()).select_from(model)
        for cond in where:
            q = q.where(cond)
        return int((await db.execute(q)).scalar_one() or 0)

    return Conteo(
        tickets=await n(TicketModel),
        tickets_revisados=await n(TicketModel, TicketModel.reviewed_at.isnot(None)),
        compras=await n(CompraModel),
        compras_procesadas=await n(
            CompraModel, CompraModel.estado == "PROCESADO"
        ),
        # El papel se cuenta aparte porque NO se borra directo: se va en cascada
        # con su ticket. Contarlo es para que quien va a borrar sepa cuantos
        # comprobantes va a perder, y ese numero no se deduce de `tickets`.
        documentos=await n(TicketDocumentModel),
        archivos=await n(ScanFileModel),
        conciliaciones=await n(ReconciliationModel),
        movimientos=await n(MovimientoInventarioModel),
    )


async def limpiar(db: AsyncSession, *, confirmar: bool) -> ResultadoReset:
    """Borra lo digitalizado, o dice lo que borraria.

    `confirmar=False` es el `dry_run`: cuenta y responde, sin escribir una sola
    fila. Es el estado por omision a proposito — un endpoint destructivo cuyo modo
    por omision es el destructivo es un endpoint del que hay que acordarse de no
    usar.

    `confirmar=True` borra, en una transaccion, y solo si no hay trabajo humano.
    """
    conteo = await contar(db)
    empresas = int(
        (await db.execute(select(func.count()).select_from(CompanyModel))).scalar_one()
        or 0
    )
    usuarios = int(
        (await db.execute(select(func.count()).select_from(UserModel))).scalar_one()
        or 0
    )

    if not confirmar:
        return ResultadoReset(
            simulado=True,
            conteo=conteo,
            empresas_conservadas=empresas,
            usuarios_conservados=usuarios,
        )

    # El cerrojo 4. Va DESPUES del dry_run y ANTES de borrar, y es la unica
    # condicion que puede cancelar una limpieza ya confirmada.
    if conteo.hay_trabajo_humano:
        raise HayTrabajoHumano(conteo)

    # El orden importa y lo explica el docstring del modulo. `tickets` antes que
    # `ticket_documents`: el trigger append-only solo permite borrar el papel
    # cuando su ticket ya no existe.
    borrado = {
        "conciliaciones": await _borra(db, ReconciliationModel),
        "tickets": await _borra(db, TicketModel),
        "compras": await _borra(db, CompraModel),
        "movimientos": await _borra(db, MovimientoInventarioModel),
        "scan_files": await _borra(db, ScanFileModel),
        "scan_events": await _borra(db, ScanEventModel),
    }
    await db.commit()

    return ResultadoReset(
        simulado=False,
        conteo=conteo,
        borrado=borrado,
        empresas_conservadas=empresas,
        usuarios_conservados=usuarios,
    )


async def _borra(db: AsyncSession, model) -> int:
    """`DELETE` de una tabla entera, y cuantas filas eran.

    Sin `filter`: el endpoint es "deja la base limpia para las pruebas de
    escaneo", no "borra lo de este cliente". Y `synchronize_session=False`
    porque la sesion no se usa despues y recorrer cada fila en memoria solo
    para tirar la informacion es trabajo gratis.
    """
    from sqlalchemy import delete

    resultado = await db.execute(delete(model).execution_options(synchronize_session=False))
    return int(resultado.rowcount or 0)


class HayTrabajoHumano(Exception):
    """Hay correcciones o compras que una persona hizo y que no se rehacen.

    Es una excepcion y no un booleano porque la condicion debe **impedir** la
    operacion: si fuera un valor de retorno, el endpoint tendria que acordarse de
    no borrar despues, y ese es justo el punto donde un endpoint destructivo
    se vuelve peligroso.
    """

    def __init__(self, conteo: Conteo):
        self.conteo = conteo
        super().__init__("hay trabajo humano en la base")