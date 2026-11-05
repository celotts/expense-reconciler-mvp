-- 0013_compras_rechazables.sql
--
-- QUE HACE ESTA MIGRACION
-- =======================
--
-- 1. Le anade RECHAZADO a los estados de `compras`.
-- 2. Arregla el signo con el que el trigger de stock negativo cuenta.
-- 3. Anade `reconciliations.revisado_por` / `revisado_at`.
--
-- Las tres son el mismo motivo: cada una hace falta porque un endpoint que
-- todavia no existia pretendia escribir algo que la base no podia representar.
-- Ninguna es extra: son la parte de esquema de los endpoints de administracion
-- (productos, movimientos, compras, reconciliaciones y usuarios).


-- ---------------------------------------------------------------------------
-- 1. RECHAZADO: una compra que NO es una compra
-- ---------------------------------------------------------------------------
--
-- QUE POR QUE HACE FALTA, Y POR QUE NO BASTABA CON BORRARLA
-- ---------------------------------------------------------
--
-- `EstadoCompra` tenia PROCESAR, EN_REVISION y PROCESADO, y sus tres estados son
-- "esta compra va a contar". Rechazar un comprobante que no es una compra (una
-- nota sin detalle, un papel que se leyo mal y no es lo que decia) no cabia en
-- ninguno: lo unico que se podia hacer era dejarla en la cola para siempre, o
-- borrarla.
--
-- Y BORRARLA ESTA PEOR DE LO QUE PARECE. `compras.ticket_id` es UNIQUE, asi que
-- borrar la compra libera el ticket y el proximo reescaneo volveria a crearla
-- desde `registrar_compra`, con estado EN_REVISION y sin recordar que alguien
-- ya la habia mirado. Es un bucle: la compra descartada reaparece sola cada vez
-- que se toca el comprobante.
--
-- Con RECHAZADO el veredicto queda escrito y es idempotente: `registrar_compra`
-- la devuelve tal cual, y el reescaneo no la resucita.
--
-- POR QUE NO SE USA `ExtractionStatus.RECHAZADO`, QUE YA EXISTE
-- -------------------------------------------------------------
--
-- Porque son dos maquinas distintas. `ExtractionStatus` responde "como se leyo el
-- papel": un ticket puede estar RECHAZADO (el papel no servia) y aun asi su
-- compra estar EN_REVISION, porque el total si se leyo bien y lo que no sirvio
-- fueron las lineas. Confundirlas hace que "rechazado" deje de decir que paso.
-- Ver el docstring de `EstadoCompra` en app/core/enums.py.
--
-- `ck_compras_confirmacion` NO se toca, y por eso esto no la desactiva: la
-- constraint exige firma y fecha SOLO para PROCESADO, y prohibe ambas en los
-- demas. RECHAZADO es "los demas": sin `confirmada_por`, sin `confirmada_at`.
-- Rechazar no es confirmar al reves, y la constraint sigue diciendo que autorizo
-- stock nadie.

ALTER TABLE compras DROP CONSTRAINT IF EXISTS ck_compras_estado;
ALTER TABLE compras ADD CONSTRAINT ck_compras_estado
    CHECK (estado IN ('PROCESAR', 'EN_REVISION', 'PROCESADO', 'RECHAZADO'));


-- ---------------------------------------------------------------------------
-- 2. El trigger de stock negativo contaba con el signo equivocado
-- ---------------------------------------------------------------------------
--
-- EL BUG, MEDIDO
-- ---------------
--
-- `stock_de()` (app/services/inventario_service.py) suma `ENTRADA` y `AJUSTE` y
-- resta `SALIDA`. El trigger hacia:
--
--     IF NEW.tipo = 'ENTRADA' THEN actual := actual + NEW.cantidad;
--     ELSE                        actual := actual - NEW.cantidad;
--
-- o sea que `AJUSTE` RESTABA. Dos lecturas del signo en el mismo numero, y el
-- `SUM` previo del trigger tampoco aplicaba signo a las filas anteriores.
--
-- POR QUE NO SE NOTABA, Y POR QUE ESO NO LO HACE MENOS GRAVE
-- -----------------------------------------------------------
--
-- Porque la unica via que escribia en `movimientos_inventario` era
-- `confirmar_compra`, y esa solo produce `ENTRADA`. Con un solo tipo en juego, el
-- `ELSE` del trigger nunca se ejecutaba y el `SUM` sin signo daba el mismo
-- numero que el stock real.
--
-- Es el mismo patron que `AGENTS.md` documenta para otros casos: una defensa
-- puede estar en su sitio y no estar probada, y el motivo de que no se rompa es
-- que nadie ha llegado al camino. `POST /inventario/movimientos` es ese camino.
--
-- Consecuencia concreta si no se arregla: un AJUSTE de "se conto de mas" se
-- escribiria como SALIDA y el trigger lo contaria como suma, con lo que la
-- defensa de stock negativo dejaria de bloquear la salida que lo deja negativo.
-- Una defensa que se puede desactivar escribiendo en otro sitio no es una
-- defensa.
--
-- EL ARREGLO
-- -----------
--
-- El signo sale de una sola pregunta, `NEW.tipo = 'SALIDA'`, que es la MISMA que
-- usa `TipoMovimiento.suma_stock` en Python. Un `AJUSTE` nunca llega aqui con
-- tipo='AJUSTE' (se escribe como ENTRADA o SALIDA con `referencia_tipo='AJUSTE'`,
-- ver `inventario_service.registrar_ajuste`), asi que la funcion queda con dos
-- ramas y sin caso ambiguo.
--
-- Y el `SUM` de las filas previas ahora aplica el signo, que es lo que lo hacia
-- falso: sin esto, con ENTRADA 10 y SALIDA 4 el trigger creia que habia 14 y
-- dejaba pasar una SALIDA de 10 mas que el stock real no aguantaba.

