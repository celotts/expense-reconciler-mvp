-- =====================================================================
-- 0018 - Tipos de gasto: el catalogo de cuentas
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- La pregunta que responde es "en que cuenta va este gasto", y hoy el sistema
-- no la puede contestar: `tickets.category` es un texto libre de 100 caracteres
-- que **nunca se llena** —el parser la deja en `None` siempre, en las tres rutas
-- de lectura— y el unico catalogo que existe esta en codigo
-- (`app/core/categorias.py`), que no se puede dar de alta ni de baja sin tocar
-- el repositorio.
--
-- Que el gasto quede sin cuenta no es un dato incompleto: es un dato que no
-- existe. El cierre mensual y la analitica agrupan por `category`, asi que todo
-- gasto recien capturado cae en "Sin clasificar" hasta que alguien lo escriba a
-- mano, y eso es trabajo que el sistema todavia no hace.
--
-- QUE RESUELVE
--
--   tipos_gasto             el catalogo de cuentas, por empresa
--   tickets.tipo_gasto_id   cada gasto dice en que cuenta va
--   productos.tipo_gasto_id cada producto dice en que cuenta va por omision
--
-- Y EL ESTADO DE AUTORIZACION, que va en la misma migracion a proposito:
-- autorizar un gasto y leer bien el papel son preguntas distintas, asi que son
-- columnas distintas. Ver la nota de abajo.
--
--
-- POR QUE LA CUENTA ESTA EN `tickets` Y EN `productos`
--
-- Son dos preguntas con respuestas distintas y por eso las dos tienen su columna:
--
--   - El GASTO ya se sabe a que cuenta va: lo que se compro en esa fecha. Es el
--     dato que se reporta y el que agrupa el cierre.
--   - El PRODUCTO dice donde va *por omision* lo que se compre de el. No es lo
--     mismo: las mismo panecillos pueden ir a despensa este mes y a un evento el
--     siguiente. Por eso es una AYUDA, no una regla, y por eso no se copia del
--     producto al gasto automaticamente: hacerlo seria decidir por la persona
--     en que cuenta va un gasto, que es exactamente lo que este producto no
--     hace en ningun otro campo.
--
--
-- POR QUE `tickets.category` NO SE BORRA
--
-- Porque `analitica.py`, `reporte_cierre.py` y la exportacion a CONTPAQI agrupan
-- por ese texto, y reescribirlos todos para leer un id es un cambio grande que
-- no aporta nada. La tabla es la fuente de verdad y el texto es la etiqueta
-- COPIADA por la misma operacion que asigna el id; hay un test que muere si se
-- desincronizan. Es una copia con un solo escritor, que si se administra es un
-- cache; dos -- que se escriben solas es una segunda verdad.
--
--
-- POR QUE NO HAY SEED EN SQL
--
-- El catalogo es POR EMPRESA, y una migracion no sabe que empresa va a existir.
-- Sembrarlo en SQL seria sembrarlo para una empresa inventada. Lo siembra
-- `tipos_gasto_service.asegurar_catalogo(company_id)`, que es idempotente, desde
-- la creacion de empresa y desde `scripts/sembrar_catalogo.py` para las que ya
-- existen. Es la unica tabla del proyecto cuyos datos no se declaran aqui, y
-- esta nota es la que dice por que.


