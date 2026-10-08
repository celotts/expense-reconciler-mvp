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

    -- Las líneas del comprobante, crudas, tal como las leyó el lector (0010).
    --
    -- El modelo ya las pedía desde antes de que existiera el inventario, y se
    -- perdían en `capture.py:invoice_to_result`. Sin ellas una compra no tiene
    -- contenido: un comprobante dice que se gastó $4,093.80, no qué se compró.
    --
    -- NULL y no `[]`: NULL = el lector no produjo líneas (la ruta OCR no las
    -- extrae, solo el LLM). `[]` = produjo líneas y no eran ninguna. Con las dos
    -- en `[]` no se distingue "no tiene detalle" de "no lo sabemos".
    --
    -- OJO CON EL `NULL` DE VERDAD: la columna del modelo es
    -- `Column(JSON, none_as_null=True)`. Sin ese `True`, SQLAlchemy escribe un
    -- `None` de Python como el literal JSON `null`, y `items IS NULL` da falso.
    -- Medido: 5 tickets con `json 'null'` donde la documentacion de esta misma
    -- columna promete SQL NULL. El `WHERE items IS NULL` no devuelve nada y el
    -- fallo es silencioso.
    items JSONB,

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
    matched_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    -- Quien cambio el veredicto a mano, si alguien lo cambio.
    --
    -- `PATCH /reconciliations/{id}` deja que una persona corrija el `match_status`
    -- que puso el motor. Sin estas dos columnas, un PERFECT que decidio el motor
    -- y uno que aprobo una persona son la misma fila y no hay forma de saber
    -- cual es cual — que es el salto que este repo no hace en ningun otro sitio.
    -- Ver `confidence_source` y el precedente de `compras.confirmada_por`.
    --
    -- NULL = "el motor lo decidio y nadie lo ha tocado". No cadena vacia: `''`
    -- seria "alguien lo toco y no lo dijo".
    --
    -- Texto y no FK: la decision tiene que sobrevivir a la baja de la cuenta.
    revisado_por VARCHAR(255),
    revisado_at TIMESTAMP WITH TIME ZONE,

    -- Una revision sin fecha no se puede ordenar, y una fecha sin autor no dice
    -- quien. Las dos van o ninguna.
    CONSTRAINT ck_reconciliations_revision
        CHECK (
            (revisado_por IS NULL AND revisado_at IS NULL)
            OR (revisado_por IS NOT NULL AND length(trim(revisado_por)) > 0
                AND revisado_at IS NOT NULL)
        )
);

-- "Que reviso una persona", que es la consulta de una auditoria de cierre.
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

