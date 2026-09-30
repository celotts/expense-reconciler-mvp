"""El periodo que alguien firmo como cerrado, y con que informacion.

Por que existe
--------------

R3 del contrato (docs/contrato-producto.md:176) dice que el informe no puede
declarar un periodo cerrado si hay tickets sin categoria o sin conciliar. Y anade
la excepcion: se puede, *"salvo que el contador lo marque explicitamente (y
entonces el informe registra que lo fue)"*.

La excepcion era la parte que no existia. Se verifico que no hay ningun estado
de periodo cerrado en los modelos, ni columna de quien lo cerro, ni tabla que lo
guarde. Sin esto, R3 se cumple a medias: el informe puede negarse a declarar
cerrado un periodo, pero no hay forma de registrar que un contador lo cerro a
sabiendas, y por lo tanto tampoco de que el informe lo diga.

Que guarda esta tabla
---------------------

Una fila por `(company_id, periodo)`, que es el grano del informe. No es un
historico de versiones: se sobrescribe. La justificacion de por que NO es un log
de eventos esta en D5 del contrato ("un periodo cerrado genera su informe y ese
informe es inmutable"): congelar el informe es la Fase 4, y armar el log antes de
saber que se reabre seria construir Fase 4 adelantada.

`cerrado_por` es texto y no llave foranea
-----------------------------------------

Copia el precedente de `tickets.spot_checked_by` (`db/init.sql:83-85`), que dice
exactamente por que: *"Quien registro el veredicto, desde el token. Texto y no
llave foranea a proposito: el veredicto tiene que sobrevivir a la baja de la
cuenta."*

Aplica igual aqui y con mas fuerza: un cierre fiscal que desaparece porque se dio
de baja una cuenta es peor que un veredicto que desaparece. El nombre es la
prueba de que alguien se hizo cargo, y una prueba que se borra sola no es una
prueba.

`pendientes_al_cerrar`: que se sabia cuando se cerro
---------------------------------------------------

Sin esta columna, "cerrado con 12 tickets sin conciliar" y "cerrado limpio" son
lo mismo para cualquier que mire la tabla despues. Y no se puede reconstruir:
los tickets se pueden haberse conciliado ya, y entonces el dato original se
perdió. R3 promete que el contador cerro *a sabiendas*, y esto es lo que sostiene
esa parte de la promesa.

Es `JSON` y no tres columnas `INTEGER` a proposito. El dato se muestra en el
informe y no se agrega ni se cruza; con JSON agregar un tipo de pendiente nuevo
no es una migracion. Si algun dia hace falta tabularlo ("cuantas discrepancias
suman todos los cierres del ano"), ahi si son columnas.

`huella`: si los datos se movieron despues de cerrar
----------------------------------------------------

R4 (`docs/contrato-producto.md:179`) define el determinismo como *"mismo periodo
+ misma base de datos -> PDF byte-identico"*. Fijate en la segunda mitad: el
informe es funcion del estado de la base, no un documento congelado. Eso esta
bien para el determinismo y es un problema para la firma: si enero esta cerrado
y luego se sube un ticket de enero, el informe de enero cambia, y quien lo
firmo en enero y lo lee en marzo ve otros numeros sin ninguna senal de que son
otros.

`huella` es el SHA-256 de la serializacion canonica del informe en el momento del
cierre. Al regenerarlo se compara y, si no coincide, el informe dice que el
periodo fue cerrado con otra informacion. Es el "reabrirlo deja rastro" de D5
por una fraccion de su costo, y sale gratis de algo que R4 ya obliga a construir
(la serializacion determinista).

NULL y no cadena vacia
----------------------

Los dos son opcionales a proposito. Una tabla recien creada no tiene por que
tenerlos: se permite que el cierre se registre antes de que exista la huella
(migracion vieja, cierre hecho a mano). Lo que no se permite es inventarlos, y
`NULL` es la unica forma de decir "no se sabe".
"""

import uuid

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.time import utcnow

# --- Las constraints, y por que no son las del DDL ------------------------
#
# El DDL de Postgres (db/init.sql y db/migrations/0007_cierre_periodo.sql) usa
# la regex completa del contrato: ^\d{4}-(0[1-9]|1[0-2])$
#
# Aqui no se puede. En SQLite `~` es el operador de negacion a bits, no regex: una
# constraint con `~` en el modelo no validaria nada en los tests, que corren
# contra SQLite, y en el mejor de los casos no dira nada. AGENTS.md ya avisa de
# que "un test verde sobre SQLite no dice nada" de las tres cosas que SQLite
# ignora; esto seria una cuarta.
#
# Asi que cada constraint se replica aqui con lo que los dos motores entienden,
# y se acepta que sea mas debil. Es una decision, no una(descuido.