-- ---------------------------------------------------------------------
-- 1. El catalogo de cuentas
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tipos_gasto (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- El codigo de la cuenta. Es lo que se agrupa, lo que va al archivo de
    -- CONTPAQI y lo que se lee en un reporte. Se guarda en mayusculas porque el
    -- agrupamiento es por texto y "supermercado" y "SUPERMERCADO" siendo la
    -- misma cuenta darian dos filas en el cierre.
    clave VARCHAR(50) NOT NULL,

    -- Lo que ve la persona. Se separa de la clave porque el codigo es para
    -- máquinas y para el contador, y el nombre es para quien lee la pantalla.
    nombre VARCHAR(120) NOT NULL,

    -- Gasto variable: depende del volumen (consumo, viaticos) o es fijo
    -- (renta, suedos). Es lo que separa "subió porque hubo más actividad" de
    -- "subió porque se fue de las manos", que es la pregunta que un cierre tiene
    -- que poder contestar. Viene de `Categoria.variable` en el catalogo viejo.
    variable BOOLEAN NOT NULL DEFAULT TRUE,

    -- Dar de baja, no borrar (regla 24). Un gasto ya contabilizado apunta a esta
    -- fila para siempre: si se borra, el reporte de un mes que ya se cerro no
    -- puede decir en que cuenta estaba.
    activo BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_tipos_gasto_clave_no_vacia CHECK (length(trim(clave)) > 0),
    CONSTRAINT ck_tipos_gasto_nombre_no_vacio CHECK (length(trim(nombre)) > 0),
    CONSTRAINT ck_tipos_gasto_clave_mayuscula CHECK (clave = upper(clave))
);

-- Una cuenta por clave y por empresa. Sin este indice, dos altas con la misma
-- clave crean dos cuentas y el cierre agrupa dos filas que son la misma cuenta.
CREATE UNIQUE INDEX IF NOT EXISTS ux_tipos_gasto_empresa_clave
    ON tipos_gasto (company_id, clave);

-- La lista de la pantalla: las de la empresa, las vivas primero y por nombre.
CREATE INDEX IF NOT EXISTS ix_tipos_gasto_empresa_activo
    ON tipos_gasto (company_id, activo);


-- ---------------------------------------------------------------------
-- 2. El gasto dice en que cuenta va
-- ---------------------------------------------------------------------
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS tipo_gasto_id UUID
    REFERENCES tipos_gasto(id) ON DELETE RESTRICT;

-- El reporte del cierre agrupa por esta columna, asi que sin el indice cada
-- cierre lee la tabla entera de tickets.
CREATE INDEX IF NOT EXISTS ix_tickets_tipo_gasto ON tickets (tipo_gasto_id);

COMMENT ON COLUMN tickets.tipo_gasto_id IS
    'Cuenta de gasto. NULL significa SIN CLASIFICAR, que es un dato sobre la disciplina de captura, no una cuenta mas.';


