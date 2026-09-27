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
        CHECK (extraction_status NOT IN ('AUTO_APROBADO', 'APROBADO') OR tax_amount <= total_amount)
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

-- Carga masiva idempotente: reintentar un lote no duplica gastos. Único solo
-- cuando existe el hash, porque los tickets manuales no lo tienen y varios
-- NULL en la misma columna no pueden violar unicidad.
CREATE UNIQUE INDEX IF NOT EXISTS ix_tickets_source_hash
    ON tickets (source_hash)
    WHERE source_hash IS NOT NULL;