-- Registro de los archivos de la carpeta escaneada (0008).
--
-- Es el mismo DDL que emite app/models/scan_file.py y que esta en
-- db/migrations/0008_scan_ledger.sql, por la misma razon que las tablas de
-- arriba: una base creada desde cero y una migrada tienen que terminar con las
-- mismas reglas, o el mismo codigo se comporta de dos maneras segun cuando se
-- erigio la base.
--
-- Sin esto, `tickets.source_hash` es lo unico que sabe que un archivo ya se
-- leyo, y ese hash deduplica por CONTENIDO, no por ruta. No puede contestar
-- "este archivo ya fue mirado?" ni "por que fallo?", y las dos preguntas son
-- las que hacen que un escaner no reprocese lo mismo en cada corrida.
CREATE TABLE IF NOT EXISTS scan_files (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- Relativa a TICKETS_INPUT_DIR, no absoluta. Ver app/models/scan_file.py.
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
    -- SIN `UNIQUE`. Antes lo tenía, y era correcto cuando la tabla era "un
    -- documento por ticket". Con la cadena de versiones (0009) un ticket tiene
    -- VARIAS filas y solo UNA vigente, y el UNIQUE impediría hasta la segunda:
    -- una instalación nueva fallaría al guardar el primer reemplazo, sin que
    -- ningún test lo notara porque los tests construyen desde los modelos.
    -- Lo que no puede haber son dos VIGENTES, y eso lo garantiza el índice
    -- `ix_ticket_documents_sin_bifurcar` más la regla de que el vigente es el
    -- de mayor `version`.
    ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    contenido BYTEA NOT NULL,
    -- Lo declara el cliente y NO se usa tal cual para servirlo: el endpoint
    -- responde desde una lista cerrada de tipos que el navegador no puede
    -- ejecutar. Ver app/models/ticket_document.py.
    content_type VARCHAR(120),
    nombre_archivo VARCHAR(500),
    tamano INTEGER NOT NULL,
    -- El hash DE LOS BYTES DE ESTA VERSION. Antes se llenaba con
    -- `tickets.source_hash`, que es el hash de lo que el ESCANER leyó: en esa
    -- ruta coinciden, pero al reemplazar un documento los bytes nuevos quedaban
    -- sellados con el hash de los viejos y la columna describía otra cosa.
    sha256 VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    -- Cadena de versiones (0009). El comprobante digitalizado NO SE ALTERA: se
    -- apila. `reemplaza_a` apunta hacia atrás (la versión nueva dice a cuál
    -- reemplaza) porque la tabla es append-only y el UPDATE está prohibido, así
    -- que la vieja no puede apuntar a la nueva. El vigente es el de mayor
    -- `version`.
    reemplaza_a UUID REFERENCES ticket_documents(id) ON DELETE SET NULL,
    version INTEGER NOT NULL DEFAULT 1,
    actor VARCHAR(255),
    motivo TEXT,

    -- Las tres reglas de la cadena, con el mismo texto que la migración 0009.
    CONSTRAINT ck_ticket_documents_version
        CHECK (version >= 1),
    -- El motivo es obligatorio cuando hay reemplazo: la primera versión la pone
    -- el escáner y no hay nadie detrás; desde la segunda, "por qué cambiaste el
    -- papel" es la pregunta que un contador hace después.
    CONSTRAINT ck_ticket_documents_motivo_si_reemplaza
        CHECK (reemplaza_a IS NULL OR (motivo IS NOT NULL AND length(trim(motivo)) > 0)),
    -- Y el actor también: un reemplazo siempre lo hace una persona.
    CONSTRAINT ck_ticket_documents_actor_si_reemplaza
        CHECK (reemplaza_a IS NULL OR (actor IS NOT NULL AND length(trim(actor)) > 0))
);

CREATE INDEX IF NOT EXISTS ix_ticket_documents_ticket
    ON ticket_documents (ticket_id);

CREATE INDEX IF NOT EXISTS ix_ticket_documents_cadena
    ON ticket_documents (ticket_id, version);

-- Una versión puede ser reemplazada por UNA sola siguiente. Es lo que impide que
-- la cadena se bifurque, y por eso el vigente es "el de mayor version" y no "el
-- que nadie apunta".
CREATE UNIQUE INDEX IF NOT EXISTS ix_ticket_documents_sin_bifurcar
    ON ticket_documents (reemplaza_a)
    WHERE reemplaza_a IS NOT NULL;

-- --- La regla del motor (0009) ---------------------------------------------
--
-- Un trigger, y no una convención del código: así vale también para un `psql`,
-- una restauración mal hecha o el próximo script que se escriba.
--
-- UPDATE nunca. No hay un caso legítimo: un documento es una foto de un papel y
-- cambiarlo in situ es alterar la evidencia, no corregirla.
--
-- DELETE tampoco, SALVO que el ticket ya no exista. `ticket_id` tiene ON DELETE
-- CASCADE, y bloquearlo dejaría imposible borrar una empresa. La distinción es
-- "borré el gasto entero" contra "me robé el papel".
--
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

-- Registro del escaneo de carpeta (0008). Los cuatro indices y sus razones estan
-- en db/migrations/0008_scan_ledger.sql; aqui van por la misma regla: el
-- `init.sql` de arriba y esa migracion tienen que decir lo mismo, o una base
-- creada desde cero y una migrada se comportan distinto con el mismo codigo.
CREATE INDEX IF NOT EXISTS ix_scan_files_status
    ON scan_files (status);

CREATE INDEX IF NOT EXISTS ix_scan_files_content_hash
    ON scan_files (content_hash);

CREATE INDEX IF NOT EXISTS ix_scan_files_company
    ON scan_files (company_id, status);

CREATE INDEX IF NOT EXISTS ix_scan_events_file_created
    ON scan_events (scan_file_id, created_at);


