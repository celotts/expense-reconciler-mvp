-- =====================================================================
-- 0019 - Extraccion multi-pais, controles fiscales y conciliacion
-- =====================================================================
--
-- QUE AGREGA (aditivo, sin romper datos ni flujos existentes)
--
--   tickets.pais                     MEX / CHL (normalizado)
--   tickets.identificacion_fiscal    RFC (MX) o RUT (CL), espejo canonico
--   tickets.folio                    folio/factura del comprobante
--   tickets.metodo_pago              metodo de pago declarado
--   tickets.link_facturacion         liga o referencia para facturar
--   tickets.hash_duplicado           huella de negocio SHA-256 (servidor):
--                                    identificacion_fiscal + fecha + folio + monto
--   tickets.estado_conciliacion      estado sincronizado con conciliaciones
--   tickets.periodo_cerrado_override* override administrativo trazable cuando
--                                    la fecha cae en un periodo cerrado
--
-- NOTAS DE INTEGRACION
--
-- - `hash_duplicado` complementa `tickets.source_hash` (huella de ARCHIVO) y
--   `ticket_documents.sha256` (huella de bytes/version). No los reemplaza.
-- - La huella de negocio debe recalcularse/confirmarse en servidor aunque la IA
--   la devuelva: la salida del modelo no es autoridad.
-- - `estado_conciliacion` se sincroniza desde `reconciliations.match_status`:
--     PERFECT / MANUAL -> CONCILIADO
--     DISCREPANCY      -> CON_DISCREPANCIA
--     sin conciliacion -> PENDIENTE
-- - `provider_tax_id` se conserva por compatibilidad; `identificacion_fiscal` es
--   el campo canonico multi-pais y se rellena desde el existente cuando aplica.
--
-- Esta migracion es idempotente (`IF NOT EXISTS` / `IF EXISTS` / `ON CONFLICT`)
-- para poder reejecutarse con seguridad.

-- ---------------------------------------------------------------------
-- 1. Campos multi-pais y fiscales en tickets
-- ---------------------------------------------------------------------
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS pais VARCHAR(3);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS identificacion_fiscal VARCHAR(50);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS folio VARCHAR(100);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS metodo_pago VARCHAR(50);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS link_facturacion TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS hash_duplicado VARCHAR(64);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS estado_conciliacion VARCHAR(30) NOT NULL DEFAULT 'PENDIENTE';

COMMENT ON COLUMN tickets.pais IS
    'Pais del comprobante, normalizado (MEX/CHL). NULL en registros previos a la extraccion multi-pais.';
COMMENT ON COLUMN tickets.identificacion_fiscal IS
    'Identificacion fiscal del emisor: RFC (Mexico) o RUT (Chile). Canonico para validacion/anti-duplicado multi-pais.';
COMMENT ON COLUMN tickets.folio IS
    'Folio o numero de factura/comprobante declarado por el documento.';
COMMENT ON COLUMN tickets.metodo_pago IS
    'Metodo de pago declarado (efectivo, tarjeta, transferencia, etc.).';
COMMENT ON COLUMN tickets.link_facturacion IS
    'Liga o referencia para facturacion proporcionada por el comprobante.';
COMMENT ON COLUMN tickets.hash_duplicado IS
    'Huella SHA-256 de negocio (identificacion_fiscal + fecha + folio + monto), calculada/confirmada en servidor. Complementa source_hash del archivo.';
COMMENT ON COLUMN tickets.estado_conciliacion IS
    'Estado de conciliacion sincronizado: PENDIENTE, CONCILIADO o CON_DISCREPANCIA.';

-- ---------------------------------------------------------------------
-- 2. Override administrativo trazable de periodo cerrado
-- ---------------------------------------------------------------------
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS periodo_cerrado_override BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS periodo_cerrado_motivo TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS periodo_cerrado_autorizado_por VARCHAR(255);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS periodo_cerrado_autorizado_at TIMESTAMP WITH TIME ZONE;

COMMENT ON COLUMN tickets.periodo_cerrado_override IS
    'TRUE cuando un administrador autorizo registrar/corregir el ticket pese a caer en un periodo cerrado. Trazable.';