-- ---------------------------------------------------------------------
-- 3. El producto dice en que cuenta va por omision
-- ---------------------------------------------------------------------
ALTER TABLE productos ADD COLUMN IF NOT EXISTS tipo_gasto_id UUID
    REFERENCES tipos_gasto(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS ix_productos_tipo_gasto ON productos (tipo_gasto_id);

COMMENT ON COLUMN productos.tipo_gasto_id IS
    'Cuenta propuesta para las compras de este producto. NO se copia al gasto: lo que se compro en una fecha puede ir a otra cuenta.';


-- ---------------------------------------------------------------------
-- 4. La autorizacion, que NO es lo mismo que haber leido bien el papel
-- ---------------------------------------------------------------------
--
-- `ExtractionStatus` responde "como se leyo el papel". `AUTO_APROBADO` significa
-- que el automatismo se responsabiliza de la lectura, y eso NO es que alguien haya
-- autorizado el gasto. Meter la autorizacion ahi seria afirmar que un gasto que
-- leyo solo una maquina quedo autorizado por alguien, y el cierre mensual no
-- podria distinguir los dos casos.
--
-- Son dos preguntas, asi que son dos columnas. Y el que autoriza sale del token,
-- nunca del cuerpo (regla 23): aceptarlo del body seria una puerta para autorizar
-- en nombre de otra persona.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS estado_autorizacion VARCHAR(20)
    NOT NULL DEFAULT 'PENDIENTE';

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS autorizado_por VARCHAR(255) NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS autorizado_at TIMESTAMP WITH TIME ZONE NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS motivo_rechazo TEXT NULL;

-- `PENDIENTE` / `APROBADA` / `RECHAZADA`. La constraint esta en la base y no solo
-- en el enum, por el mismo motivo que `ck_compras_confirmacion`: sin ella, un
-- UPDATE basta para que una autorizacion parezca firmada.
ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_estado_autorizacion;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_estado_autorizacion
    CHECK (estado_autorizacion IN ('PENDIENTE', 'APROBADA', 'RECHAZADA'));

-- APROBADA y RECHAZADA exigen quien y cuando. PENDIENTE prohibe los dos: un
-- gasto autorizado por nadie es un gasto que nadie autorizo.
ALTER TABLE tickets DROP CONSTRAINT IF EXISTS ck_tickets_autorizacion_firmada;
ALTER TABLE tickets ADD CONSTRAINT ck_tickets_autorizacion_firmada CHECK (
    (estado_autorizacion = 'APROBADA' AND autorizado_por IS NOT NULL AND autorizado_at IS NOT NULL AND motivo_rechazo IS NULL)
 OR (estado_autorizacion = 'RECHAZADA' AND autorizado_por IS NOT NULL AND autorizado_at IS NOT NULL AND motivo_rechazo IS NOT NULL)
 OR (estado_autorizacion = 'PENDIENTE' AND autorizado_por IS NULL AND autorizado_at IS NULL AND motivo_rechazo IS NULL)
);

-- La cola de autorizacion: lo que falta por autorizar.
CREATE INDEX IF NOT EXISTS ix_tickets_autorizacion
    ON tickets (estado_autorizacion) WHERE estado_autorizacion <> 'APROBADA';


-- ---------------------------------------------------------------------
-- 5. Y QUE NADIE CAMBIE UNA AUTORIZACION SIN QUE SE NOTE
-- ---------------------------------------------------------------------
--
-- Una autorizacion reescrita es una autorizacion que no ocurrio. El trigger
-- prohibe UPDATE de las cuatro columnas de autorizacion, y el mismo camino que
-- protege el comprobante digitalizado (regla 18): la regla es del motor, no del
-- codigo, y por eso vale tambien para un `psql`.
--
-- LA EXCEPCION, Y POR QUE ES UNA BANDERA DE SESION
--
-- Cambiar el estado ES reescribir las columnas, asi que el trigger tiene que
-- tener una salida. La salida NO es aflojar la condicion —eso dejaria pasar el
-- UPDATE de cualquiera— sino una variable de sesion que el codigo de confianza
-- levanta y Postgres vuelve a bajar sola al cerrar la transaccion:
--
--     SET LOCAL sesion.autorizacion_permitida = 'si';
--
-- `SET LOCAL` desaparece en el COMMIT, asi que la ventana existe solo durante la
-- transaccion que la abrio. Un `psql` puede imitarla a proposito, que es
-- explicito y queda en el log; lo que no puede es cambiarla sin querer.
CREATE OR REPLACE FUNCTION trg_tickets_autorizacion_no_se_reescribe()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('sesion.autorizacion_permitida', true) = 'si' THEN
        RETURN NEW;
    END IF;

    IF NEW.estado_autorizacion IS DISTINCT FROM OLD.estado_autorizacion
       OR NEW.autorizado_por IS DISTINCT FROM OLD.autorizado_por
       OR NEW.autorizado_at IS DISTINCT FROM OLD.autorizado_at
       OR NEW.motivo_rechazo IS DISTINCT FROM OLD.motivo_rechazo THEN
        RAISE EXCEPTION 'una autorizacion no se reescribe: se cambia de estado con su motivo'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tickets_autorizacion_inmutable ON tickets;
CREATE TRIGGER trg_tickets_autorizacion_inmutable
    BEFORE UPDATE ON tickets
    FOR EACH ROW
    EXECUTE FUNCTION trg_tickets_autorizacion_no_se_reescribe();