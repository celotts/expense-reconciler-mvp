-- 0014_import_bancario_idempotente.sql
--
-- QUE HACE ESTA MIGRACION
-- =======================
--
-- Le anade `bank_transactions.source_hash` con un indice unico parcial, para que
-- importar dos veces el mismo extracto del banco no duplique todos los movimientos.
--
--
-- MEDIDO, Y POR QUE ESTO ERA UN BUG Y NO UNA MOLESTIA
-- ===================================================
--
-- El 2026-10-10, probando el boton "Importar y Guardar" de
-- `front/src/pages/BankTransactions.tsx` (que hasta ese dia no podia funcionar:
-- mandaba un objeto donde va un `File` y el backend contestaba 422), se importo el
-- mismo CSV dos veces y quedaron 17 movimientos de los que 8 estaban duplicados:
--
--     x2  2025-01-31   15000.00  VENTAS MES
--     x2  2025-01-28    -450.00  PAGO LUZ CFE
--     x2  2025-01-25   -1200.00  NOMINA SEMANAL
--
-- El endpoint `POST /bank-transactions/import-csv-and-create` insertaba cada fila
-- sin mirar nada: no habia ninguna condicion, ni en Python ni en el DDL.
--
-- POR QUE NO ERA "SOLO UN DETALLE DE UI"
-- ======================================
--
-- Un extracto duplicado hace que la conciliacion **cuente doble**: el motor
-- empareja el mismo ticket contra dos movimientos identicos, y el reporte de
-- cierre suma el gasto dos veces. Y el error es invisible: los 17 movimientos se
-- venIguales en pantalla, no hay ningun aviso, y el numero equivocado aparece un
-- mes despues en un PDF firmado.
--
--
-- POR QUE UN `source_hash`, Y POR QUE ES EL MISMO QUE YA USA `tickets`
-- ==================================================================
--
-- Los tickets ya tienen exactamente esta defensa (AGENTS.md reglas 6 y 8) y por
-- eso escanear dos veces la misma carpeta no duplica gastos. El import bancario
-- se quedo sin ella porque se escribio en otro momento y nadie lo noto: es el
-- mismo forgetting, no un caso distinto.
--
-- El hash es de la FILA (fecha + importe + concepto + referencia), no del
-- archivo. Es a proposito:
--
--   - Un archivo del banco con el header distinto en cada descarga produce el
--     mismo hash por fila, asi que el header no rompe la idempotencia.
--   - Un extracto de dos meses tiene filas nuevas y no se salta enteras.
--
-- Y el indice es PARCIAL (`WHERE source_hash IS NOT NULL`) por la misma razon que
-- el de `tickets`: un movimiento creado a mano desde la pantalla no viene de un
-- archivo y no tiene de que欄pillar con otro. Si el indice fuera total, dos
-- movimientos manuales sin hash colisionarian en `NULL`.


-- ---------------------------------------------------------------------------
-- 1. LA COLUMNA
-- ---------------------------------------------------------------------------
ALTER TABLE bank_transactions ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64);


-- ---------------------------------------------------------------------------
-- 2. EL INDICE UNICO
-- ---------------------------------------------------------------------------
--
-- UNICO y POR EMPRESA, no solo por hash. El SHA-256 no lleva empresa dentro: la
-- misma compra pagada desde dos cuentas es legitima y no es un duplicado. Es la
-- misma razon por la que `tickets` es unico por `(company_id, source_hash)`.
--
-- Y el motor NO puede decidir esto solo con Python: una comprobacion previa en el
-- servicio deja pasar la segunda fila si dos importaciones corren a la vez. La
-- restriccion vive en la base porque es la unica que no se puede esquivar.
CREATE UNIQUE INDEX IF NOT EXISTS uq_bank_transactions_company_source_hash
    ON bank_transactions (company_id, source_hash)
    WHERE source_hash IS NOT NULL;


-- ---------------------------------------------------------------------------
-- 3. LO QUE HACE CON LAS FILAS QUE YA EXISTEN
-- ---------------------------------------------------------------------------
--
-- MEDIDO, y es un decision que hay que tomar explicita: **no se rellena la
-- columna retroactivamente**.
--
-- Hashear las filas que ya estan exigiria un INSERT/UPDATE, y una fila de
-- `bank_transactions` que ya esta conciliada tiene un `reconciliations` que la
-- apunta: backfillear el hash duplicaria el problema que se esta arreglando.
--
-- Lo que se hace es dejar las filas viejas con `NULL`, que es exactamente lo que
-- significa "no se de donde vino este movimiento". El indice parcial las ignora.
-- A partir de la migracion, toda importacion queda protegida.
--
-- Las duplicadas que YA estan en una baseInstalled (como las 8 del CSV de prueba)
-- NO se borran aqui: borrar un movimiento que ya tiene una conciliacion es una
-- operacion destructiva que tiene que decidir una persona, no una migracion.
