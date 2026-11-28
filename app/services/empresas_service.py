"""Desactivar y eliminar empresas sin borrar su evidencia.

Tres preguntas distintas, que antes se mezclaban en un 409 que ofrecia una
accion inexistente:

1. **¿Tiene datos esta empresa?** Si los tiene, no se elimina: se desactiva.
2. **¿Quedaria el sistema sin empresa activa?** Con datos de trabajo vigentes,
   no. Crear una empresa no cuenta como dato: solo siembra su catalogo de
   cuentas, que es configuracion, no evidencia.
3. **¿Se desactiva o se elimina?** `inactivo` se puede reactivar; `eliminado`
   es un borrado logico y nadie vuelve a trabajar en esa empresa.

Lo que cuenta como dato es una fila de trabajo con `company_id`: comprobantes,
banco, mapeos contables, ledger del escaner, cierres, productos, compras,
kardex, cajas y movimientos de caja. `ticket_documents`, `compra_items` y
`reconciliations` no se cuentan por separado: son hijos de esas filas, y
contarlos dos veces haria que una sola compra pareciera dos datos. La
auditoria tampoco cuenta: `registrar_auditoria` no escribe `company_id` y
crear una empresa ya deja una fila, con lo que el cerrojo de "ultima activa"
siempre seria cierto y ninguna empresa podria eliminarse nunca.

SQLite no aplica las FK de `db/init.sql`, asi que los cerrojos viven AQUI tambien:
una comprobacion solo en la base daria verde en la suite y en produccion el
borrado se llevaria por delante la evidencia en cascada.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EstadoEmpresa
from app.models.accounting_mapping import AccountingMappingModel
from app.models.bank_transaction import BankTransactionModel
from app.models.caja_chica import CajaChicaModel, MovimientoCajaModel
from app.models.cierre_periodo import CierrePeriodoModel
from app.models.company import CompanyModel
from app.models.inventario import (
    CompraModel,
    MovimientoInventarioModel,
    ProductoModel,
)
from app.models.scan_file import ScanFileModel
from app.models.ticket import TicketModel


TABLAS_DE_TRABAJO = (
    TicketModel,
    BankTransactionModel,
    AccountingMappingModel,
    ScanFileModel,
    CierrePeriodoModel,
    ProductoModel,
    CompraModel,
    MovimientoInventarioModel,
    CajaChicaModel,
    MovimientoCajaModel,
)


async def _cuantas(db: AsyncSession, modelo, company_id=None) -> int:
    consulta = select(func.count()).select_from(modelo)
    if company_id is not None:
        consulta = consulta.where(modelo.company_id == company_id)
    return int(await db.scalar(consulta) or 0)


async def datos_de_la_empresa(db: AsyncSession, company_id) -> int:
    """Filas de trabajo de una empresa, contadas una sola vez por tabla."""
    total = 0
    for modelo in TABLAS_DE_TRABAJO:
        total += await _cuantas(db, modelo, company_id)
    return total


async def hay_datos_en_el_sistema(db: AsyncSession) -> bool:
    """Cualquier dato vigente, en cualquier empresa, activa o no.

    La primera fila que aparece basta: preguntar cuantas hay en todas las
    tablas seria trabajo extra para una respuesta que solo tiene dos valores.
    """
    for modelo in TABLAS_DE_TRABAJO:
        if await _cuantas(db, modelo) > 0:
            return True
    return False


async def cantidad_de_empresas_activas(db: AsyncSession) -> int:
    consulta = select(func.count()).select_from(CompanyModel).where(
        CompanyModel.estado == EstadoEmpresa.ACTIVO.value
    )
    return int(await db.scalar(consulta) or 0)


class EmpresaConDatosError(Exception):
    """La empresa tiene filas de trabajo: no se elimina, se desactiva."""

    def __init__(self, filas: int):
        self.filas = filas
        super().__init__(
            f"La empresa tiene {filas} filas de trabajo y no se puede eliminar. "
            "Desactívela para conservar su evidencia."
        )


class UltimaEmpresaActivaError(Exception):
    """Desactivar la ultima activa dejaria el sistema sin donde trabajar."""

    def __init__(self) -> None:
        super().__init__(
            "No se puede dejar el sistema sin ninguna empresa activa mientras "
            "existan datos de trabajo. Cree y active otra empresa primero."
        )


class EmpresaEliminadaError(Exception):
    """Una empresa eliminada no vuelve: el borrado logico no es una pausa."""

    def __init__(self) -> None:
        super().__init__(
            "La empresa está eliminada. Cree otra para volver a trabajar con ese RFC."
        )


async def verificar_activar(db: AsyncSession, company: CompanyModel) -> None:
    if company.estado == EstadoEmpresa.ELIMINADO.value:
        raise EmpresaEliminadaError()


async def verificar_desactivar(db: AsyncSession, company: CompanyModel) -> None:
    """La baja es reversible, pero no puede dejar el sistema sin empresa activa."""
    if company.estado != EstadoEmpresa.ACTIVO.value:
        return
    if await cantidad_de_empresas_activas(db) == 1 and await hay_datos_en_el_sistema(db):
        raise UltimaEmpresaActivaError()


async def verificar_eliminar(db: AsyncSession, company: CompanyModel) -> None:
    """Dos cerrojos: datos en ESTA empresa, y ultima activa con datos en el sistema."""
    filas = await datos_de_la_empresa(db, company.id)
    if filas:
        raise EmpresaConDatosError(filas)
    await verificar_desactivar(db, company)


def cambiar_estado(
    db: AsyncSession, company: CompanyModel, estado: EstadoEmpresa
) -> bool:
    """Escribe el estado y devuelve si hubo cambio (idempotente).

    No hace `commit`: el cambio y su auditoria tienen que entrar juntos, o el
    rastro podria decir que se desactivo una empresa que sigue activa. Es
    sincrona a proposito: no consulta la base y por eso no puede fallar a mitad.
    """
    if company.estado == estado.value:
        return False
    company.estado = estado.value
    return True
