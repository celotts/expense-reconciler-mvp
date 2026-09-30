-- Creación de extensiones necesarias
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Tabla de Empresas o Perfiles de Usuario
CREATE TABLE IF NOT EXISTS companies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(150) NOT NULL,
    tax_id VARCHAR(50) UNIQUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Tabla de Cuentas con acceso
--
-- Sin autorregistro: las crea scripts/crear_usuario.py, que pide la contrasena
-- por teclado. Esta tabla es el mismo DDL que emite app/models/user.py, y la
-- razon de que esten los dos es la misma que la de tickets: una base creada
-- desde cero y una base migrada tienen que terminar con las mismas reglas.
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) NOT NULL,
    nombre VARCHAR(120) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP WITH TIME ZONE
);

-- Sobre `lower(email)`, no sobre `email`: el login siempre normaliza a
-- minúsculas antes de buscar, así que un índice sobre la columna tal cual
-- no evita "Ana@empresa.mx" y "ana@empresa.mx". Ver db/migrations/0004_auth.sql.
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (lower(email));
CREATE INDEX IF NOT EXISTS ix_users_activos ON users (lower(email)) WHERE is_active;

-- Tabla de Tickets Digitalizados / Extraídos
--
-- Este archivo SOLO corre la primera vez que Postgres arranca con el volumen
-- vacio. Para una base ya existente, el gate de confianza y la cola de
-- revision se aplican con db/migrations/0002_confidence_gate.sql.
--
-- Las constraints y los indices de abajo son el mismo DDL que emite el modelo
-- SQLAlchemy. Si se cambian aqui, hay que cambiarlos tambien alla, o una base
-- nueva y una migrada terminan con reglas distintas y el mismo codigo se
-- comporta de dos maneras segun la maquina.
CREATE TABLE IF NOT EXISTS tickets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    provider_name VARCHAR(150) NOT NULL,
    provider_tax_id VARCHAR(50),
    total_amount NUMERIC(12, 2) NOT NULL,
    tax_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    expense_date DATE NOT NULL,
    category VARCHAR(100),
    raw_text TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    -- Trazabilidad de la extracción: la confianza que la IA ya calculaba y se
    -- descartaba. Ahora se persiste y gobierna el flujo, que es lo que permite
    -- medir exactitud real en vez de suponerla.
    confidence NUMERIC(4, 3),
    confidence_source VARCHAR(20),
    extraction_status VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
    source_type VARCHAR(20),
    source_file VARCHAR(500),
    source_hash VARCHAR(64),

    -- Checks deterministas que fallaron. Guardarlos permite ver dónde se
    -- concentra el error (OCR, visión, reglas) en vez de suponerlo.
    validation_errors TEXT,

    -- Revisión humana
    reviewed_by VARCHAR(100),
    reviewed_at TIMESTAMP WITH TIME ZONE,
    review_notes TEXT,

    -- Muestreo de exactitud. NULL = fuera de la muestra, que es el 95%. Solo
    -- se marca lo que el sistema aprobó solo y nadie tocó; ver
    -- db/migrations/0003_spot_check.sql.
    spot_check_status VARCHAR(20),
    spot_checked_at TIMESTAMP WITH TIME ZONE,
    spot_check_notes TEXT,
    spot_check_wrong_fields TEXT,
    -- Quien registró el veredicto, desde el token. Texto y no llave foránea a
    -- propósito: el veredicto tiene que sobrevivir a la baja de la cuenta.
    spot_checked_by VARCHAR(255),

    -- El subtotal se extraía y se le pasaba al gate para validar
    -- `subtotal + IVA == total`, y no se guardaba: la decisión no era
    -- auditable. NULL y no cero, porque si el comprobante no trae subtotal no
    -- se sabe el subtotal, y un 0 haría que la cuenta pareciera cuadrar.
    subtotal NUMERIC(12, 2),

    -- La última línea, donde el gate ya no puede opinar.
    --
    -- Condicionales a propósito: solo aplican cuando el ticket está cerrado
    -- (AUTO_APROBADO o APROBADO) y por lo tanto puede entrar a conciliación.
    -- Un documento ilegible tiene que poder guardarse como PENDIENTE y
    -- descartarse como RECHAZADO; con una constraint incondicional, una foto de
    -- un papel quemado daba IntegrityError -> 500 -> documento perdido en
    -- silencio, y la cola no se podía vaciar nunca.
    CONSTRAINT ck_tickets_total_positive_when_settled
        CHECK (extraction_status NOT IN ('AUTO_APROBADO', 'APROBADO') OR total_amount > 0),
    CONSTRAINT ck_tickets_tax_non_negative_when_settled
        CHECK (extraction_status NOT IN ('AUTO_APROBADO', 'APROBADO') OR tax_amount >= 0),
    CONSTRAINT ck_tickets_tax_lte_total_when_settled
        CHECK (extraction_status NOT IN ('AUTO_APROBADO', 'APROBADO') OR tax_amount <= total_amount),

    -- Constraints del muestreo. Se replican de la migración 0003 a proposito:
    -- un archivo de migración que no se lee al crear la base nueva produce
    -- bases distintas según cuándo se erigió, que es la forma más lenta de
    -- tener dos verdades.
    CONSTRAINT ck_tickets_spot_check_values
        CHECK (spot_check_status IS NULL
               OR spot_check_status IN ('PENDIENTE', 'CORRECTO', 'INCORRECTO')),
    CONSTRAINT ck_tickets_spot_check_verdict_has_date
        CHECK (spot_check_status IS NULL
               OR spot_check_status = 'PENDIENTE'
               OR spot_checked_at IS NOT NULL),
    CONSTRAINT ck_tickets_spot_check_only_auto
        CHECK (spot_check_status IS NULL OR extraction_status = 'AUTO_APROBADO')
);