CREATE OR REPLACE FUNCTION movimientos_no_dejar_negativo() RETURNS trigger AS $$
DECLARE
    actual NUMERIC(14, 3);
BEGIN
    -- El signo se aplica aqui y no en el `WHERE`. Antes era `SUM(cantidad)` a
    -- secas, que contaba una SALIDA como si sumara: con ENTRADA 10 y SALIDA 4
    -- decia 14 en vez de 6, y de ahi en adelante cada comprobacion estaba
    -- corrida por el mismo error.
    SELECT COALESCE(
        SUM(CASE WHEN m.tipo = 'SALIDA' THEN -m.cantidad ELSE m.cantidad END), 0
    ) INTO actual
    FROM movimientos_inventario m
    WHERE m.producto_id = NEW.producto_id;

    -- `SALIDA` y solo `SALIDA` resta. Antes era `IF tipo = 'ENTRADA' THEN suma
    -- ELSE resta`, que hacia que `AJUSTE` restara; `stock_de()` lo suma. Ver la
    -- cabecera de esta migracion.
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


-- ---------------------------------------------------------------------------
-- 3. Quien reviso una conciliacion a mano
-- ---------------------------------------------------------------------------
--
-- `PATCH /reconciliations/{id}` deja que una persona cambie el `match_status` que
-- puso el motor. Sin esto, un `PERFECT` que decidio el motor y uno que aprobo
-- una persona son la misma fila, y no hay forma de saber cual es cual.
--
-- Eso es exactamente el salto que este repo no puede hacer. `confidence_source`
-- existe porque "el modelo lo leyo" y "lo leyo una persona" no se suman; igual
-- aqui: `revisado_por` es el `_source` de la conciliacion.
--
-- NULL y no cadena vacia: `NULL` es "el motor lo decidio y nadie lo ha tocado",
-- que es un hecho. `''` seria "alguien lo toco y no lo dijo".
--
-- Texto y no llave foranea, por el mismo precedente de `compras.confirmada_por`,
-- `tickets.spot_checked_by` y `cierres_periodo.cerrado_por`: la decision tiene
-- que sobrevivir a la baja de la cuenta que la tomo.
ALTER TABLE reconciliations
    ADD COLUMN IF NOT EXISTS revisado_por VARCHAR(255);
ALTER TABLE reconciliations
    ADD COLUMN IF NOT EXISTS revisado_at TIMESTAMP WITH TIME ZONE;

-- Una revision sin fecha es una decision sin momento, que es cuando empieza a
-- ser imposible de ordenar. Y una fecha sin autor no dice quien.
ALTER TABLE reconciliations DROP CONSTRAINT IF EXISTS ck_reconciliations_revision;
ALTER TABLE reconciliations ADD CONSTRAINT ck_reconciliations_revision
    CHECK (
        (revisado_por IS NULL AND revisado_at IS NULL)
        OR (revisado_por IS NOT NULL AND length(trim(revisado_por)) > 0
            AND revisado_at IS NOT NULL)
    );

-- `match_status` es lo que decide si la fila se exporta (`MATCHED_STATUSES` en
-- export_service). Si una persona lo cambia a mano, la fila que sale a CONTPAQI ya
-- no es la que el motor produzco, y sin esto no habia ni una columna que lo
-- dijera. El indice es por la consulta "que reviso una persona", que es la que
-- se hace cuando se audita un cierre.
CREATE INDEX IF NOT EXISTS ix_reconciliations_revisadas
    ON reconciliations (revisado_at DESC)
    WHERE revisado_por IS NOT NULL;
