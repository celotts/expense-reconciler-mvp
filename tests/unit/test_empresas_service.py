"""La baja y el borrado logico de empresas: cerrojos en el servicio, no en la base.

SQLite activa las FK en la suite, pero `ON DELETE CASCADE` no protege nada: un
borrado en cascada borra la evidencia sin que ningun `IntegrityError` avise. Por
eso los cerrojos se comprueban antes de tocar la fila, y aqui se prueban con la
misma funcion que llama el router.

`estado` tiene TRES valores. Un booleano no podia decir si una empresa se puede
reactivar (`inactivo`) o si nadie debe volver a usarla (`eliminado`).
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.enums import EstadoEmpresa
from app.models.bank_transaction import BankTransactionModel
from app.models.company import CompanyModel
from app.services import empresas_service


async def _empresa(db_session, nombre: str) -> CompanyModel:
    empresa = CompanyModel(name=nombre, tax_id=f"EMP{uuid4().hex[:10].upper()}")
    db_session.add(empresa)
    await db_session.commit()
    await db_session.refresh(empresa)
    return empresa


async def _movimiento(db_session, empresa: CompanyModel) -> None:
    db_session.add(
        BankTransactionModel(
            company_id=empresa.id,
            transaction_date=date(2026, 1, 15),
            amount=Decimal("-125.50"),
            description="Pago de prueba",
        )
    )
    await db_session.commit()


class TestDatosDeLaEmpresa:
    @pytest.mark.asyncio
    async def test_cuenta_filas_de_trabajo(self, db_session):
        empresa = await _empresa(db_session, "Con datos")
        await _movimiento(db_session, empresa)
        assert await empresas_service.datos_de_la_empresa(db_session, empresa.id) >= 1

    @pytest.mark.asyncio
    async def test_catalogo_sembrado_no_cuenta_como_dato(self, db_session):
        """Crear una empresa siembra tipos de gasto: eso es configuracion.

        Si contara, toda empresa recien creada tendria "movimiento" y ninguna
        podria borrarse nunca, aunque nadie hubiera procesado un comprobante.
        """
        empresa = await _empresa(db_session, "Sin datos")
        assert await empresas_service.datos_de_la_empresa(db_session, empresa.id) == 0
        assert not await empresas_service.hay_datos_en_el_sistema(db_session)

    @pytest.mark.asyncio
    async def test_hay_datos_ve_tambien_las_empresas_dadas_de_baja(self, db_session):
        empresa = await _empresa(db_session, "De baja con datos")
        await _movimiento(db_session, empresa)
        empresas_service.cambiar_estado(db_session, empresa, EstadoEmpresa.INACTIVO)
        await db_session.commit()
        assert await empresas_service.hay_datos_en_el_sistema(db_session)


class TestUltimaEmpresaActiva:
    @pytest.mark.asyncio
    async def test_no_se_puede_bajar_la_unica_activa_con_datos(self, db_session):
        empresa = await _empresa(db_session, "Unica")
        await _movimiento(db_session, empresa)
        with pytest.raises(empresas_service.UltimaEmpresaActivaError):
            await empresas_service.verificar_desactivar(db_session, empresa)

    @pytest.mark.asyncio
    async def test_eliminar_exige_empresa_vacia_y_no_dejar_sistema_sin_activa(self, db_session):
        empresa = await _empresa(db_session, "Unica con datos")
        await _movimiento(db_session, empresa)
        with pytest.raises(empresas_service.EmpresaConDatosError):
            await empresas_service.verificar_eliminar(db_session, empresa)

    @pytest.mark.asyncio
    async def test_sin_datos_si_puede_bajar_la_unica(self, db_session):
        empresa = await _empresa(db_session, "Unica vacia")
        await empresas_service.verificar_desactivar(db_session, empresa)
        assert await empresas_service.cantidad_de_empresas_activas(db_session) == 1

    @pytest.mark.asyncio
    async def test_con_otra_activa_si_puede_bajar(self, db_session):
        empresa = await _empresa(db_session, "Con datos")
        otra = await _empresa(db_session, "Otra activa")
        await _movimiento(db_session, empresa)
        await empresas_service.verificar_desactivar(db_session, empresa)
        assert await empresas_service.cantidad_de_empresas_activas(db_session) == 2
        assert otra.estado == EstadoEmpresa.ACTIVO.value

    @pytest.mark.asyncio
    async def test_cantidad_de_activas_excluye_las_dadas_de_baja(self, db_session):
        await _empresa(db_session, "Activa")
        bajada = await _empresa(db_session, "Bajada")
        empresas_service.cambiar_estado(db_session, bajada, EstadoEmpresa.INACTIVO)
        await db_session.commit()
        assert await empresas_service.cantidad_de_empresas_activas(db_session) == 1


class TestLosTresEstados:
    def test_init_sql_espeja_la_columna_y_registra_la_migracion(self):
        """La base nueva nace con `estado`; la existente lo recibe en `0029`.

        La suite construye el esquema desde los modelos, asi que sin esta
        comprobacion los dos DDL pueden divergir sin que ningun test lo vea.
        """
        init = (Path(__file__).resolve().parents[2] / "db/init.sql").read_text()
        assert "estado VARCHAR(20) NOT NULL DEFAULT 'activo'" in init
        assert "estado IN ('activo', 'inactivo', 'eliminado')" in init
        assert "('0029_estado_empresa.sql')" in init

    def test_los_tres_estados_son_los_de_la_base(self):
        assert [e.value for e in EstadoEmpresa] == ["activo", "inactivo", "eliminado"]

    @pytest.mark.asyncio
    async def test_cambiar_estado_es_idempotente(self, db_session):
        empresa = await _empresa(db_session, "Baja")
        assert empresas_service.cambiar_estado(
            db_session, empresa, EstadoEmpresa.INACTIVO
        ) is True
        await db_session.commit()
        assert empresas_service.cambiar_estado(
            db_session, empresa, EstadoEmpresa.INACTIVO
        ) is False
        assert empresa.estado == EstadoEmpresa.INACTIVO.value

    @pytest.mark.asyncio
    async def test_eliminar_no_borra_la_fila(self, db_session):
        empresa = await _empresa(db_session, "Eliminada")
        empresas_service.cambiar_estado(db_session, empresa, EstadoEmpresa.ELIMINADO)
        await db_session.commit()
        assert await db_session.get(CompanyModel, empresa.id) is not None
        assert empresa.estado == EstadoEmpresa.ELIMINADO.value

    @pytest.mark.asyncio
    async def test_una_eliminada_no_se_reactiva(self, db_session):
        empresa = await _empresa(db_session, "Eliminada")
        empresas_service.cambiar_estado(db_session, empresa, EstadoEmpresa.ELIMINADO)
        await db_session.commit()
        with pytest.raises(empresas_service.EmpresaEliminadaError):
            await empresas_service.verificar_activar(db_session, empresa)
