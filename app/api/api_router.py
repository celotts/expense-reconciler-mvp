from fastapi import APIRouter, Depends

from app.api.auth import router as auth_router
from app.api.auditoria import router as auditoria_router
from app.api.bank_transactions import router as bank_transactions_router
from app.api.caja_chica import router as caja_chica_router
from app.api.categorias import router as categorias_router
from app.api.duplicados import router as duplicados_router
from app.api.companies import router as companies_router
from app.api.dashboard import router as dashboard_router
from app.api.inventario import router as inventario_router
from app.api.ia_asistida import router as ia_asistida_router
from app.api.reconciliations import router as reconciliations_router
from app.api.reports import router as reports_router
from app.api.scans import router as scan_router
from app.api.sistema import router as sistema_router
from app.api.tickets import router as tickets_router
from app.api.tipos_gasto import router as tipos_gasto_router
from app.api.usuarios import router as usuarios_router
from app.core.deps import empresa_activa, get_current_user, guardia_de_permisos
from app.routers.expenses import router as expenses_router

api_router = APIRouter()

# La misma puerta para TODOS los routers protegidos, escrita una sola vez.
# Anadir un router nuevo con solo `get_current_user` lo dejaria sin guardia de
# permisos, que es exactamente el fallo que esta lista evita para el token.
# `auth` queda fuera a proposito: `POST /auth/login` no puede traer token ni
# membresia resuelta, y es la puerta por la que se obtiene la sesion.
PROTEGIDOS = [Depends(get_current_user), Depends(guardia_de_permisos)]

# ---------------------------------------------------------------------------
# La puerta se instala primero y SIN proteccion: `POST /auth/login` es
# justamente la peticion que no puede traer un token, porque todavia no hay
# ninguno. `GET /auth/me` si lo exige, con su propia dependencia, para que
# "dice que soy yo" no dependa de que el router entero este protegido.
# ---------------------------------------------------------------------------
api_router.include_router(auth_router, prefix="/auth", tags=["Auth"])
api_router.include_router(
    auditoria_router,
    prefix="/auditoria",
    tags=["Auditoria"],
    dependencies=PROTEGIDOS,
)