-- =====================================================================
-- Inventario: productos, compras y el kardex (0010)
-- =====================================================================
--
-- Replicado de db/migrations/0010_inventario.sql, y por la misma regla que las
-- tablas de arriba: este archivo y esa migración tienen que decir lo mismo, o
-- una base creada desde cero y una migrada se comportan distinto con el mismo
-- código. Los argumentos largos de por qué el stock no es una columna y por qué
-- una compra no suma stock sin que una persona la autorice están en la
-- migración; aquí van solo las reglas.
--
-- --- Catalogo -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS productos (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- El codigo de barras o el SKU del proveedor. NULL cuando el papel no lo
    -- trae, y eso es legitimo: manyos comprobantes de autocomERCio no lo
    -- imprimen.
    --
    -- Unico SOLO cuando existe, por el mismo motivo que
    -- `ix_tickets_source_hash`: varios NULL en la misma columna no pueden
    -- violar unicidad, y los tickets manuales tampoco lo tienen. Un UNIQUE a
    -- secas impediria dar de alta el segundo producto sin codigo.
    codigo VARCHAR(64),

    nombre VARCHAR(200) NOT NULL,
    -- "PZA", "KG", "LT", "CAJA". Se guarda porque "3" de piezas y "3" de
    -- kilos son inventarios distintos, y sin la unidad el kardex no puede
    -- comparar dos movimientos del mismo producto.
    unidad_medida VARCHAR(20) NOT NULL DEFAULT 'PZA',

    -- Referencia, no el costo real: el costo lo determina cada compra, en
    -- `compra_items.costo_unitario`. Un precio unico en el producto seria el
    -- ultimo costo conocido, y eso es otra cosa.
    precio_referencia NUMERIC(12, 2),

    -- Para dar de baja un producto sin borrarlo: el historial del kardex lo
    -- necesita vivo. `activo = false` lo saca de las listas de captura pero no
    -- borra una sola fila de su historia.
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_productos_codigo_no_vacio
        CHECK (codigo IS NULL OR length(trim(codigo)) > 0),
    CONSTRAINT ck_productos_nombre_no_vacio
        CHECK (length(trim(nombre)) > 0),
    -- Un precio negativo es un descuento, y un descuento se registra en la
    -- compra, no como el precio del producto.
    CONSTRAINT ck_productos_precio_no_negativo
        CHECK (precio_referencia IS NULL OR precio_referencia >= 0)
);

-- El indice unico va sobre (empresa, codigo) y no solo sobre codigo, por la
-- misma razon que `ix_tickets_source_hash`: el codigo de barras es del mundo y
-- no lleva empresa dentro. Con unicidad solo por codigo, la segunda empresa que
-- compra el mismo producto no lo puede dar de alta. Ver
-- db/migrations/0005_source_hash_por_empresa.sql.
CREATE UNIQUE INDEX IF NOT EXISTS ix_productos_codigo
    ON productos (company_id, codigo)
    WHERE codigo IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_productos_empresa
    ON productos (company_id, activo);

-- Buscar por nombre es como se resuelve la cola de lineas sin producto, y sin
-- indice es un seq scan por cada linea.
CREATE INDEX IF NOT EXISTS ix_productos_nombre
    ON productos (company_id, lower(nombre));


-- --- La orden de compra ---------------------------------------------------

