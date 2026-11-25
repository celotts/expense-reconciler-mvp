-- 0021_caja_chica_y_huella_fiscal.sql
-- Agrega cajas chicas, movimientos (con soporte de reposición desde banco)
-- e índice compuesto de huella fiscal por empresa en tickets.

CREATE TABLE IF NOT EXISTS cajas_chicas (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    responsable_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    nombre VARCHAR(120) NOT NULL,
    monto_inicial NUMERIC(12, 2) NOT NULL,
    saldo_actual NUMERIC(12, 2) NOT NULL,
    umbral_alerta NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    estado VARCHAR(20) NOT NULL DEFAULT 'abierta',
    opened_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP WITH TIME ZONE,

    CONSTRAINT ck_cajas_chicas_nombre_no_vacio
        CHECK (length(trim(nombre)) > 0),
    CONSTRAINT ck_cajas_chicas_montos_no_negativos
        CHECK (monto_inicial >= 0 AND umbral_alerta >= 0),
    CONSTRAINT ck_cajas_chicas_estado
        CHECK (estado IN ('abierta', 'en_revision', 'cerrada')),
    CONSTRAINT ck_cajas_chicas_cierre_coherente
        CHECK (estado <> 'cerrada' OR closed_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_cajas_chicas_company_estado
    ON cajas_chicas (company_id, estado);

CREATE INDEX IF NOT EXISTS ix_cajas_chicas_responsable
    ON cajas_chicas (company_id, responsable_id);

ALTER TABLE tickets
    ADD COLUMN IF NOT EXISTS caja_chica_id UUID REFERENCES cajas_chicas(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_tickets_caja_chica
    ON tickets (caja_chica_id)
    WHERE caja_chica_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_tickets_company_fiscal_huella
    ON tickets (company_id, provider_tax_id, folio, expense_date, total_amount);

CREATE TABLE IF NOT EXISTS movimientos_caja (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    caja_chica_id UUID NOT NULL REFERENCES cajas_chicas(id) ON DELETE CASCADE,
    ticket_id UUID REFERENCES tickets(id) ON DELETE SET NULL,
    tipo VARCHAR(20) NOT NULL,
    origen_fondo VARCHAR(20),
    banco_nombre VARCHAR(80),
    referencia_bancaria VARCHAR(100),
    monto NUMERIC(12, 2) NOT NULL,
    notas TEXT,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_movimientos_caja_tipo
        CHECK (tipo IN ('apertura', 'reposicion', 'gasto_ticket', 'ajuste', 'cierre')),
    CONSTRAINT ck_movimientos_caja_origen
        CHECK (origen_fondo IS NULL OR origen_fondo IN ('efectivo', 'banco')),
    CONSTRAINT ck_movimientos_caja_banco_cuando_banco
        CHECK (
            origen_fondo IS DISTINCT FROM 'banco'
            OR (banco_nombre IS NOT NULL AND length(trim(banco_nombre)) > 0)
        ),
    CONSTRAINT ck_movimientos_caja_monto_no_negativo
        CHECK (monto >= 0)
);

CREATE INDEX IF NOT EXISTS ix_movimientos_caja_company_caja
    ON movimientos_caja (company_id, caja_chica_id, created_at);

CREATE INDEX IF NOT EXISTS ix_movimientos_caja_banco_ref
    ON movimientos_caja (company_id, origen_fondo, banco_nombre, referencia_bancaria)
    WHERE origen_fondo = 'banco';

INSERT INTO schema_migrations (nombre)
VALUES ('0021_caja_chica_y_huella_fiscal.sql')
ON CONFLICT (nombre) DO NOTHING;