# A partir de aqui, TODO lo demas pide token. Se pone a nivel de router, no
# endpoint por endpoint: escrito a mano serian 35 oportunidades de que el
# endpoint 36 quede abierto y no se note en la revision, porque la ausencia se
# ve igual que la presencia. Y un endpoint nuevo nace protegido.
# El prefijo NO se repite aqui: `dashboard.py` ya lo declara en su propio
# APIRouter. Ponerlo en los dos lados produce `/dashboard/dashboard/`, que es
# un 404 silencioso: el router del backend responde bien, el cliente pide otra
# cosa, y el error aparece como "el dashboard no carga" en vez de "la ruta
# esta mal". La unica forma de que no vuelva es no tener el prefijo en dos
# sitios, y los demas routers ya lo hacen asi.
api_router.include_router(
    dashboard_router,
    tags=["Dashboard"],
    dependencies=PROTEGIDOS,
)
# `categorias.py` ya declara su prefijo en su propio APIRouter, igual que
# `dashboard.py`. Ponerlo tambien aqui produce `/categorias/categorias`, que es
# un 404 silencioso: el cliente pide `/categorias` y el servidor responde en
# otra ruta, y el desplegable de la pantalla de Tickets queda vacio sin error.
api_router.include_router(
    categorias_router,
    tags=["Categorias"],
    dependencies=PROTEGIDOS,
)
api_router.include_router(
    companies_router,
    prefix="/companies",
    tags=["Companies"],
    dependencies=PROTEGIDOS,
)
api_router.include_router(
    tickets_router,
    prefix="/tickets",
    tags=["Tickets"],
    dependencies=PROTEGIDOS,
)
# IA asistida de SOLO LECTURA: hereda la guardia de permisos como cualquier
# otro router protegido, y ademas resuelve la empresa dentro del endpoint para
# que un ticket ajeno no existe. No anade escrituras ni cambia el gate.
api_router.include_router(
    ia_asistida_router,
    prefix="/ia-asistida",
    tags=["IA asistida"],
    dependencies=PROTEGIDOS,
)
# Carga explicita por lote. Es la misma captura que `/tickets` y que `/scan`:
# `procesar_archivo` con `source_type=BULK`. El token se exige a nivel de router
# como en todos los demas, y ademas el endpoint toma `UsuarioActual` porque
# necesita el correo para firmar el override de periodo cerrado. Que el schema
# de respuesta sea `ScanItemResponse` es lo que hace que los datos del papel
# y el veredicto del gate viajen juntos y no se puedan leer por separado.
api_router.include_router(
    expenses_router,
    prefix="/expenses",
    tags=["Expenses"],
    dependencies=PROTEGIDOS,
)
# El escaner de carpeta. Mismo criterio que los demas: token obligatorio a nivel
# de router. Que lea el disco no lo hace mas peligroso de lo que ya es el resto
# de la API, y que necesite token no lo hace mas seguro si se le puede pedir
# una carpeta por parametro: por eso no hay ningun campo de carpeta en su
# schema, y la ruta sale de `TICKETS_INPUT_DIR`.
api_router.include_router(
    scan_router,
    prefix="/scan",
    tags=["Scan"],
    dependencies=PROTEGIDOS,
)
api_router.include_router(
    bank_transactions_router,
    prefix="/bank-transactions",
    tags=["Bank Transactions"],
    dependencies=PROTEGIDOS,
)
api_router.include_router(
    reconciliations_router,
    prefix="/reconciliations",
    tags=["Reconciliations"],
    dependencies=PROTEGIDOS,
)
# Inventario. El token obligatorio por el mismo motivo que el resto.
#
# El prefijo va aqui y NO en el APIRouter del router, a diferencia de
# `dashboard.py` y `categorias.py`, que ya lo declaran en su propio lado. La
# regla es la misma en los dos casos y esta escrita arriba: el prefijo se
# declara en UN solo sitio. Este router declara el suyo aqui porque no tiene
# razon para hardcodear "/inventario" dentro del modulo que lo implementa.
api_router.include_router(
    inventario_router,
    prefix="/inventario",
    tags=["Inventario"],
    dependencies=PROTEGIDOS,
)
# Cuentas. Token a nivel de router, como todos los demas.
#
# Los endpoints que dan de baja o rotan una contrasena piden ADEMAS la contrasena
# del actor (`UsuarioVerificado`, o sea el header `X-Contrasena-Actual`), y no se
# pone aqui como dependencia de router porque los de lectura no la necesitan y
# ponerla aqui obligaria a escribirla para mirar una lista. Ver la nota larga de
# `app/core/deps.py`.
api_router.include_router(
    usuarios_router,
    prefix="/usuarios",
    tags=["Usuarios"],
    dependencies=PROTEGIDOS,
)
# El informe de cierre mensual. Token a nivel de router, como todos los demas.
#
# Es la ruta que hace que el producto entregue un DOCUMENTO y no solo datos, asi
# que no se le abrevia nada: quien firma el informe es el contador, y su nombre
# sale del token (`UsuarioActual`), nunca de un campo del cuerpo. Aceptarlo en el
# body seria permitir que un informe salga firmado por quien no lo reviso, que es
# exactamente lo que R5 y la regla 23 de `AGENTS.md` prohiben.
api_router.include_router(
    reports_router,
    prefix="/reports",
    tags=["Reports"],
    dependencies=PROTEGIDOS,
)
# Sistema: backup, estado, version. Token obligatorio como el resto.
api_router.include_router(
    sistema_router,
    prefix="/sistema",
    tags=["Sistema"],
    dependencies=PROTEGIDOS,
)
# Caja chica y duplicados fiscales. Ademas del token se exige la empresa activa
# A NIVEL DE ROUTER, por el mismo motivo que el token: escrito endpoint por
# endpoint, el endpoint nuevo que se anada mañana nace sin la puerta y nadie lo
# nota. La empresa se resuelve en BD contra las membresias, no se lee del token.
api_router.include_router(
    caja_chica_router,
    prefix="/caja-chica",
    tags=["Caja chica"],
    dependencies=[*PROTEGIDOS, Depends(empresa_activa)],
)
api_router.include_router(
    duplicados_router,
    prefix="/duplicados",
    tags=["Duplicados"],
    dependencies=[*PROTEGIDOS, Depends(empresa_activa)],
)
# El catalogo de cuentas de gasto. El router trae su propio `/tipos-gasto`, asi
# que el prefijo NO se repite aqui (misma regla que `categorias.py`). La empresa
# se exige a nivel de router, ademas de en cada endpoint: un endpoint nuevo que
# se anada aqui nace con la misma puerta, y la guardia de permisos de arriba
# clasifica la autorizacion como APROBAR y no como CREAR.
api_router.include_router(
    tipos_gasto_router,
    tags=["Tipos de gasto"],
    dependencies=[*PROTEGIDOS, Depends(empresa_activa)],
)
