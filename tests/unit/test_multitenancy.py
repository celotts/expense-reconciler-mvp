"""Multi-empresa y roles de verdad (P0-1).

Lo que estos tests demuestran, y por que existen:

- **Fuga (el test obligatorio):** el usuario A pide el ticket de B por ID y
  recibe 404, no 403. Un 403 confirmaria que el ticket existe; un 404 no dice
  nada. Y la respuesta no lleva ningun dato de B.
- **Listados:** sin empresa en el query, cada usuario solo ve su empresa. Pedir
  otra empresa da 404.
- **Sin empresa no se ve nada:** una cuenta sin empresa asignada recibe 404 en
  todo, salvo que sea el operador admin de arranque, que puede pedir una
  empresa explicita.
- **Roles:** solo lectura no escribe (403); autorizar pide el permiso
  "aprobar" (403 para digitalizador).
- **P0-6:** `GET /tipos-gasto` ya no da 500: `UserModel` tiene `company_id` y
  el router esta registrado.
- **Registro:** crea empresa + admin + trial de 30 dias en una transaccion.

NOTA: autorizar/rechazar con un aprobador NO se prueba por HTTP aqui. El
servicio usa `SET LOCAL sesion.autorizacion_permitida` (Postgres) y en SQLite
eso revienta: es una limitacion preexistente del servicio, no de P0-1. Lo que
si se prueba es que la puerta de rol esta conectada (digitalizador -> 403).
"""

from __future__ import annotations

import base64
import json
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.security import hashear_contrasena
from app.models.company import CompanyModel
from app.models.ticket import TicketModel
from app.models.user import UserModel

CONTRASENA = "contrasena-de-prueba"


async def _otra_empresa(db_session, nombre: str = "Otra Empresa") -> CompanyModel:
    empresa = CompanyModel(
        name=nombre, tax_id=f"OTR-{uuid4().hex[:9].upper()}"
    )
    db_session.add(empresa)
    await db_session.commit()
    await db_session.refresh(empresa)
    return empresa