-- Tabla de Movimientos Bancarios (Importados vía CSV/PDF)
CREATE TABLE IF NOT EXISTS bank_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    transaction_date DATE NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    description TEXT NOT NULL,
    reference VARCHAR(100),
    is_reconciled BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Tabla de Conciliación (Relación Ticket <-> Banco)
CREATE TABLE IF NOT EXISTS reconciliations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id UUID REFERENCES tickets(id) ON DELETE SET NULL,
    bank_transaction_id UUID REFERENCES bank_transactions(id) ON DELETE SET NULL,
    match_status VARCHAR(50) NOT NULL,
    matched_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Tabla de Plantillas de Mapeo Contable
CREATE TABLE IF NOT EXISTS accounting_mappings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    software_name VARCHAR(100) NOT NULL,
    column_mappings JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Los CREATE INDEX de abajo (y los de tickets) son IF NOT EXISTS a proposito:
-- el script se puede volver a correr sin reventar. Postgres no lo perdona si
-- no.
CREATE INDEX IF NOT EXISTS idx_tickets_date ON tickets(expense_date);
CREATE INDEX IF NOT EXISTS idx_bank_tx_date ON bank_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_bank_tx_amount ON bank_transactions(amount);

-- Cola de revisión: índice parcial, solo contiene lo que está en la cola. No
-- crece con el histórico y la consulta de la cola no ordena sobre miles de
-- tickets ya cerrados.
CREATE INDEX IF NOT EXISTS ix_tickets_review_queue
    ON tickets (extraction_status, company_id)
    WHERE extraction_status IN ('REQUIERE_REVISION', 'PENDIENTE');

-- Carga masiva idempotente: reintentar un lote no duplica gastos.
--
-- El índice va sobre (company_id, source_hash) y no solo sobre source_hash.
-- El hash es el SHA-256 del archivo y no lleva empresa dentro, así que el mismo
-- comprobante en dos empresas da el mismo hash. Con unicidad solo por hash,
-- una de las dos empresas se queda sin poder registrar el gasto, y el índice
-- además convertía en error lo que es un caso legítimo. Ver
-- db/migrations/0005_source_hash_por_empresa.sql.
--
-- Único solo cuando existe, porque los tickets manuales no tienen hash y varios
-- NULL en la misma columna no pueden violar unicidad.
CREATE UNIQUE INDEX IF NOT EXISTS ix_tickets_source_hash
    ON tickets (company_id, source_hash)
    WHERE source_hash IS NOT NULL;

-- El comprobante original de cada ticket (0006).
--
-- Antes esto no existía y los bytes se perdían al terminar la lectura. De la
-- subida solo quedaban el nombre, el hash y el texto que el sistema había
-- extraído, que es lo que hace imposible el muestreo de exactitud: la pregunta
-- "¿la extracción coincidió con el papel?" necesita el papel.
--
-- Va aquí replicado por la misma razón que las constraints de tickets: una
-- base creada desde cero y una migrada tienen que terminar iguales.
--
-- El CASCADE es lo que hace que borrar un ticket borre su comprobante. Con el
-- archivo en disco, ese borrado sería una tarea aparte que nadie recuerda.
CREATE TABLE IF NOT EXISTS ticket_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- UNIQUE y llave foránea a la vez: un ticket tiene un documento o ninguno,
    -- nunca dos, y nunca un documento sin ticket.
    ticket_id UUID NOT NULL UNIQUE REFERENCES tickets(id) ON DELETE CASCADE,
    contenido BYTEA NOT NULL,
    -- Lo declara el cliente y NO se usa tal cual para servirlo: el endpoint
    -- responde desde una lista cerrada de tipos que el navegador no puede
    -- ejecutar. Ver app/models/ticket_document.py.
    content_type VARCHAR(120),
    nombre_archivo VARCHAR(500),
    tamano INTEGER NOT NULL,
    -- El mismo hash que tickets.source_hash, para verificar los bytes guardados
    -- sin volver a pedir el archivo.
    sha256 VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_ticket_documents_ticket
    ON ticket_documents (ticket_id);