COMMENT ON COLUMN tickets.periodo_cerrado_motivo IS
    'Motivo del override de periodo cerrado. Obligatorio cuando periodo_cerrado_override es TRUE.';
COMMENT ON COLUMN tickets.periodo_cerrado_autorizado_por IS
    'Identidad (email/usuario del token) que autorizo el override. Texto para sobrevivir bajas de cuenta.';
COMMENT ON COLUMN tickets.periodo_cerrado_autorizado_at IS
    'Momento en que se autorizo el override de periodo cerrado.';

-- ---------------------------------------------------------------------
-- 3. Constraints e indices
-- ---------------------------------------------------------------------
ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_pais;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_pais
    CHECK (pais IS NULL OR pais IN ('MEX', 'CHL'));

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_estado_conciliacion;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_estado_conciliacion
    CHECK (estado_conciliacion IN ('PENDIENTE', 'CONCILIADO', 'CON_DISCREPANCIA'));

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_periodo_override_trazable;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_periodo_override_trazable
    CHECK (
        (
            periodo_cerrado_override = FALSE
            AND periodo_cerrado_motivo IS NULL
            AND periodo_cerrado_autorizado_por IS NULL
            AND periodo_cerrado_autorizado_at IS NULL
        )
        OR (
            periodo_cerrado_override = TRUE
            AND periodo_cerrado_motivo IS NOT NULL
            AND length(trim(periodo_cerrado_motivo)) > 0
            AND periodo_cerrado_autorizado_por IS NOT NULL
            AND length(trim(periodo_cerrado_autorizado_por)) > 0
            AND periodo_cerrado_autorizado_at IS NOT NULL
        )
    );

-- Un mismo hash de negocio no debe repetirse dentro de la empresa.
-- NULL se permite (comprobantes sin folio/datos suficientes para huella estable).
CREATE UNIQUE INDEX IF NOT EXISTS ux_tickets_empresa_hash_duplicado
    ON tickets (company_id, hash_duplicado)
    WHERE hash_duplicado IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_tickets_estado_conciliacion
    ON tickets (estado_conciliacion);

CREATE INDEX IF NOT EXISTS ix_tickets_folio
    ON tickets (folio)
    WHERE folio IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_tickets_identificacion_fiscal
    ON tickets (identificacion_fiscal)
    WHERE identificacion_fiscal IS NOT NULL;

-- ---------------------------------------------------------------------
-- 4. Backfill compatible (solo donde hay datos seguros)
-- ---------------------------------------------------------------------
-- Canonizar identificacion fiscal desde provider_tax_id existente.
UPDATE tickets
SET identificacion_fiscal = provider_tax_id
WHERE identificacion_fiscal IS NULL
  AND provider_tax_id IS NOT NULL
  AND length(trim(provider_tax_id)) > 0;

-- Sincronizar estado de conciliacion desde reconciliations actuales.
-- Orden: primero discrepancias, luego conciliados; lo no conciliado queda PENDIENTE.
UPDATE tickets t
SET estado_conciliacion = 'CON_DISCREPANCIA'
WHERE EXISTS (
    SELECT 1
    FROM reconciliations r
    WHERE r.ticket_id = t.id
      AND r.match_status = 'DISCREPANCY'
);

UPDATE tickets t
SET estado_conciliacion = 'CONCILIADO'
WHERE EXISTS (
    SELECT 1
    FROM reconciliations r
    WHERE r.ticket_id = t.id
      AND r.match_status IN ('PERFECT', 'MANUAL')
)
  AND NOT EXISTS (
    SELECT 1
    FROM reconciliations r
    WHERE r.ticket_id = t.id
      AND r.match_status = 'DISCREPANCY'
);

-- ---------------------------------------------------------------------
-- 5. Registro de migracion
-- ---------------------------------------------------------------------
INSERT INTO schema_migrations (nombre)
VALUES ('0019_extraccion_multipaís_y_controles.sql')
ON CONFLICT (nombre) DO NOTHING;