async def _ticket_en(db_session, empresa: CompanyModel, proveedor: str, total: str = "100.00") -> TicketModel:
    ticket = TicketModel(
        company_id=empresa.id,
        provider_name=proveedor,
        provider_tax_id="TST010101XXX",
        total_amount=Decimal(total),
        tax_amount=Decimal("16.00"),
        expense_date=date(2026, 10, 6),
        category="SUPERMERCADO",
        raw_text=f"Ticket de {proveedor} - Total: {total}",
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


async def _usuario_en(
    db_session,
    company_id: UUID | None,
    perfil: str = "digitalizador",
) -> UserModel:
    usuario = UserModel(
        email=f"{perfil}-{uuid4().hex[:8]}@test.local",
        nombre=f"{perfil.title()} de Prueba",
        password_hash=hashear_contrasena(CONTRASENA),
        perfil=perfil,
        company_id=company_id,
    )
    db_session.add(usuario)
    await db_session.commit()
    await db_session.refresh(usuario)
    return usuario


@pytest.fixture
async def cliente_como(db_session):
    """Fabrica clientes HTTP autenticados como el usuario que se le pase.

    Igual que `async_client` de conftest, pero con el usuario elegido: el
    `async_client` siempre es el operador de `test_company`.
    """
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_db
    from app.core.deps import get_current_user
    from app.main import app

    creados = []

    async def _fabrica(usuario: UserModel) -> AsyncClient:
        async def override_get_db():
            yield db_session

        async def override_usuario():
            return usuario

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_usuario
        cliente = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        creados.append(cliente)
        return cliente

    yield _fabrica
    for cliente in creados:
        await cliente.aclose()
    app.dependency_overrides.clear()


def _claims_del_token(token: str) -> dict:
    cuerpo = token.split(".")[1]
    cuerpo += "=" * (-len(cuerpo) % 4)
    return json.loads(base64.urlsafe_b64decode(cuerpo))


class TestFugaEntreEmpresas:
    """El test obligatorio de P0-1: A no puede tocar lo de B."""

    async def test_usuario_a_pide_ticket_de_b_por_id_da_404(
        self, async_client, db_session, test_company
    ):
        otra = await _otra_empresa(db_session)
        ticket_b = await _ticket_en(db_session, otra, "EMPRESA B SECRETA")

        r = await async_client.get(f"/api/v1/tickets/{ticket_b.id}")
        assert r.status_code == 404, r.text
        # La respuesta no lleva ningun dato de B: ni el proveedor ni el total.
        assert "SECRETA" not in r.text
        assert str(ticket_b.id) not in r.text

    async def test_listado_sin_empresa_solo_muestra_la_propia(
        self, async_client, db_session, test_company
    ):
        otra = await _otra_empresa(db_session)
        await _ticket_en(db_session, test_company, "MIA")
        await _ticket_en(db_session, otra, "DE LA OTRA")

        r = await async_client.get("/api/v1/tickets/")
        assert r.status_code == 200, r.text
        proveedores = [t["provider_name"] for t in r.json()]
        assert "MIA" in proveedores
        assert "DE LA OTRA" not in proveedores

    async def test_pedir_otra_empresa_en_el_listado_da_404(
        self, async_client, db_session, test_company
    ):
        otra = await _otra_empresa(db_session)
        r = await async_client.get(
            "/api/v1/tickets/", params={"company_id": str(otra.id)}
        )
        assert r.status_code == 404, r.text

    async def test_borrar_ticket_de_otra_empresa_da_404(
        self, async_client, db_session, test_company
    ):
        otra = await _otra_empresa(db_session)
        ticket_b = await _ticket_en(db_session, otra, "EMPRESA B SECRETA")
        r = await async_client.delete(f"/api/v1/tickets/{ticket_b.id}")
        assert r.status_code == 404, r.text
        # Y sigue existiendo: el 404 no borro nada.
        sigue = (
            await db_session.execute(
                select(TicketModel).where(TicketModel.id == ticket_b.id)
            )
        ).scalar_one_or_none()
        assert sigue is not None

    async def test_modificar_ticket_de_otra_empresa_da_404(
        self, async_client, db_session, test_company
    ):
        otra = await _otra_empresa(db_session)
        ticket_b = await _ticket_en(db_session, otra, "EMPRESA B SECRETA")
        r = await async_client.patch(
            f"/api/v1/tickets/{ticket_b.id}",
            json={"category": "ROBADO"},
        )
        assert r.status_code == 404, r.text


class TestUsuarioSinEmpresa:
    async def test_sin_empresa_no_ve_nada(
        self, cliente_como, db_session, test_company
    ):
        ticket_a = await _ticket_en(db_session, test_company, "MIA")
        sin_empresa = await _usuario_en(db_session, None, "digitalizador")
        cliente = await cliente_como(sin_empresa)

        assert (await cliente.get(f"/api/v1/tickets/{ticket_a.id}")).status_code == 404
        assert (await cliente.get("/api/v1/tickets/")).status_code == 404
        assert (await cliente.get("/api/v1/usuarios")).status_code == 404

    async def test_operador_admin_sin_empresa_pide_empresa_explicita(
        self, cliente_como, db_session, test_company
    ):
        ticket_a = await _ticket_en(db_session, test_company, "MIA")
        operador = await _usuario_en(db_session, None, "admin")
        cliente = await cliente_como(operador)

        # Sin empresa explicita, ni el admin de arranque ve nada.
        assert (await cliente.get(f"/api/v1/tickets/{ticket_a.id}")).status_code == 404
        # Con la empresa explicita (y existente), si.
        r = await cliente.get(
            f"/api/v1/tickets/{ticket_a.id}",
            params={"company_id": str(test_company.id)},
        )
        assert r.status_code == 200, r.text
        assert r.json()["provider_name"] == "MIA"
        # Y con una empresa que no existe, 404 igual.
        r = await cliente.get(
            f"/api/v1/tickets/{ticket_a.id}",
            params={"company_id": str(uuid4())},
        )
        assert r.status_code == 404


class TestRoles:
    async def test_solo_lectura_no_puede_crear_ticket(
        self, cliente_como, db_session, test_company
    ):
        lector = await _usuario_en(db_session, test_company.id, "solo_lectura")
        cliente = await cliente_como(lector)
        r = await cliente.post(
            "/api/v1/tickets/",
            json={
                "company_id": str(test_company.id),
                "provider_name": "OXXO",
                "total_amount": "50.00",
                "expense_date": "2026-10-06",
            },
        )
        assert r.status_code == 403, r.text

    async def test_solo_lectura_si_puede_leer(
        self, cliente_como, db_session, test_company
    ):
        await _ticket_en(db_session, test_company, "MIA")
        lector = await _usuario_en(db_session, test_company.id, "solo_lectura")
        cliente = await cliente_como(lector)
        r = await cliente.get("/api/v1/tickets/")
        assert r.status_code == 200, r.text

    async def test_digitalizador_no_puede_autorizar(
        self, async_client, db_session, test_company
    ):
        # `async_client` es digitalizador: la puerta de "aprobar" lo corta
        # antes de llegar al servicio (ver la nota del modulo).
        ticket = await _ticket_en(db_session, test_company, "MIA")
        r = await async_client.post(
            f"/api/v1/tipos-gasto/tickets/{ticket.id}/autorizar", json={}
        )
        assert r.status_code == 403, r.text

    async def test_solo_lectura_no_puede_crear_tipo_gasto(
        self, cliente_como, db_session, test_company
    ):
        lector = await _usuario_en(db_session, test_company.id, "solo_lectura")
        cliente = await cliente_como(lector)
        r = await cliente.post(
            "/api/v1/tipos-gasto",
            json={"clave": "PRUEBA", "nombre": "Prueba"},
        )
        assert r.status_code == 403, r.text

    def test_permisos_por_rol(self):
        # La tabla de permisos, sin HTTP: cada rol tiene lo que dice tener.
        from app.core.deps import PERMISOS_POR_ROL, puede, rol_de

        class _U:
            def __init__(self, perfil):
                self.perfil = perfil

        assert rol_de(_U("admin")) == "admin"
        assert rol_de(_U("develop")) == "admin"  # perfil viejo, mismo rol
        assert rol_de(_U("programador")) == "admin"
        assert rol_de(_U("qa")) == "solo_lectura"  # auditor, solo lee
        assert rol_de(_U("raro")) == "digitalizador"  # desconocido, el default
        assert puede(_U("admin"), "administrar")
        assert puede(_U("contador"), "cerrar")
        assert puede(_U("aprobador"), "aprobar")
        assert not puede(_U("aprobador"), "escribir")
        assert not puede(_U("digitalizador"), "aprobar")
        assert PERMISOS_POR_ROL["solo_lectura"] == {"leer"}


class TestP0SeisTiposGasto:
    """El bug P0-6: el router entero daba 500 y ni estaba registrado."""

    async def test_listar_tipos_gasto_ya_no_da_500(self, async_client):
        r = await async_client.get("/api/v1/tipos-gasto")
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

    async def test_crear_tipo_gasto_en_mi_empresa(self, async_client, test_company):
        r = await async_client.post(
            "/api/v1/tipos-gasto",
            json={"clave": "VIAJE", "nombre": "Viajes"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["company_id"] == str(test_company.id)
        assert r.json()["clave"] == "VIAJE"


class TestRegistro:
    async def test_registro_crea_empresa_admin_y_trial(
        self, async_client_sin_autenticar, db_session
    ):
        correo = f"nueva-{uuid4().hex[:8]}@empresa.mx"
        rfc = f"REG{uuid4().hex[:9].upper()}"
        r = await async_client_sin_autenticar.post(
            "/auth/registro",
            json={
                "email": correo,
                "nombre": "Nueva Empresa",
                "password": "PasswordSegura123!",
                "empresa_nombre": "Mi Empresa Nueva",
                "tax_id": rfc,
            },
        )
        assert r.status_code == 201, r.text
        cuerpo = r.json()
        assert cuerpo["user"]["perfil"] == "admin"
        assert cuerpo["user"]["company_id"] is not None

        empresa = (
            await db_session.execute(
                select(CompanyModel).where(CompanyModel.tax_id == rfc)
            )
        ).scalar_one()
        assert empresa.plan == "basico"
        assert empresa.tickets_usados_este_mes == 0
        # Trial de 30 dias: ni ayer ni dentro de un ano.
        from app.core.time import utcnow

        assert empresa.trial_hasta is not None
        assert utcnow() < empresa.trial_hasta < utcnow() + timedelta(days=31)
        # El catalogo de tipos de gasto se sembro con la empresa.
        from app.models.tipo_gasto import TipoGastoModel

        catalogo = (
            await db_session.execute(
                select(TipoGastoModel).where(
                    TipoGastoModel.company_id == empresa.id
                )
            )
        ).scalars().all()
        assert len(catalogo) > 0

        # Y con esas credenciales se entra, con la empresa en el token.
        login = await async_client_sin_autenticar.post(
            "/auth/login",
            json={"email": correo, "password": "PasswordSegura123!"},
        )
        assert login.status_code == 200, login.text
        claims = _claims_del_token(login.json()["access_token"])
        assert claims["company_id"] == str(empresa.id)

    async def test_registro_con_correo_duplicado_da_409(
        self, async_client_sin_autenticar
    ):
        correo = f"duplicado-{uuid4().hex[:8]}@empresa.mx"
        cuerpo = {
            "email": correo,
            "nombre": "Duplicada",
            "password": "PasswordSegura123!",
            "empresa_nombre": "Empresa Uno",
            "tax_id": f"DUP{uuid4().hex[:9].upper()}",
        }
        assert (
            await async_client_sin_autenticar.post("/auth/registro", json=cuerpo)
        ).status_code == 201
        cuerpo["tax_id"] = f"DUP{uuid4().hex[:9].upper()}"
        r = await async_client_sin_autenticar.post("/auth/registro", json=cuerpo)
        assert r.status_code == 409, r.text

    async def test_registro_sin_rfc_usa_marcador(
        self, async_client_sin_autenticar, db_session
    ):
        r = await async_client_sin_autenticar.post(
            "/auth/registro",
            json={
                "email": f"sinrfc-{uuid4().hex[:8]}@empresa.mx",
                "nombre": "Sin RFC",
                "password": "PasswordSegura123!",
                "empresa_nombre": "Empresa Sin RFC",
            },
        )
        assert r.status_code == 201, r.text
        empresa_id = r.json()["user"]["company_id"]
        empresa = (
            await db_session.execute(
                select(CompanyModel).where(CompanyModel.id == UUID(empresa_id))
            )
        ).scalar_one()
        assert empresa.tax_id.startswith("SIN-RFC-")


class TestUsuariosConEmpresa:
    async def test_listar_solo_muestra_mi_empresa(
        self, async_client, db_session, test_company
    ):
        otra = await _otra_empresa(db_session)
        usuario_b = await _usuario_en(db_session, otra.id, "digitalizador")
        r = await async_client.get("/api/v1/usuarios")
        assert r.status_code == 200, r.text
        correos = [c["email"] for c in r.json()]
        assert usuario_b.email not in correos

    async def test_ver_cuenta_de_otra_empresa_da_404(
        self, async_client, db_session, test_company
    ):
        otra = await _otra_empresa(db_session)
        usuario_b = await _usuario_en(db_session, otra.id, "digitalizador")
        r = await async_client.get(f"/api/v1/usuarios/{usuario_b.id}")
        assert r.status_code == 404, r.text

    async def test_crear_usuario_con_empresa_inexistente_da_404(
        self, async_client
    ):
        r = await async_client.post(
            "/api/v1/usuarios",
            json={
                "email": f"nuevo-{uuid4().hex[:8]}@empresa.mx",
                "nombre": "Nuevo",
                "password": "PasswordSegura123!",
                "company_id": str(uuid4()),
            },
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 404, r.text

    async def test_digitalizador_no_puede_cambiar_la_empresa_de_una_cuenta(
        self, async_client, db_session, usuario_de_prueba, test_company
    ):
        otra = await _otra_empresa(db_session)
        r = await async_client.patch(
            f"/api/v1/usuarios/{usuario_de_prueba.id}",
            json={"company_id": str(otra.id)},
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 403, r.text

    async def test_admin_si_puede_cambiar_la_empresa_de_una_cuenta(
        self, async_client, db_session, usuario_de_prueba, test_company
    ):
        otra = await _otra_empresa(db_session)
        subordinado = await _usuario_en(db_session, test_company.id, "digitalizador")
        usuario_de_prueba.perfil = "admin"
        await db_session.commit()
        r = await async_client.patch(
            f"/api/v1/usuarios/{subordinado.id}",
            json={"company_id": str(otra.id)},
            headers={"X-Contrasena-Actual": CONTRASENA},
        )
        assert r.status_code == 200, r.text
        assert r.json()["company_id"] == str(otra.id)