# Largo, guion en la posicion 5, y mes entre '01' y '12'. La comparacion de
# cadenas de dos caracteres ordena igual en Postgres y en SQLite.
#
# Lo que esta NO deja pasar en ningun motor: `2026-13`, `2026-00`, `2026-1`,
# `202601` y `2026_01`.
#
# Lo que NO deja pasar y el DDL si: `abcd-01` (anio no numerico). Para eso estan
# la regex de Postgres y el borde de la API, que devuelve 422 antes de la base.
_CHECK_PERIODO = (
    "length(periodo) = 7 "
    "AND substr(periodo, 5, 1) = '-' "
    "AND substr(periodo, 6, 2) BETWEEN '01' AND '12'"
)

# El largo nada mas. Expresar "solo hex" necesita `~` (Postgres) o `GLOB` (SQLite)
# y no hay forma comun, asi que el alfabeto se lo dejan Postgres y la API. Lo que
# atrapa aqui es el error que de verdad se cuela: una huella de 63 caracteres, o
# el string "NOESUNAHUELLA" que alguien pone para probar. El DDL los rechaza a los
# dos por el otro motivo.
_CHECK_HUELLA = "huella IS NULL OR length(huella) = 64"

# EL SELLO NO SE COMPRUEBA EN EL MODELO, A PROPOSITO.
#
# Hay dos reasons y los dos se comprobaron, no se supusieron:
#
# 1. `now()` no existe en SQLite. Una constraint con `now()` revienta el CREATE
#    TABLE con "no such function: now", y como los tests arman el esquema sobre
#    SQLite, tumbaba los 229 que crean tablas.
#
# 2. `CURRENT_TIMESTAMP` si existe en los dos, pero en SQLite devuelve
#    '2026-09-30 18:07:24' (sin microsegundos) mientras que SQLAlchemy escribe un
#    DateTime como '2026-09-30 18:07:24.704058'. La comparacion es de cadenas, y
#    '...24.704058' > '...24', asi que la constraint RECHAZA una fila valida:
#    insertar un cierre con la hora actual da IntegrityError. Medido.
#
# Arreglarlo exigiria una expresion de "ahora" que devuelva el mismo formato que
# escribe el driver, que es justo el tipo de detalle que se porta mal entre
# motores. Y una constraint que a veces rechaza filas buenas es peor que no
# tenerla: falla en produccion, en el camino correcto, y no avisa de nada.
#
# La constraint vive solo en el DDL de Postgres (db/init.sql y
# db/migrations/0007_cierre_periodo.sql), donde `cerrado_at <= now()` compara dos
# `timestamptz` de verdad y funciona. Postgres es el motor que corre el producto;
# SQLite solo corre tests. Un cliente que quisiera colar un cierre de 2099 tiene
# que pasar por la API, que valida antes de llegar aqui.
#
# Que no haya constraint aqui no significa que no haya que probarla: esta misma
# tabla no tendria ninguna defensa en SQLite hasta que se escriba el test que
# inserta una fila de verdad.


class CierrePeriodoModel(Base):
    """Un `(empresa, periodo)` que alguien marco como cerrado. Ver el modulo."""

    __tablename__ = "cierres_periodo"
    __table_args__ = (
        CheckConstraint(_CHECK_PERIODO, name="ck_cierres_periodo_formato"),
        CheckConstraint(_CHECK_HUELLA, name="ck_cierres_periodo_huella_hex"),
        # `ck_cierres_periodo_sello_sano` NO va aqui. Ver el bloque de arriba:
        # en SQLite rechazaba filas validas. Vive solo en el DDL de Postgres.
        # UNIQUE y no PK compuesta porque el grano del informe es
        # (empresa, periodo): dos cierres del mismo periodo serian ambiguos, y
        # "la fila mas reciente de un conjunto que no deberia existir" es una
        # respuesta que depende de un ORDER BY que alguien puede olvidar. Ademas
        # deja la PK la pone la app, como en el resto del esquema, y permite
        # colgarle referencias despues (una rectificacion) sin cambiar el grano.
        Index("ix_cierres_periodo_unico", "company_id", "periodo", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 'YYYY-MM', 7 caracteres. El dia no se guarda: el cierre es del mes, y meter
    # un dia aqui invita a que alguien lo lea como si el periodo fuera de un dia.
    periodo = Column(String(7), nullable=False)
    # Texto y no FK. Ver el docstring del modulo.
    cerrado_por = Column(String(255), nullable=False)
    cerrado_at = Column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    # Lo que estaba pendiente cuando se cerro. JSON y no columnas: ver el modulo.
    pendientes_al_cerrar = Column(JSON, nullable=True)
    # SHA-256 (64 hex) de la serializacion canonica del informe al cerrar.
    huella = Column(String(64), nullable=True)

    # `backref` y no `back_populates` porque `CompanyModel` no declara ninguna
    # relacion: las de empresa se crean todas desde el lado del hijo, que es lo
    # que hacen `tickets.py:181` y `bank_transaction.py:32`.
    company = relationship("CompanyModel", backref="cierres_periodo")
