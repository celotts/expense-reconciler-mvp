-- =====================================================================
-- 0019 - Lotes de digitalizacion: el grupo de tickets que se escanearon juntos
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- La fecha del papel dice EN QUE MES se gasto. No dice con que otros papeles
-- se digitalizo, ni si ese grupo ya se reviso, ni si el contador lo dio por
-- cerrado. Sin un lote, "los tickets de octubre" son una consulta por fecha
-- que cualquiera puede reescribir, y "cerrar octubre" no tiene donde
-- guardarse: `cierres_periodo` registra que un periodo se firmo, pero no que
-- papeles entraron en el.
--
-- QUE RESUELVE
--
--   lotes                       el grupo: folio, periodo, estado, contadores
--   companies.lote_config       la configuracion por empresa (JSON, ver abajo)
--   tickets.lote_id             cada gasto dice en que lote va
--   scan_files.lote_id          cada archivo dice en que lote se escaneo
--
-- POR QUE `lote_config` ES JSON EN `companies` Y NO UNA TABLA APARTE
--
-- Son cuatro escalares (`tipo_periodo`, `exige_total_esperado`,
-- `permite_reapertura`, `prefijo_folio`) que se leen siempre con la empresa y
-- nunca se buscan por su valor. No hay consulta "que empresas exigen total"
-- que justifique una tabla ni un indice, y no tiene historia: cambiar el
-- prefijo no necesita saber cual era antes. Una tabla mas es un modelo mas,
-- una migracion mas y un join mas, para un dato que cabe en la fila que ya se
-- lee. NULL = sin configurar: se aplican los defaults de
-- `app/models/lote.py:CONFIG_LOTE_DEFECTO`.
--
-- POR QUE `lote_id` ES NULLABLE EN LAS DOS TABLAS
--
-- Los registros viejos se digitalizaron antes de que existieran los lotes y
-- quedan con NULL: el lote inicial se asigna en otro trabajo. Y un archivo
-- puede inventariarse sin empresa ni lote (ver `scan_files.company_id`). Un
-- NOT NULL aqui obligaria a inventar un lote para el historico, que es justo
-- el dato que no se tiene.
--
-- POR QUE `ON DELETE SET NULL` Y NO `CASCADE`
--
-- Borrar un lote no puede borrar el gasto ni el registro de que el archivo se
-- leyo: el ticket es el dato contable y el registro del archivo es lo que
-- evita que el proximo escaneo vuelva a gastar OCR en el. El ticket se queda,
-- sin lote, y alguien lo reasigna.


-- ---------------------------------------------------------------------
-- 1. El lote
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lotes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- LOT-2026-10-001: prefijo + ejercicio + mes + secuencial por empresa y
    -- mes. Lo genera el API, no el cliente.
    folio VARCHAR(30) NOT NULL,
    nombre VARCHAR(150) NOT NULL,
    ejercicio INTEGER NOT NULL,
    mes INTEGER NOT NULL,
    tipo_periodo VARCHAR(20) NOT NULL DEFAULT 'mensual',
    fecha_inicio DATE NOT NULL,
    fecha_fin DATE NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'ABIERTO',

    -- Cuantos tickets se esperaban, si alguien lo sabe. NULL y no cero: un
    -- lote sin expectativa no es un lote al que le falten todos.
    total_esperado INTEGER NULL,
    -- Contadores CACHE que mantiene `scan_service` al asignar. La cuenta en
    -- vivo esta en `GET /lotes/{id}`.
    total_escaneado INTEGER NOT NULL DEFAULT 0,
    importe_total NUMERIC(12, 2) NOT NULL DEFAULT 0.00,

    -- El lote del que este es complemento (o alcance). NULL = lote normal.
    lote_padre_id UUID NULL REFERENCES lotes(id) ON DELETE SET NULL,
    -- El motivo de la reapertura, o de que exista el complemento.
    motivo TEXT NULL,

    -- Quien lo creo, desde el token. Texto y no FK, como
    -- `tickets.spot_checked_by`: la firma tiene que sobrevivir a la baja de
    -- la cuenta.
    creado_por VARCHAR(255) NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    cerrado_at TIMESTAMP WITH TIME ZONE NULL
);