-- Muestreo de exactitud (0003). Dos índices parciales, y la diferencia importa:
--
-- ix_tickets_spot_check_queue es la cola de trabajo. Se vacia a medida que se
-- revisa, y su índice no crece.
--
-- ix_tickets_spot_check_report es la evidencia. Al revés que el anterior: solo
-- crece, porque los tickets revisados se quedan para siempre, son lo que
-- sostiene el número de exactitud. Por eso va sobre `confidence_source` y no
-- sobre el estado: el reporte agrupa por origen de lectura y sin este índice
-- contaría unas cuantas filas recorriendo todo el histórico.
CREATE INDEX IF NOT EXISTS ix_tickets_spot_check_queue
    ON tickets (company_id, created_at)
    WHERE spot_check_status = 'PENDIENTE';

CREATE INDEX IF NOT EXISTS ix_tickets_spot_check_report
    ON tickets (company_id, confidence_source)
    WHERE spot_check_status IS NOT NULL;

-- =====================================================================
-- El periodo que alguien firmo como cerrado (0007)
-- =====================================================================
--
-- Esto es lo que le faltaba a R3 del contrato (docs/contrato-producto.md:176).
-- La regla dice que el informe no puede declarar cerrado un periodo con
-- pendientes, "salvo que el contador lo marque explicitamente (y entonces el
-- informe registra que lo fue)". La excepcion era la parte que no existia: el
-- informe podia negarse, pero no habia donde registrar que alguien lo cerro a
-- sabiendas. Sin esta fila, R3 se cumple a medias.
--
-- Una fila por (empresa, periodo). NO es un historico de reaperturas: se
-- sobrescribe. Congelar el informe y guardar el log de cierres es D5, y D5 es
-- Fase 4.
--
-- `cerrado_por` es texto y no llave foranea a proposito, por el mismo motivo que
-- `tickets.spot_checked_by`: el cierre tiene que sobrevivir a la baja de la cuenta.
-- Una firma que se borra sola no es una firma.
CREATE TABLE IF NOT EXISTS cierres_periodo (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- 'YYYY-MM', y no DATE. Un DATE (el dia 1) obligaria al informe a decidir si
    -- significa "enero" o "enero a partir del dia 1", y en un cierre fiscal son
    -- dos cosas distintas.
    periodo VARCHAR(7) NOT NULL,

    -- Quien lo cerro, desde el token. Texto, no FK.
    cerrado_por VARCHAR(255) NOT NULL,
    cerrado_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Que estaba pendiente cuando se cerro. JSONB y no tres columnas porque se
    -- muestra en el informe y no se agrega. NULL = no se sabe, y el informe lo
    -- dice en vez de rellenarlo con cero.
    pendientes_al_cerrar JSONB,

    -- SHA-256 en hex de la serializacion canonica del informe al cerrarlo. Es lo
    -- que detecta que los datos se movieron despues de la firma. NULL = se cerro
    -- sin huella, no "la huella esta vacia".
    huella VARCHAR(64),

    -- La regex del contrato. Aqui si se puede usar `~` porque esto es Postgres;
    -- en el modelo va la version portable, que es lo que corre en los tests sobre
    -- SQLite. Ver app/models/cierre_periodo.py.
    CONSTRAINT ck_cierres_periodo_formato
        CHECK (periodo ~ '^\d{4}-(0[1-9]|1[0-2])$'),

    -- Un cierre con fecha futura es una intencion, no un hecho.
    CONSTRAINT ck_cierres_periodo_sello_sano
        CHECK (cerrado_at <= now()),

    -- 64 hex minusculas, que es lo que produce hashlib.sha256().hexdigest().
    -- Sin esto, una huella mal formada nunca coincidiria al comparar, que es un
    -- fallo silencioso: siempre "cerrado con otra informacion".
    CONSTRAINT ck_cierres_periodo_huella_hex
        CHECK (huella IS NULL OR huella ~ '^[0-9a-f]{64}$')
);

-- UNIQUE (company_id, periodo) y no PK compuesta, porque el grano del informe es
-- (empresa, periodo): dos cierres del mismo periodo serian ambiguos. Tambien es el
-- acceso principal, que siempre pregunta por un par concreto.
CREATE UNIQUE INDEX IF NOT EXISTS ix_cierres_periodo_unico
    ON cierres_periodo (company_id, periodo);
