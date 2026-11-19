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
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) NOT NULL,
    nombre VARCHAR(120) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    perfil VARCHAR(50) NOT NULL DEFAULT 'digitalizador',
    grupo VARCHAR(50) NOT NULL DEFAULT 'operaciones',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS auditoria (
    id BIGSERIAL PRIMARY KEY,
    usuario_id UUID,
    rol VARCHAR(50),
    accion VARCHAR(100) NOT NULL,
    modulo VARCHAR(50),
    registro_afectado UUID,
    detalles TEXT,
    ip_inet VARCHAR(45),
    fecha TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_auditoria_fecha ON auditoria (fecha DESC);
CREATE INDEX IF NOT EXISTS ix_auditoria_usuario ON auditoria (usuario_id);

CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (lower(email));
CREATE INDEX IF NOT EXISTS ix_users_activos ON users (lower(email)) WHERE is_active;

-- Tabla de Tickets Digitalizados / Extraídos
CREATE TABLE IF NOT EXISTS tickets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    provider_name VARCHAR(150) NOT NULL,
    provider_tax_id VARCHAR(50),
    total_amount NUMERIC(12, 2) NOT NULL,
    tax_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    expense_date DATE NOT NULL,
    fecha_provisional BOOLEAN NOT NULL DEFAULT FALSE,
    category VARCHAR(100),
    raw_text TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    confidence NUMERIC(4, 3),
    confidence_source VARCHAR(20),
    extraction_status VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
    source_type VARCHAR(20),
    source_file VARCHAR(500),
    source_hash VARCHAR(64),
    validation_errors TEXT,
    reviewed_by VARCHAR(100),
    reviewed_at TIMESTAMP WITH TIME ZONE,
    review_notes TEXT,
    spot_check_status VARCHAR(20),
    spot_checked_at TIMESTAMP WITH TIME ZONE,
    spot_check_notes TEXT,
    spot_check_wrong_fields TEXT,
    spot_checked_by VARCHAR(255),
    subtotal NUMERIC(12, 2),
    items JSONB,

    CONSTRAINT ck_tickets_total_positive_when_settled
        CHECK (extraction_status NOT IN ('AUTO_APROBADO', 'APROBADO') OR total_amount > 0),
    CONSTRAINT ck_tickets_tax_non_negative_when_settled
        CHECK (extraction_status NOT IN ('AUTO_APROBADO', 'APROBADO') OR tax_amount >= 0),
    CONSTRAINT ck_tickets_tax_lte_total_when_settled
        CHECK (extraction_status NOT IN ('AUTO_APROBADO', 'APROBADO') OR tax_amount <= total_amount),
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

-- Tabla de Movimientos Bancarios
CREATE TABLE IF NOT EXISTS bank_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    transaction_date DATE NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    description TEXT NOT NULL,
    reference VARCHAR(100),
    is_reconciled BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Tabla de Conciliación
CREATE TABLE IF NOT EXISTS reconciliations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id UUID REFERENCES tickets(id) ON DELETE SET NULL,
    bank_transaction_id UUID REFERENCES bank_transactions(id) ON DELETE SET NULL,
    match_status VARCHAR(50) NOT NULL,
    matched_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    revisado_por VARCHAR(255),
    revisado_at TIMESTAMP WITH TIME ZONE,

    CONSTRAINT ck_reconciliations_revision
        CHECK (
            (revisado_por IS NULL AND revisado_at IS NULL)
            OR (revisado_por IS NOT NULL AND length(trim(revisado_por)) > 0
                AND revisado_at IS NOT NULL)
        )
);

CREATE INDEX IF NOT EXISTS ix_reconciliations_revisadas
    ON reconciliations (revisado_at DESC)
    WHERE revisado_por IS NOT NULL;

-- Tabla de Plantillas de Mapeo Contable
CREATE TABLE IF NOT EXISTS accounting_mappings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    software_name VARCHAR(100) NOT NULL,
    column_mappings JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Registro de archivos de carpeta escaneada