-- El folio es unico POR EMPRESA, no global: dos empresas pueden tener su
-- propio LOT-2026-10-001.
CREATE UNIQUE INDEX IF NOT EXISTS ux_lotes_empresa_folio
    ON lotes (company_id, folio);

-- La lista de la pantalla: los lotes de la empresa, por periodo y estado.
CREATE INDEX IF NOT EXISTS ix_lotes_empresa_periodo
    ON lotes (company_id, ejercicio, mes, estado);

CREATE INDEX IF NOT EXISTS ix_lotes_padre ON lotes (lote_padre_id);

-- El mes es 1-12, el periodo no termina antes de empezar, y el estado y el
-- tipo de periodo son los del contrato. Las constraints se reponen con DROP +
-- ADD como en la 0018, para que la migracion se pueda volver a correr.
ALTER TABLE lotes DROP CONSTRAINT IF EXISTS ck_lotes_mes;
ALTER TABLE lotes ADD CONSTRAINT ck_lotes_mes CHECK (mes BETWEEN 1 AND 12);

ALTER TABLE lotes DROP CONSTRAINT IF EXISTS ck_lotes_fechas;
ALTER TABLE lotes ADD CONSTRAINT ck_lotes_fechas CHECK (fecha_inicio <= fecha_fin);

ALTER TABLE lotes DROP CONSTRAINT IF EXISTS ck_lotes_estado;
ALTER TABLE lotes ADD CONSTRAINT ck_lotes_estado
    CHECK (estado IN ('ABIERTO', 'EN_REVISION', 'CERRADO'));

ALTER TABLE lotes DROP CONSTRAINT IF EXISTS ck_lotes_tipo_periodo;
ALTER TABLE lotes ADD CONSTRAINT ck_lotes_tipo_periodo
    CHECK (tipo_periodo IN ('mensual', 'quincenal', 'semanal'));

COMMENT ON COLUMN lotes.total_esperado IS
    'Cuantos tickets se esperaban. NULL significa SIN EXPECTATIVA, no cero.';
COMMENT ON COLUMN lotes.total_escaneado IS
    'Contador cache que mantiene scan_service. La cuenta en vivo es GET /lotes/{id}.';
COMMENT ON COLUMN lotes.motivo IS
    'Motivo de la reapertura o de que exista el complemento. Texto libre: lo lee una persona.';


-- ---------------------------------------------------------------------
-- 2. La configuracion por empresa
-- ---------------------------------------------------------------------
ALTER TABLE companies ADD COLUMN IF NOT EXISTS lote_config JSON NULL;

COMMENT ON COLUMN companies.lote_config IS
    'Configuracion de lotes: tipo_periodo, exige_total_esperado, permite_reapertura, prefijo_folio. NULL = defaults de app/models/lote.py.';


-- ---------------------------------------------------------------------
-- 3. El gasto dice en que lote va
-- ---------------------------------------------------------------------
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS lote_id UUID
    REFERENCES lotes(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_tickets_lote ON tickets (lote_id);

COMMENT ON COLUMN tickets.lote_id IS
    'Lote de digitalizacion. NULL = digitalizado antes de los lotes o todavia sin asignar.';


-- ---------------------------------------------------------------------
-- 4. El archivo dice en que lote se escaneo
-- ---------------------------------------------------------------------
ALTER TABLE scan_files ADD COLUMN IF NOT EXISTS lote_id UUID
    REFERENCES lotes(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_scan_files_lote ON scan_files (lote_id);

COMMENT ON COLUMN scan_files.lote_id IS
    'Lote en el que se escaneo este archivo. NULL = escaneado antes de los lotes o inventario sin lote.';
