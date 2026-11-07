-- =====================================================================
-- 0020 - Multi-empresa y roles de verdad (P0-1)
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- Hasta ahora la empresa llegaba del cliente (`company_id` en el query o en
-- el cuerpo) y el usuario del token no tenia empresa: cualquiera autenticado
-- podia pedir el ticket de otra empresa cambiando un UUID. `tipos_gasto.py`
-- leia `usuario.company_id` en 10 sitios y `UserModel` no tenia esa columna
-- (bug P0-6): el router entero devolvia 500 y ni siquiera estaba registrado.
--
-- QUE RESUELVE
--
--   users.company_id              cada cuenta pertenece a una empresa (NULL =
--                                 operador sin empresa, que no ve nada salvo
--                                 que sea admin de arranque)
--   companies.plan/trial_hasta/   plan comercial, fin de la prueba de 30 dias
--     tickets_usados_este_mes     y uso del mes para el limite del plan
--   reconciliations.company_id    la conciliacion es de la empresa del ticket
--   scan_events.company_id        el evento es de la empresa del archivo
--   ticket_documents.company_id   el documento es de la empresa del ticket
--   Row Level Security            segunda cerradura en Postgres (ver abajo)
--
-- POR QUE `company_id` ES NULLABLE EN `users` Y EN LAS TRES TABLAS NUEVAS
--
-- Las cuentas y las filas viejas existen antes que las empresas: un NOT NULL
-- obligaria a inventar una empresa para el historico, que es justo el dato
-- que no se tiene. Las cuentas nuevas nacen con empresa (registro) o sin
-- ella (script) y el `resolver_empresa` de `app/core/deps.py` convierte el
-- NULL en 404. Los `company_id` nuevos siempre se rellenan al crear la fila.
--
-- POR QUE `ON DELETE SET NULL` Y NO `CASCADE`
--
-- Borrar una empresa no puede borrar sus usuarios (son la trazabilidad de
-- quien firmo que) ni sus conciliaciones, eventos o documentos (son el
-- historico). La fila se queda, sin empresa, y alguien la reasigna o la
-- archiva. Mismo argumento que en `scan_files.company_id`.
--
-- ROW LEVEL SECURITY: LA SEGUNDA CERRADURA, NO LA PRIMERA
-- --------------------------------------------------------
--
-- El filtro de verdad vive en la aplicacion (`resolver_empresa` en
-- `app/core/deps.py`): cada endpoint resuelve la empresa del usuario
-- autenticado y filtra por ella. El RLS es la red por si un endpoint se
-- olvida de filtrar: la sesion lleva `app.company_id` puesta por
-- `_usuario_del_token` (`SELECT set_config('app.company_id', ..., true)`)
-- y la politica solo deja pasar las filas de esa empresa.
--
-- La politica es FAIL-OPEN a proposito: si `app.company_id` esta vacio (sin
-- sesion de usuario: migraciones, siembra, respaldo, escaneo en segundo
-- plano), deja pasar todo. Un RLS fail-closed dejaria la base muda en cada
-- tarea que no pasa por el login, y una cerradura que rompe el respaldo es
-- peor que no tenerla. El peor caso del fail-open es que el RLS no filtre y
-- la aplicacion tenga que hacerlo sola, que es lo que ya hace.
--
-- `FORCE ROW LEVEL SECURITY`: sin el, el dueno de la tabla no pasa por el
-- RLS, y la API se conecta con el usuario que creo las tablas. Con el, la
-- politica se aplica tambien al dueno. Las filas sin empresa (`company_id
-- IS NULL`: archivos de inventario sin atribuir) las ve cualquiera: son del
-- buzon compartido, no de una empresa, y esconderlas rompería el inventario.
--
-- TABLAS SIN POLITICA, Y POR QUE
--
-- `auditoria` no tiene `company_id` (el rastro es global por diseno) y
-- `compra_items` tampoco (su empresa es la de su compra; la aplicacion
-- siempre la carga a traves de la compra, que si esta filtrada).
--
-- IDEMPOTENCIA: todo con `IF NOT EXISTS` / `DROP POLICY IF EXISTS`, como
-- las migraciones anteriores. `make migrar` la aplica una sola vez y la
-- registra en `schema_migrations`; `db/init.sql` la replica para las bases
-- nuevas (ver `test_init_sql_espeja_los_modelos.py`).