CREATE TABLE IF NOT EXISTS scan_files (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    relative_path VARCHAR(500) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    file_size BIGINT,
    file_mtime TIMESTAMP WITH TIME ZONE,
    detected_format VARCHAR(20),
    declared_extension VARCHAR(20),
    status VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
    attempts INTEGER NOT NULL DEFAULT 0,
    read_by VARCHAR(20),
    last_error TEXT,
    ticket_id UUID REFERENCES tickets(id) ON DELETE SET NULL,
    company_id UUID REFERENCES companies(id) ON DELETE SET NULL,
    first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_scanned_at TIMESTAMP WITH TIME ZONE,
    processed_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT ck_scan_files_status
        CHECK (status IN ('PENDIENTE', 'PROCESADO', 'DUPLICADO', 'ERROR', 'NO_SOPORTADO')),
    CONSTRAINT uq_scan_files_relative_path UNIQUE (relative_path)
);

CREATE TABLE IF NOT EXISTS scan_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    scan_file_id UUID NOT NULL REFERENCES scan_files(id) ON DELETE CASCADE,
    action VARCHAR(20) NOT NULL,
    detail TEXT,
    actor VARCHAR(255),
    confidence NUMERIC(4, 3),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_scan_events_action
        CHECK (action IN ('VISTO', 'CREADO', 'ACTUALIZADO', 'SIN_CAMBIOS',
                          'OMITIDO', 'ERROR', 'REINTENTO', 'BORRADO'))
);

CREATE INDEX IF NOT EXISTS idx_tickets_date ON tickets(expense_date);
CREATE INDEX IF NOT EXISTS idx_tickets_periodo_sin_fecha
    ON tickets(company_id, expense_date, fecha_provisional);
CREATE INDEX IF NOT EXISTS idx_bank_tx_date ON bank_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_bank_tx_amount ON bank_transactions(amount);

CREATE INDEX IF NOT EXISTS ix_tickets_review_queue
    ON tickets (extraction_status, company_id)
    WHERE extraction_status IN ('REQUIERE_REVISION', 'PENDIENTE');

CREATE UNIQUE INDEX IF NOT EXISTS ix_tickets_source_hash
    ON tickets (company_id, source_hash)
    WHERE source_hash IS NOT NULL;

-- El comprobante original de cada ticket
CREATE TABLE IF NOT EXISTS ticket_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    contenido BYTEA NOT NULL,
    content_type VARCHAR(120),
    nombre_archivo VARCHAR(500),
    tamano INTEGER NOT NULL,
    sha256 VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    reemplaza_a UUID REFERENCES ticket_documents(id) ON DELETE SET NULL,
    version INTEGER NOT NULL DEFAULT 1,
    actor VARCHAR(255),
    motivo TEXT,

    CONSTRAINT ck_ticket_documents_version
        CHECK (version >= 1),
    CONSTRAINT ck_ticket_documents_motivo_si_reemplaza
        CHECK (reemplaza_a IS NULL OR (motivo IS NOT NULL AND length(trim(motivo)) > 0)),
    CONSTRAINT ck_ticket_documents_actor_si_reemplaza
        CHECK (reemplaza_a IS NULL OR (actor IS NOT NULL AND length(trim(actor)) > 0))
);

CREATE INDEX IF NOT EXISTS ix_ticket_documents_ticket
    ON ticket_documents (ticket_id);

CREATE INDEX IF NOT EXISTS ix_ticket_documents_cadena
    ON ticket_documents (ticket_id, version);

CREATE UNIQUE INDEX IF NOT EXISTS ix_ticket_documents_sin_bifurcar
    ON ticket_documents (reemplaza_a)
    WHERE reemplaza_a IS NOT NULL;

-- Triggers de inmutabilidad para ticket_documents
CREATE OR REPLACE FUNCTION ticket_documents_no_actualizar() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'ticket_documents es append-only: un documento no se actualiza, se agrega '
        'una version nueva (reemplaza_a) y la anterior se conserva. Ticket %',
        OLD.ticket_id
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION ticket_documents_no_borrar() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM tickets WHERE id = OLD.ticket_id) THEN
        RAISE EXCEPTION
            'ticket_documents es append-only: no se borra un documento mientras su '
            'ticket exista. Para cambiar el papel se agrega una version nueva. Ticket %',
            OLD.ticket_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ticket_documents_no_actualizar ON ticket_documents;
