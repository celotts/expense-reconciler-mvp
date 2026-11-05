-- =====================================================================
-- 0010 - Inventario: productos, compras y el kardex
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- `db/init.sql` solo corre cuando Postgres arranca con el volumen vacio. Una
-- base que ya existe no ve los cambios del modelo. Sin esta migracion, el
-- inventario levanta el API, responde 200 y revienta en la primera consulta con
-- UndefinedTable: "productos".
--
-- QUE RESUELVE
--
-- Cuatro tablas:
--
--   productos               el catalogo por empresa
--   compras                 la orden de compra: una por comprobante, con su estado
--   compra_items            las lineas del comprobante
--   movimientos_inventario   el kardex: la unica fuente del stock
--
-- Y UNA COLUMNA NUEVA en `tickets`: `items`, que ya se extraia y se tiraba.
--
--
-- POR QUE `items` ESTABA EN EL AI Y NO EN LA BASE
--
-- `app/services/ai_extractor.py` le pide al modelo las lineas del comprobante
-- (`description, quantity, unit_price, total, tax_rate, tax_amount`) desde antes
-- de que existiera el inventario. El modelo las devuelve, `capture.py` las
-- traduce... y no. `invoice_to_result` armaba el `TicketExtractionResult` sin ese
-- campo, de modo que la linea desaparecia antes de llegar al gate. El gasto se
-- guardaba (total, IVA, proveedor) y el contenido se perdia.
--
-- Guardarlas es lo que hace posible todo lo demas de esta migracion. Y es
-- exactamente el dato que hace falta para inventario: un comprobante dice que
-- se gasto $4,093.80, y el inventario necesita saber que se compro que.
--
-- Es JSON y no una tabla de lineas en `tickets` porque la tabla `compra_items`
-- ya es la version normalizada, consultable y con FK a producto. Guardar las
-- dos seria duplicar la verdad. Aqui queda la LECTURA CRUDA del modelo —sin
-- normalizar, tal como salio— y `compra_items` es la version ya revisada por
-- una persona. (Ver `inventario_service.registrar_compra`, que es donde una se
-- copia de la otra.)
--
--
-- POR QUE EL STOCK NO ES UNA COLUMNA
--
-- No hay `productos.stock`. El stock es la SUMA de `movimientos_inventario`:
--
--     SELECT producto_id, SUM(cantidad) FROM movimientos_inventario
--     WHERE company_id = ? GROUP BY producto_id
--
-- Un campo `stock` es una copia que se desincroniza. Se desincroniza siempre:
-- el dia que un movimiento se escriba en la tabla y la copia no, o que un
-- DELETE (que el kardex no permite, pero un DELETE de empresa si dispara por
-- CASCADE) no la limpie, los dos numeros dicen cosas distintas y no hay forma
-- de saber cual es el bueno. La suma no se puede desincronizar porque no hay
-- nada que sincronizar: es la definicion.
--
-- Cuesta una agregacion por consulta de stock, que es lo correcto: una lectura
-- de stock es una operacion, y 20 de estas tablas siguen cabiendo en memoria
-- mientras un numero guardado en una fila se desincroniza sin que nadie lo note.
--
--
-- POR QUE `compras.ticket_id` ES UNIQUE
--
-- Es la garantia de que una compra no se cuenta dos veces, y es el unico punto
-- donde se puede garantizar. Sin el, `POST /inventario/compras` repetido, o el
-- escaner releyendo el mismo comprobante, generan dos compras del mismo papel y
-- el inventario sube el doble.
--
-- Reutiliza el `id` del ticket en vez de una FK a `scan_files`: el comprobante
-- ya esta en `tickets` con su `source_hash`, y `ix_tickets_source_hash` ya
-- garantiza que no haya dos tickets del mismo papel en la misma empresa.
--
--
-- POR QUE UNA COMPRA NO SUMA STOCK AL REGISTRARSE
--
-- `estado` arranca en PROCESAR y solo llega a PROCESADO con una persona de por
-- medio. El motivo esta medido y no es prudencia generica:
--
-- - La exactitud del OCR sobre fotos reales es de 33.3% (AGENTS.md, y los cuatro
--   caminos para mejorarla estan descartados con medicion en
--   docs/known-issues.md 21).
-- - Una linea de un comprobante es un tiro mas que el total. Con 15 lineas, que
--   las 15 esten bien no tiene la misma probabilidad que el total este bien: se
--   multiplican.
-- - La conciliacion bancaria valida el MONTO, nunca la COMPOSICION. Un ticket
--   puede dar PERFECT contra el banco y tener las lineas equivocadas.
-- - `confianza_por_campos` evalua los campos del encabezado. Nunca ha medido la
--   confianza de una linea, asi que AUTO_APROBADO no es una garantia sobre
--   `items`.
-- - Y la deriva es monotona: una cantidad de mas infla el stock para siempre, y
--   no hay entrada que lo revierta.
--
--
-- POR QUE `compra_items.producto_id` ADMITE NULL
--
-- Porque la linea se guarda SIEMPRE, coincida o no con un producto del catalogo.
-- Un producto sin `producto_id` es exactamente la cola de "esta descripcion no
-- la conozco": se ve con `WHERE producto_id IS NULL`, sin una tabla mas que se
-- pueda desincronizar de `compra_items`.
--
-- Y porque se guarda la `descripcion` tal cual la leyo el OCR. El caso real que
-- lo justifica: el proveedor salio como "Cadena Comercial) Uxxo,". Si las
-- lineas no se guardaran sin normalizar, esa basura se perderia y habria que
-- volver a escanear el papel para recuperarla.
--
-- Y NO se crea un producto automaticamente desde esa descripcion: con OCR al
-- 33%, un catalogo armado solo se llena de variantes del mismo producto
-- ("Reginen de", "Regin de") que el sistema contaria como tres.


-- --- La columna que ya se extraia y se tiraba ---------------------------

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS items JSONB;

COMMENT ON COLUMN tickets.items IS
    'Lineas del comprobante tal como las leyo el modelo (JSON). NULL = el '
    'lector no produjo lineas: la ruta OCR no las extrae, solo el LLM. No es '
    'lo mismo que "un comprobante sin lineas", que seria una lista vacia.';


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

    CONSTRAINT ck_compras_estado
        CHECK (estado IN ('PROCESAR', 'EN_REVISION', 'PROCESADO')),

    -- La regla que sostiene todo el diseno: PROCESADO exige firma y fecha, y
    -- cualquier otro estado no las tiene. Sin esto, el estado por si solo
    -- declara que el inventario se movio, y eso es exactamente lo que no se
    -- quiere: que se pueda marcar una compra como PROCESSED sin que nadie
    -- haya pasado.
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
    SELECT COALESCE(SUM(m.cantidad), 0) INTO actual
    FROM movimientos_inventario m
    WHERE m.producto_id = NEW.producto_id;

    IF NEW.tipo = 'ENTRADA' THEN
        actual := actual + NEW.cantidad;
    ELSE
        actual := actual - NEW.cantidad;
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