-- ---------------------------------------------------------------------
-- 1. La cuenta pertenece a una empresa
-- ---------------------------------------------------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS company_id UUID
    REFERENCES companies(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_users_company ON users (company_id);

COMMENT ON COLUMN users.company_id IS
    'Empresa de la cuenta (P0-1). NULL = operador sin empresa: no ve nada salvo que sea admin de arranque.';


-- ---------------------------------------------------------------------
-- 2. Plan comercial de la empresa
-- ---------------------------------------------------------------------
ALTER TABLE companies ADD COLUMN IF NOT EXISTS plan VARCHAR(20)
    NOT NULL DEFAULT 'basico';

ALTER TABLE companies ADD COLUMN IF NOT EXISTS trial_hasta
    TIMESTAMP WITH TIME ZONE;

ALTER TABLE companies ADD COLUMN IF NOT EXISTS tickets_usados_este_mes
    INTEGER NOT NULL DEFAULT 0;

-- El plan es un dato, no una cerradura: hoy no bloquea nada. El CHECK deja
-- constancia de los valores que existen; anadir un plan nuevo es alterar
-- este CHECK, no inventar un texto libre en cada fila.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_companies_plan'
    ) THEN
        ALTER TABLE companies ADD CONSTRAINT ck_companies_plan
            CHECK (plan IN ('basico', 'demo'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_companies_uso_no_negativo'
    ) THEN
        ALTER TABLE companies ADD CONSTRAINT ck_companies_uso_no_negativo
            CHECK (tickets_usados_este_mes >= 0);
    END IF;
END $$;

COMMENT ON COLUMN companies.plan IS
    'Plan comercial (P0-1): basico por defecto, demo para la empresa de demostracion.';
COMMENT ON COLUMN companies.trial_hasta IS
    'Hasta cuando dura la prueba gratuita. NULL = sin prueba (pago o demo sin caducidad).';
COMMENT ON COLUMN companies.tickets_usados_este_mes IS
    'Tickets creados este mes. Contador cache para el limite del plan; el conteo real sigue en tickets.';


-- ---------------------------------------------------------------------
-- 3. La conciliacion es de la empresa del ticket
-- ---------------------------------------------------------------------
ALTER TABLE reconciliations ADD COLUMN IF NOT EXISTS company_id UUID
    REFERENCES companies(id) ON DELETE SET NULL;

-- Relleno desde el ticket: una conciliacion no puede ser de dos empresas a
-- la vez, y el enlace ticket <-> movimiento solo vale si los dos son de la
-- misma. Las que quedan en NULL son las que no tienen ticket.
UPDATE reconciliations r
    SET company_id = t.company_id
    FROM tickets t
    WHERE r.ticket_id = t.id AND r.company_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_reconciliations_company ON reconciliations (company_id);

COMMENT ON COLUMN reconciliations.company_id IS
    'Empresa de la conciliacion (P0-1). Copia de tickets.company_id al crearla.';


-- ---------------------------------------------------------------------
-- 4. El evento de escaneo es de la empresa del archivo
-- ---------------------------------------------------------------------
ALTER TABLE scan_events ADD COLUMN IF NOT EXISTS company_id UUID
    REFERENCES companies(id) ON DELETE SET NULL;

UPDATE scan_events e
    SET company_id = f.company_id
    FROM scan_files f
    WHERE e.scan_file_id = f.id AND e.company_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_scan_events_company ON scan_events (company_id);

COMMENT ON COLUMN scan_events.company_id IS
    'Empresa del evento (P0-1). Copia de scan_files.company_id al registrarlo.';


-- ---------------------------------------------------------------------
-- 5. El documento es de la empresa del ticket
-- ---------------------------------------------------------------------
ALTER TABLE ticket_documents ADD COLUMN IF NOT EXISTS company_id UUID
    REFERENCES companies(id) ON DELETE SET NULL;

UPDATE ticket_documents d
    SET company_id = t.company_id
    FROM tickets t
    WHERE d.ticket_id = t.id AND d.company_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_ticket_documents_company ON ticket_documents (company_id);

COMMENT ON COLUMN ticket_documents.company_id IS
    'Empresa del documento (P0-1). Copia de tickets.company_id al guardarlo.';


-- ---------------------------------------------------------------------
-- 6. Row Level Security: segunda cerradura (fail-open, ver arriba)
-- ---------------------------------------------------------------------
-- Una politica por tabla con empresa. `company_id IS NULL` pasa siempre:
-- son filas sin empresa (inventario sin atribuir), no filas de otra empresa.
DO $$
DECLARE
    tabla text;
BEGIN
    FOREACH tabla IN ARRAY ARRAY[
        'tickets', 'bank_transactions', 'reconciliations',
        'accounting_mappings', 'scan_files', 'scan_events',
        'ticket_documents', 'cierres_periodo', 'productos', 'compras',
        'movimientos_inventario', 'tipos_gasto', 'lotes', 'users', 'companies'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tabla);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tabla);
        EXECUTE format('DROP POLICY IF EXISTS p_tenant ON %I', tabla);
        IF tabla = 'companies' THEN
            -- En `companies` la empresa ES la fila: se compara el id.
            EXECUTE format($pol$
                CREATE POLICY p_tenant ON %I USING (
                    current_setting('app.company_id', true) IS NULL
                    OR current_setting('app.company_id', true) = ''
                    OR id::text = current_setting('app.company_id', true)
                )$pol$, tabla);
        ELSE
            EXECUTE format($pol$
                CREATE POLICY p_tenant ON %I USING (
                    current_setting('app.company_id', true) IS NULL
                    OR current_setting('app.company_id', true) = ''
                    OR company_id IS NULL
                    OR company_id::text = current_setting('app.company_id', true)
                )$pol$, tabla);
        END IF;
    END LOOP;
END $$;