CREATE TRIGGER trg_ticket_documents_no_actualizar
    BEFORE UPDATE ON ticket_documents
    FOR EACH ROW EXECUTE FUNCTION ticket_documents_no_actualizar();

DROP TRIGGER IF EXISTS trg_ticket_documents_no_borrar ON ticket_documents;
CREATE TRIGGER trg_ticket_documents_no_borrar
    BEFORE DELETE ON ticket_documents
    FOR EACH ROW EXECUTE FUNCTION ticket_documents_no_borrar();

CREATE INDEX IF NOT EXISTS ix_tickets_spot_check_queue
    ON tickets (company_id, created_at)
    WHERE spot_check_status = 'PENDIENTE';

CREATE INDEX IF NOT EXISTS ix_tickets_spot_check_report
    ON tickets (company_id, confidence_source)
    WHERE spot_check_status IS NOT NULL;

-- =====================================================================
-- Periodos Contables Cerrados (cierres_periodo y closed_accounting_periods)
-- =====================================================================
CREATE TABLE IF NOT EXISTS cierres_periodo (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    periodo VARCHAR(7) NOT NULL,
    cerrado_por VARCHAR(255) NOT NULL,
    cerrado_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    pendientes_al_cerrar JSONB,
    huella VARCHAR(64),

    CONSTRAINT ck_cierres_periodo_formato
        CHECK (periodo ~ '^\d{4}-(0[1-9]|1[0-2])$'),
    CONSTRAINT ck_cierres_periodo_sello_sano
        CHECK (cerrado_at <= now()),
    CONSTRAINT ck_cierres_periodo_huella_hex
        CHECK (huella IS NULL OR huella ~ '^[0-9a-f]{64}$')
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_cierres_periodo_unico
    ON cierres_periodo (company_id, periodo);

-- NUEVO: Tabla explícita para closed_accounting_periods (paridad con los nuevos modelos)
CREATE TABLE IF NOT EXISTS closed_accounting_periods (
    id SERIAL PRIMARY KEY,
    year INT NOT NULL,
    month INT NOT NULL,
    closed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    closed_by VARCHAR(100),
    CONSTRAINT uq_year_month_closed_period UNIQUE (year, month)
);

CREATE INDEX IF NOT EXISTS ix_closed_accounting_periods_year_month
    ON closed_accounting_periods (year, month);


-- Registro del escaneo de carpeta
CREATE INDEX IF NOT EXISTS ix_scan_files_status
    ON scan_files (status);

CREATE INDEX IF NOT EXISTS ix_scan_files_content_hash
    ON scan_files (content_hash);

CREATE INDEX IF NOT EXISTS ix_scan_files_company
    ON scan_files (company_id, status);

CREATE INDEX IF NOT EXISTS ix_scan_events_file_created
    ON scan_events (scan_file_id, created_at);


-- =====================================================================
-- Inventario: productos, compras y el kardex
-- =====================================================================
CREATE TABLE IF NOT EXISTS productos (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    codigo VARCHAR(64),
    nombre VARCHAR(200) NOT NULL,
    unidad_medida VARCHAR(20) NOT NULL DEFAULT 'PZA',
    precio_referencia NUMERIC(12, 2),
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_productos_codigo_no_vacio
        CHECK (codigo IS NULL OR length(trim(codigo)) > 0),
    CONSTRAINT ck_productos_nombre_no_vacio
        CHECK (length(trim(nombre)) > 0),
    CONSTRAINT ck_productos_precio_no_negativo
        CHECK (precio_referencia IS NULL OR precio_referencia >= 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_productos_codigo
    ON productos (company_id, codigo)
    WHERE codigo IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_productos_empresa
    ON productos (company_id, activo);

CREATE INDEX IF NOT EXISTS ix_productos_nombre
    ON productos (company_id, lower(nombre));

-- La orden de compra
CREATE TABLE IF NOT EXISTS compras (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    ticket_id UUID NOT NULL UNIQUE REFERENCES tickets(id) ON DELETE CASCADE,
    estado VARCHAR(20) NOT NULL DEFAULT 'PROCESAR',
    fecha DATE NOT NULL,
    proveedor_nombre VARCHAR(150),
    total NUMERIC(12, 2) NOT NULL,
    confirmada_por VARCHAR(255),
    confirmada_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_compras_estado
        CHECK (estado IN ('PROCESAR', 'EN_REVISION', 'PROCESADO', 'RECHAZADO')),
    CONSTRAINT ck_compras_confirmacion
        CHECK (
            (estado = 'PROCESADO'
             AND confirmada_por IS NOT NULL
             AND length(trim(confirmada_por)) > 0
             AND confirmada_at IS NOT NULL)
            OR
            (estado <> 'PROCESADO' AND confirmada_por IS NULL AND confirmada_at IS NULL)
        ),
    CONSTRAINT ck_compras_total_no_negativo
        CHECK (total >= 0)
);

CREATE INDEX IF NOT EXISTS ix_compras_empresa_estado
    ON compras (company_id, estado);

CREATE INDEX IF NOT EXISTS ix_compras_fecha
    ON compras (fecha);

-- Las líneas de compra
CREATE TABLE IF NOT EXISTS compra_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    compra_id UUID NOT NULL REFERENCES compras(id) ON DELETE CASCADE,
    producto_id UUID REFERENCES productos(id) ON DELETE SET NULL,
    descripcion VARCHAR(300) NOT NULL,
    cantidad NUMERIC(14, 3) NOT NULL,
    costo_unitario NUMERIC(12, 2),
    total NUMERIC(12, 2),
    orden INTEGER NOT NULL DEFAULT 0,
    producto_asignado_por VARCHAR(255),
    producto_asignado_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_compra_items_descripcion_no_vacia
        CHECK (length(trim(descripcion)) > 0),
    CONSTRAINT ck_compra_items_cantidad_positiva
        CHECK (cantidad > 0),
    CONSTRAINT ck_compra_items_costos_no_negativos
        CHECK ((costo_unitario IS NULL OR costo_unitario >= 0)
               AND (total IS NULL OR total >= 0)),
    CONSTRAINT ck_compra_items_asignacion
        CHECK (
            producto_id IS NULL
            OR (producto_asignado_por IS NOT NULL
                AND length(trim(producto_asignado_por)) > 0
                AND producto_asignado_at IS NOT NULL)
        )
);

CREATE INDEX IF NOT EXISTS ix_compra_items_compra
    ON compra_items (compra_id);

CREATE INDEX IF NOT EXISTS ix_compra_items_sin_producto
    ON compra_items (compra_id)
    WHERE producto_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_compra_items_producto
    ON compra_items (producto_id)
    WHERE producto_id IS NOT NULL;

-- El kardex
CREATE TABLE IF NOT EXISTS movimientos_inventario (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    producto_id UUID NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    tipo VARCHAR(20) NOT NULL,
    cantidad NUMERIC(14, 3) NOT NULL,
    referencia_tipo VARCHAR(20) NOT NULL,
    referencia_id UUID,
    actor VARCHAR(255) NOT NULL,
    descripcion_origen VARCHAR(300),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_movimientos_tipo
        CHECK (tipo IN ('ENTRADA', 'SALIDA', 'AJUSTE')),
    CONSTRAINT ck_movimientos_referencia_tipo
        CHECK (referencia_tipo IN ('COMPRA', 'VENTA', 'AJUSTE')),
    CONSTRAINT ck_movimientos_cantidad_positiva
        CHECK (cantidad > 0),
    CONSTRAINT ck_movimientos_actor
        CHECK (actor IS NOT NULL AND length(trim(actor)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_movimientos_producto
    ON movimientos_inventario (company_id, producto_id, created_at);

CREATE INDEX IF NOT EXISTS ix_movimientos_referencia
    ON movimientos_inventario (referencia_tipo, referencia_id);

-- Triggers de kardex
CREATE OR REPLACE FUNCTION movimientos_no_reescribir() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'movimientos_inventario es append-only: un movimiento no se edita, se '
            'agrega un AJUSTE. Producto %',
            OLD.producto_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF EXISTS (SELECT 1 FROM productos WHERE id = OLD.producto_id) THEN
        RAISE EXCEPTION
            'movimientos_inventario es append-only: no se borra un movimiento. '
            'Para corregir el stock se agrega un AJUSTE. Producto %',
            OLD.producto_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_movimientos_no_reescribir ON movimientos_inventario;
CREATE TRIGGER trg_movimientos_no_reescribir
    BEFORE UPDATE OR DELETE ON movimientos_inventario
    FOR EACH ROW EXECUTE FUNCTION movimientos_no_reescribir();

CREATE OR REPLACE FUNCTION movimientos_no_dejar_negativo() RETURNS trigger AS $$
DECLARE
    actual NUMERIC(14, 3);
BEGIN
    SELECT COALESCE(
        SUM(CASE WHEN m.tipo = 'SALIDA' THEN -m.cantidad ELSE m.cantidad END), 0
    ) INTO actual
    FROM movimientos_inventario m
    WHERE m.producto_id = NEW.producto_id;

    IF NEW.tipo = 'SALIDA' THEN
        actual := actual - NEW.cantidad;
    ELSE
        actual := actual + NEW.cantidad;
    END IF;

    IF actual < 0 THEN
        RAISE EXCEPTION
            'este movimiento (% de % piezas) dejaria el stock del producto % en '
            '% (negativo). Registra un AJUSTE si es una correccion de '
            'inventario, o corrige la cantidad.',
            NEW.tipo, NEW.cantidad, NEW.producto_id, to_char(actual, 'FM999999990.000')
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_movimientos_no_negativo ON movimientos_inventario;
CREATE TRIGGER trg_movimientos_no_negativo
    BEFORE INSERT ON movimientos_inventario
    FOR EACH ROW EXECUTE FUNCTION movimientos_no_dejar_negativo();

-- Productos automáticos y origen
ALTER TABLE productos ADD COLUMN IF NOT EXISTS origen VARCHAR(20) NOT NULL DEFAULT 'MANUAL';
ALTER TABLE productos ADD COLUMN IF NOT EXISTS verificado BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE productos DROP CONSTRAINT IF EXISTS ck_productos_origen;
ALTER TABLE productos ADD CONSTRAINT ck_productos_origen
    CHECK (origen IN ('MANUAL', 'OCR'));

ALTER TABLE productos DROP CONSTRAINT IF EXISTS ck_productos_verificado_ocr;
ALTER TABLE productos ADD CONSTRAINT ck_productos_verificado_ocr
    CHECK (verificado = true OR origen = 'OCR');

CREATE INDEX IF NOT EXISTS ix_productos_sin_verificar
    ON productos (company_id, created_at)
    WHERE verificado = false;

ALTER TABLE productos ADD COLUMN IF NOT EXISTS nombre_normalizado VARCHAR(200);
UPDATE productos SET nombre_normalizado = lower(nombre) WHERE nombre_normalizado IS NULL;

CREATE INDEX IF NOT EXISTS ix_productos_nombre_normalizado
    ON productos (company_id, nombre_normalizado);

-- Impuestos de la partida
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS ieps_amount NUMERIC(12, 2);
ALTER TABLE compra_items ADD COLUMN IF NOT EXISTS iva_linea NUMERIC(12, 2);
ALTER TABLE compra_items ADD COLUMN IF NOT EXISTS ieps_linea NUMERIC(12, 2);

COMMENT ON COLUMN compra_items.iva_linea IS 'IVA de ESTA linea, en pesos.';
COMMENT ON COLUMN compra_items.ieps_linea IS 'IEPS de ESTA linea, en pesos.';
COMMENT ON COLUMN tickets.ieps_amount IS 'IEPS total del comprobante, en pesos.';

CREATE INDEX IF NOT EXISTS ix_compra_items_impuestos
    ON compra_items(compra_id)
    WHERE ieps_linea IS NOT NULL OR iva_linea IS NOT NULL;

-- Tipos de gasto y autorización
CREATE TABLE IF NOT EXISTS tipos_gasto (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    clave VARCHAR(50) NOT NULL,
    nombre VARCHAR(120) NOT NULL,
    variable BOOLEAN NOT NULL DEFAULT TRUE,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_tipos_gasto_clave_no_vacia CHECK (length(trim(clave)) > 0),
    CONSTRAINT ck_tipos_gasto_nombre_no_vacio CHECK (length(trim(nombre)) > 0),
    CONSTRAINT ck_tipos_gasto_clave_mayuscula CHECK (clave = upper(clave))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_tipos_gasto_empresa_clave
    ON tipos_gasto (company_id, clave);

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS tipo_gasto_id UUID REFERENCES tipos_gasto(id) ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS ix_tickets_tipo_gasto ON tickets (tipo_gasto_id);

ALTER TABLE productos ADD COLUMN IF NOT EXISTS tipo_gasto_id UUID REFERENCES tipos_gasto(id) ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS ix_productos_tipo_gasto ON productos (tipo_gasto_id);

CREATE INDEX IF NOT EXISTS ix_tipos_gasto_empresa_activo
    ON tipos_gasto (company_id, activo);

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS estado_autorizacion VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE';
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS autorizado_por VARCHAR(255) NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS autorizado_at TIMESTAMP WITH TIME ZONE NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS motivo_rechazo TEXT NULL;

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_estado_autorizacion;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_estado_autorizacion
    CHECK (estado_autorizacion IN ('PENDIENTE', 'APROBADA', 'RECHAZADA'));

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_autorizacion_firmada;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_autorizacion_firmada
    CHECK (
        estado_autorizacion <> 'APROBADA'
        OR (autorizado_por IS NOT NULL AND autorizado_at IS NOT NULL)
    );

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_rechazo_con_motivo;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_rechazo_con_motivo
    CHECK (estado_autorizacion <> 'RECHAZADA' OR motivo_rechazo IS NOT NULL);

-- Extracción multi-país y controles
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS pais VARCHAR(3);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS identificacion_fiscal VARCHAR(50);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS folio VARCHAR(100);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS metodo_pago VARCHAR(50);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS link_facturacion TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS hash_duplicado VARCHAR(64);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS estado_conciliacion VARCHAR(30) NOT NULL DEFAULT 'PENDIENTE';

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS periodo_cerrado_override BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS periodo_cerrado_motivo TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS periodo_cerrado_autorizado_por VARCHAR(255);
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS periodo_cerrado_autorizado_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_pais;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_pais
    CHECK (pais IS NULL OR pais IN ('MEX', 'CHL'));

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_estado_conciliacion;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_estado_conciliacion
    CHECK (estado_conciliacion IN ('PENDIENTE', 'CONCILIADO', 'CON_DISCREPANCIA'));

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

-- schema_migrations
CREATE TABLE IF NOT EXISTS schema_migrations (
    nombre VARCHAR(255) PRIMARY KEY,
    aplicada_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO schema_migrations (nombre) VALUES
    ('0002_confidence_gate.sql'),
    ('0003_spot_check.sql'),
    ('0004_auth.sql'),
    ('0005_source_hash_por_empresa.sql'),
    ('0006_ticket_documents.sql'),
    ('0007_cierre_periodo.sql'),
    ('0008_scan_ledger.sql'),
    ('0009_documento_inmutable.sql'),
    ('0010_inventario.sql'),
    ('0011_productos_origen.sql'),
    ('0012_el_impuesto_es_de_la_partida.sql'),
    ('0013_compras_rechazables.sql'),
    ('0014_import_bancario_idempotente.sql'),
    ('0015_fecha_provisional.sql'),
    ('0016_perfil_usuario.sql'),
    ('0017_auditoria.sql'),
    ('0018_tipos_gasto.sql'),
    ('0019_extraccion_multipaís_y_controles.sql')
ON CONFLICT (nombre) DO NOTHING;

CREATE TABLE ticket_persistence (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL,
    user_id INT NOT NULL,
    invoice_text TEXT,
    invoice_raw_text TEXT,
    hash_sha256 VARCHAR(64),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_ticket UNIQUE (hash_sha256)
);