CREATE TABLE IF NOT EXISTS compras (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- UNIQUE y no un indice mas. Es el candado que impide contar dos veces el
    -- mismo comprobante; ver el bloque de arriba.
    ticket_id UUID NOT NULL UNIQUE REFERENCES tickets(id) ON DELETE CASCADE,

    -- Ver el docstring de `EstadoCompra` en app/core/enums.py. NO es lo mismo
    -- que `tickets.extraction_status`.
    estado VARCHAR(20) NOT NULL DEFAULT 'PROCESAR',

    -- Copia del comprobante en el momento de la compra. Redundante con
    -- `tickets`, y a proposito: una compra se lee y se concilia durante meses,
    -- y para entonces el ticket pudo recibir correcciones. Lo que se facturas
    -- es lo que estaba en el papel el dia que se aprobo.
    fecha DATE NOT NULL,
    proveedor_nombre VARCHAR(150),
    total NUMERIC(12, 2) NOT NULL,

    -- Quien autorizo que esto entrara al inventario. Texto y no FK, por el
    -- precedente de `tickets.spot_checked_by` y `cierres_periodo.cerrado_por`: la
    -- firma tiene que sobrevivir a la baja de la cuenta. Una entrada de
    -- inventario sin nombre de quien la aprobo no se puede auditar.
    confirmada_por VARCHAR(255),
    confirmada_at TIMESTAMP WITH TIME ZONE,

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- RECHAZADO es "esta compra no es una compra": un papel que se leyo mal y no
    -- dice lo que decia, una nota sin detalle. Hace falta como estado y no como
    -- borrado porque `compras.ticket_id` es UNIQUE: borrar la compra libera el
    -- ticket y el proximo reescaneo la vuelve a crear desde `registrar_compra`,
    -- en bucle y sin recordar que ya se habia mirado. Con el estado, el
    -- veredicto queda escrito y `registrar_compra` lo devuelve tal cual.
    --
    -- NO es `ExtractionStatus.RECHAZADO`: son dos maquinas distintas. Un ticket
    -- puede estar RECHAZADO y su compra EN_REVISION. Ver `EstadoCompra`.
    CONSTRAINT ck_compras_estado
        CHECK (estado IN ('PROCESAR', 'EN_REVISION', 'PROCESADO', 'RECHAZADO')),

    -- La regla que sostiene todo el diseno: PROCESADO exige firma y fecha, y
    -- cualquier otro estado no las tiene. Sin esto, el estado por si solo
    -- declara que el inventario se movio, y eso es exactamente lo que no se
    -- quiere: que se pueda marcar una compra como PROCESSED sin que nadie
    -- haya pasado.
    --
    -- RECHAZADO entra por la segunda rama sin tocar la constraint: sin
    -- `confirmada_por` y sin `confirmada_at`. Rechazar no es confirmar al reves.
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


-- --- Las lineas -----------------------------------------------------------

CREATE TABLE IF NOT EXISTS compra_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    compra_id UUID NOT NULL REFERENCES compras(id) ON DELETE CASCADE,

    -- NULL = la linea existe pero no se sabe que producto es. Es la cola de
    -- "descripcion no reconocida", y se consulta con `producto_id IS NULL`.
    --
    -- Ponerlo NOT NULL obligaria a inventar un producto para cada linea que no
    -- se reconociera, y ese producto inventado entraria al inventario con una
    -- descripcion tipo "Cadena Comercial) Uxxo,".
    producto_id UUID REFERENCES productos(id) ON DELETE SET NULL,

    -- La descripcion TAL COMO salio del comprobante, sin normalizar. Se guarda
    -- siempre, incluso cuando si hubo coincidencia: es la evidencia de lo que
    -- decia el papel, y sin ella no hay forma de auditar por que se eligio ese
    -- producto. Ver el bloque de arriba.
    descripcion VARCHAR(300) NOT NULL,

    cantidad NUMERIC(14, 3) NOT NULL,
    costo_unitario NUMERIC(12, 2),
    total NUMERIC(12, 2),

    -- La posicion de la linea en el papel. Sin esto no se puede cotejar una
    -- linea con la linea, que es la operacion completa cuando hay una diferencia.
    orden INTEGER NOT NULL DEFAULT 0,

    -- Quien asigno el producto, de NULL a un id. Se guarda el dato del acto, no
    -- solo el resultado: "lo asigno el sistema" y "lo asigno Ana" son
    -- respuestas distintas a "de donde salio este producto del catalogo".
    producto_asignado_por VARCHAR(255),
    producto_asignado_at TIMESTAMP WITH TIME ZONE,

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_compra_items_descripcion_no_vacia
        CHECK (length(trim(descripcion)) > 0),

    -- Cantidad positiva SIEMPRE. Una cantidad negativa seria una devolucion, y
    -- una devolucion es una SALIDA, no una linea de compra con signo cambiado:
    -- mezclarlas hace que el kardex tenga dos formas de restar y no se pueda
    -- sumar sin mirar el tipo.
    CONSTRAINT ck_compra_items_cantidad_positiva
        CHECK (cantidad > 0),

    CONSTRAINT ck_compra_items_costos_no_negativos
        CHECK ((costo_unitario IS NULL OR costo_unitario >= 0)
               AND (total IS NULL OR total >= 0)),

    -- Misma idea que `ck_compras_confirmacion`: si hay producto asignado, tiene
    -- que haber alguien y una fecha. Una linea con producto sin saber quien lo
    -- eligio no se puede auditar.
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

-- La cola: "que lineas no tienen producto". Parcial, a proposito, porque lo
-- unico que se consulta en caliente es esa cola, y un indice sobre toda la tabla
-- seria del tamano del historico entero de compras.
CREATE INDEX IF NOT EXISTS ix_compra_items_sin_producto
    ON compra_items (compra_id)
    WHERE producto_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_compra_items_producto
    ON compra_items (producto_id)
    WHERE producto_id IS NOT NULL;


-- --- El kardex ------------------------------------------------------------
--
-- El stock NO es una columna. Ver el bloque de arriba.

CREATE TABLE IF NOT EXISTS movimientos_inventario (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    producto_id UUID NOT NULL REFERENCES productos(id) ON DELETE CASCADE,

    -- Ver `TipoMovimiento`. `cantidad` es positiva siempre y el signo lo da el
    -- tipo, para que el SUM sea un SUM y no un SUM con un CASE.
    tipo VARCHAR(20) NOT NULL,
    cantidad NUMERIC(14, 3) NOT NULL,

    -- De donde viene este movimiento, para poder deshacerlo. La compra es lo
    -- unico que lo genera hoy, pero la compra se puede anular y el movimiento
    -- tiene que seguir siendo rastreable despues de eso.
    --
    -- 'COMPRA' y 'AJUSTE' son las unicas que se escriben en esta migracion.
    -- 'VENTA' esta en el enum porque la regla de negocio lo exige ("compra
    -- suma, venta resta") y porque un enum del que hay que quitar un valor
    -- para anadirlo despues es un enum mal puesto.
    referencia_tipo VARCHAR(20) NOT NULL,
    referencia_id UUID,

    -- Quien lo autoriza. Mismo criterio que `compras.confirmada_por`.
    actor VARCHAR(255) NOT NULL,

    -- Lo que decia el papel cuando se genero. Copia congelada a proposito: si
    -- el producto se renombra o se corrige la cantidad en su linea, el kardex
    -- tiene que seguir diciendo lo que se aprobo ese dia.
    descripcion_origen VARCHAR(300),

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_movimientos_tipo
        CHECK (tipo IN ('ENTRADA', 'SALIDA', 'AJUSTE')),
    CONSTRAINT ck_movimientos_referencia_tipo
        CHECK (referencia_tipo IN ('COMPRA', 'VENTA', 'AJUSTE')),

    -- El signo va en el tipo, nunca en la cantidad. Ver `TipoMovimiento`.
    CONSTRAINT ck_movimientos_cantidad_positiva
        CHECK (cantidad > 0),

    CONSTRAINT ck_movimientos_actor
        CHECK (actor IS NOT NULL AND length(trim(actor)) > 0)
);

-- La consulta de stock: SUM por producto. Este indice es el que la hace
-- barata, y es el indice mas importante de la migracion: sin el, cada lectura
-- de inventario recorre el kardex completo.
CREATE INDEX IF NOT EXISTS ix_movimientos_producto
    ON movimientos_inventario (company_id, producto_id, created_at);

-- Para auditar "que movio este producto en este dia", que es la pregunta que
-- aparece cuando el conteo fisico no cuadra.
CREATE INDEX IF NOT EXISTS ix_movimientos_referencia
    ON movimientos_inventario (referencia_tipo, referencia_id);


-- --- Regla del motor: el kardex no se reescribe --------------------------
--
-- Un trigger, y no una convencion del codigo, por la misma razon que los de
-- `ticket_documents` en init.sql: asi vale tambien para un `psql`.
--
-- Por que UPDATE prohibido: corregir un movimiento en sitio cambia el historial
-- del inventario. El movimiento estaba, ahora dice otra cosa, y no hay forma de
-- saber que estuvo. La correccion es un AJUSTE nuevo.
--
-- Por que DELETE prohibido: es lo que hace que el kardex sea la verdad del
-- stock. Si se puede borrar un movimiento, `SELECT SUM(cantidad)` deja de
-- describir el inventario y el campo de la respuesta se vuelve una opinion.
--
-- La excepcion es la cascada: borrar la empresa borra su inventario entero, y
-- bloquearlo dejaria imposible borrar una empresa.
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

-- Un producto no se puede dejar con stock negativo por un error de captura: es
-- el estado que mas veces se descubre tarde y mas caro cuesta. La exception es
-- el AJUSTE, que existe justo para eso y se escribe con un actor.
CREATE OR REPLACE FUNCTION movimientos_no_dejar_negativo() RETURNS trigger AS $$
DECLARE
    actual NUMERIC(14, 3);
BEGIN
    -- El signo va en el SUM. Antes era `SUM(cantidad)` a secas, que contaba una
    -- SALIDA como si sumara: con ENTRADA 10 y SALIDA 4 decia 14 en vez de 6, y
    -- de ahi en adelante cada comprobacion de este trigger estaba corrida por el
    -- mismo error —incluida la que decide si el stock queda negativo.
    SELECT COALESCE(
        SUM(CASE WHEN m.tipo = 'SALIDA' THEN -m.cantidad ELSE m.cantidad END), 0
    ) INTO actual
    FROM movimientos_inventario m
    WHERE m.producto_id = NEW.producto_id;

    -- `SALIDA` y solo `SALIDA` resta. Antes era `IF tipo = 'ENTRADA' THEN suma
    -- ELSE resta`, que hacia que AJUSTE restara mientras `stock_de()` lo suma.
    -- Era inerte porque la unica via que escribia aqui era `confirmar_compra`, y
    -- esa solo produce ENTRADA; `POST /inventario/movimientos` es el camino que
    -- lo vuelve vivo. La pregunta es la misma que hace
    -- `TipoMovimiento.suma_stock` en Python.
    IF NEW.tipo = 'SALIDA' THEN
        actual := actual - NEW.cantidad;
    ELSE
        actual := actual + NEW.cantidad;
    END IF;

    IF actual < 0 THEN
        -- `%s` y no `%.3f`: plpgsql no tiene especificadores de precision, y
        -- `%.3f` sale literal pegado al numero ("-15.000.3f"), que es peor que
        -- no dar el numero. `to_char` es lo que si lo formatea.
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


-- =====================================================================
-- Productos que se crean solos, y como distinguirlos (0011)
-- =====================================================================
--
-- Replicado de db/migrations/0011_productos_origen.sql, por la misma regla que
-- las tablas de arriba: este archivo y esa migracion tienen que decir lo mismo.
-- El argumento largo de por que crear-y-marcar es mejor que no-crear y que
-- crear-sin-marcar esta en la migracion.
--
ALTER TABLE productos ADD COLUMN IF NOT EXISTS origen VARCHAR(20) NOT NULL DEFAULT 'MANUAL';
ALTER TABLE productos ADD COLUMN IF NOT EXISTS verificado BOOLEAN NOT NULL DEFAULT TRUE;

-- Las filas que ya existian se dejan como estaban: un producto dado de alta a
-- mano antes de esta migracion es 'MANUAL' y verificado, que es lo cierto. El
-- DEFAULT solo aplica a las de aqui en adelante, y no hay forma de distinguirlas
-- sin mirar la tabla, asi que se asume lo correcto: lo que habia, lo puso una
-- persona.

-- El enum y la constraint van en los dos motores, como siempre. La lista tiene que
-- coincidir con `ProductoOrigen` de app/core/enums.py.
ALTER TABLE productos DROP CONSTRAINT IF EXISTS ck_productos_origen;
ALTER TABLE productos ADD CONSTRAINT ck_productos_origen
    CHECK (origen IN ('MANUAL', 'OCR'));

-- Un producto de OCR con codigo de barras es verificable solo, y no por una
-- persona. Un producto de OCR SIN codigo no lo es. La constraint lo dice, para
-- que un INSERT no pueda decir "de OCR y ya verificado" por descuido.
ALTER TABLE productos DROP CONSTRAINT IF EXISTS ck_productos_verificado_ocr;
ALTER TABLE productos ADD CONSTRAINT ck_productos_verificado_ocr
    CHECK (verificado = true OR origen = 'OCR');

-- La cola de revision. Parcial y por `origen`, porque lo unico que se consulta en
-- caliente es "que hay que revisar", y un indice sobre toda la tabla seria del
-- tamano del catalogo entero.
--
-- El indice incluye `created_at` porque el orden de trabajo natural es "el mas
-- viejo primero": los productos mas viejos son los que mas veces se han
-- re-leido y mas veces se han creado.
CREATE INDEX IF NOT EXISTS ix_productos_sin_verificar
    ON productos (company_id, created_at)
    WHERE verificado = false;


-- El nombre NORMALIZADO, en su propia columna (0011)
-- Replicado de db/migrations/0011_productos_origen.sql.
--
-- El nombre NORMALIZADO, en su propia columna.
--
-- Sin esta columna, la resolucion tendria que comparar contra `lower(nombre)`, y
-- eso no funciona: `lower('Café molido')` es 'café molido' —con tilde—, mientras
-- que el objetivo normalizado es 'cafe molido'. La comparacion daria distinta y
-- "Café molido" y "Cafe molido" crearian dos productos, que es justo el problema
-- que `normalizar_descripcion` existe para evitar.
--
-- Se llena con un `@validates` en el modelo, no a mano en cada INSERT: una
-- columna "normalizada" que hay que acordarse de mantener es una columna que un
-- dia no se mantiene, y el fallo es silencioso.
--
-- Lo que hace falta aqui es un indice PLANO, no un UNIQUE. Dos filas con el
-- mismo nombre normalizado pueden convivir —una dada de alta a mano y otra del
-- OCR, por ejemplo— y la resolucion se queda con la mas antigua, que es la que
-- ya tiene stock. Un UNIQUE rechazaria el segundo INSERT legitimo con un error
-- que no explica nada.
ALTER TABLE productos ADD COLUMN IF NOT EXISTS nombre_normalizado VARCHAR(200);

-- Backfill para las filas que ya existian. Sin esto, un producto dado de alta
-- antes de esta migracion tendria `nombre_normalizado` NULL y jamas coincidiria
-- con una linea nueva — es decir, el producto se duplicaria en vez de
-- reutilizarse, que es el fallo que esta columna viene a evitar.
UPDATE productos SET nombre_normalizado = lower(nombre) WHERE nombre_normalizado IS NULL;

CREATE INDEX IF NOT EXISTS ix_productos_nombre_normalizado
    ON productos (company_id, nombre_normalizado);


-- =====================================================================
-- El impuesto es de la partida (0012)
-- =====================================================================
--
-- Replicado de db/migrations/0012_el_impuesto_es_de_la_partida.sql, por la
-- misma regla que las tablas de arriba: este archivo y esa migración tienen que
-- decir lo mismo, o una base creada desde cero y una migrada se comportan
-- distinto con el mismo código.
--
-- **FALTABAN LAS TRES. Medido, no supuesto.** Los modelos declaran
-- `tickets.ieps_amount` (`app/models/ticket.py:136`) y `compra_items.iva_linea`
-- / `.ieps_linea`, y las tres columnas no estaban en este archivo. Como
-- `ticket_persistence.py:114` escribe `ieps_amount` en **cada** INSERT de
-- ticket, una base creada desde cero (que es la unica forma de que corra este
-- archivo) reventaba con:
--
--     ERROR: column "ieps_amount" of relation "tickets" does not exist
--
-- o sea un 500 por cada comprobante, y el ticket perdido. Y no lo cazaba
-- ningun test: la suite construye el esquema desde los MODELOS (`conftest.py`),
-- nunca desde este archivo, asi que el modelo y el DDL pueden divergir
-- libremente y el test sigue en verde. Es el mismo modo de fallo que
-- `docs/known-issues.md` §9, en tres columnas nuevas.
--
-- LA MEDIDA QUE MOTIVO LA MIGRACION, para que no se lea como un dato más:
--
--     Walmart, un solo ticket:
--       SUBTOTAL        217.27
--       IVA  16.0%        8.14
--       IEPS  8.0%        8.59   <- no tenía dónde meterse
--       TOTAL           234.00
--
--     217.27 + 8.14 = 225.41, y el total es 234.00: la diferencia SON los 8.59
--     del IEPS. Con `MONEY_TOLERANCE` de un centimo, ese comprobante NUNCA podia
--     pasar el check del gate, aunque los tres numeros se leyeran perfectos. Un
--     gate que rechaza lecturas correctas hace que `subtotal_plus_tax_mismatch`
--     deje de significar "leíste mal".
--
-- Y el problema de fondo es mas grande que el IEPS: **el impuesto depende de la
-- PARTIDA, no del comprobante.** En ese mismo ticket:
--
--     BOLILLO     33.00  T   tasa 0
--     ACTII ESQ   21.00  C   tasa 0
--     ARTIELLIQ   35.00  A   tasa 16
--     ZOTE BARRA  24.00  A   tasa 16
--     PAKETAXO    30.00  C   tasa 0 + IEPS
--
-- Un solo comprobante con tres tratamientos fiscales. Por eso el impuesto se
-- guarda **por linea**, que es donde vive la tasa, y el ticket lo deriva. Por
-- eso `iva_linea`/`ieps_linea` son de `compra_items` y no del ticket.

-- El IEPS del comprobante completo, cuando el papel lo imprime. Es la suma de
-- las partidas, no una tasa: se guarda el importe, que es lo que el papel dice.
--
-- NULL y no 0.00 por la misma razon que `items`: NULL es "el comprobante no trae
-- IEPS" y 0.00 seria "trae IEPS y es cero". Confundirlas hace que un ticket sin
-- ese impuesto parezca un ticket que se leyo mal.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS ieps_amount NUMERIC(12, 2);

-- El impuesto POR LINEA. Es donde vive la tasa, y por eso va aqui y no solo en
-- el ticket: un comprobante puede tener partidas a 0%, a 16% y con IEPS.
--
-- NULL = "esta linea no tiene impuesto separado" o "no se leyo", y no es lo
-- mismo que 0.00. Con la columna en NULL el gate cae al total del comprobante,
-- que es la unica forma de comprobar algo sin datos por linea.
ALTER TABLE compra_items ADD COLUMN IF NOT EXISTS iva_linea NUMERIC(12, 2);
ALTER TABLE compra_items ADD COLUMN IF NOT EXISTS ieps_linea NUMERIC(12, 2);

-- El comentario en la base, porque el nombre de la columna no dice que el valor
-- es un IMPORTE y no una tasa. `iva_linea = 8.14` no significa 8.14%: son 8.14
-- pesos de IVA sobre esa linea. Es el mismo texto que la migracion.
COMMENT ON COLUMN compra_items.iva_linea IS
    'IVA de ESTA linea, en pesos. No es una tasa: 8.14 significa 8.14 pesos.';
COMMENT ON COLUMN compra_items.ieps_linea IS
    'IEPS de ESTA linea, en pesos. No es una tasa.';
COMMENT ON COLUMN tickets.ieps_amount IS
    'IEPS total del comprobante, en pesos. NULL cuando el papel no lo imprime.';

-- La consulta que mas se va a hacer: "las partidas de una compra con su
-- impuesto". Sin indice, cada confirmacion de compra recorre la tabla entera.
CREATE INDEX IF NOT EXISTS ix_compra_items_impuestos
    ON compra_items(compra_id)
    WHERE ieps_linea IS NOT NULL OR iva_linea IS NOT NULL;


-- =====================================================================
-- El registro de que esta base ya esta al dia (0015)
-- =====================================================================
--
-- **POR QUE ESTA TABLA EXISTE, Y POR QUE NO LA HABIA.**
--
-- Hasta ahora no habia forma de responder "¿esta base necesita migraciones?".
-- Se podia mirar el esquema, y el esquema NO RESPONDE: `init.sql` trae las
-- mismas 105 cosas que las 12 migraciones (verificado), asi que una base creada
-- desde cero y una migrada se ven IDENTICAS. Se probo usar una tabla como
-- discriminante —`cierres_periodo` es la 0007, si existe es vieja— y da la
-- respuesta equivocada en las dos direcciones: una base recien creada tambien la
-- tiene, porque `init.sql` la crea.
--
-- Es el problema clasico de un esquema sin version: **un esquema completo no
-- dice de que parte del camino viene.** Por eso Alembic lleva una tabla de
-- version y este proyecto, sin Alembic, necesita la misma cosa a mano.
--
-- QUE SE ESCRIBE AQUI
-- -------------------
-- El NOMBRE del archivo, no un numero. Un numero obliga a llevar la cuenta en
-- la cabeza y a renumerar; el nombre es el que se corre, asi que un error de
-- orden se ve en el nombre y no en un `0007` que alguien shifting.
--
-- `aplicada_at` es el momento real. Y es texto, no un id de usuario: el registro
-- dice QUE se aplico, no QUIEN lo autorizo — la migracion es DDL y el DDL no
-- tiene firma.
CREATE TABLE IF NOT EXISTS schema_migrations (
    nombre VARCHAR(255) PRIMARY KEY,
    aplicada_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Una base creada desde `init.sql` esta AL DIA con las 12 migraciones, porque
-- `init.sql` las replica todas. Sin estas filas, `make migrar` correria 12
-- archivos sobre una base que ya tiene todo: `IF NOT EXISTS` los haria
-- idempotentes, pero el operador veria "aplicando 0002..." y pensaria que su
-- base estaba vieja. El registro tiene que decir la verdad, y la verdad es que
-- esa base no necesita nada.
--
-- `INSERT ... ON CONFLICT DO NOTHING` porque el `init.sql` se puede volver a
-- correr sin reventar, y estas filas ya pueden estar.
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
    ('0013_compras_rechazables.sql')
ON CONFLICT (nombre) DO NOTHING;