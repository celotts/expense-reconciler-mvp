-- =====================================================================
-- 0005 - El hash de contenido se compara dentro de una empresa
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- Misma razón que 0002 y 0003: `db/init.sql` solo corre con el volumen vacío.
-- Cambiar el índice en el modelo no cambia nada en una base que ya existe, y
-- la app levantaría con un índice distinto del que el código cree tener.
--
-- EL PROBLEMA
--
-- `ix_tickets_source_hash` era único sobre `source_hash` a secas. Eso
-- significa que el sistema주는 una sola columna a dos cosas distintas que
-- nunca fueron la misma:
--
--   1. "este archivo ya se ingesta, no lo dupliques"  -> correcto
--   2. "este archivo es este gasto"                    -> falso
--
-- El hash es el SHA-256 del archivo. No lleva empresa dentro. Y el mismo
-- comprobante puede existir legítimamente en dos empresas: es el mismo papel,
-- cada una lo gastó por su cuenta, y las dos tienen derecho a registrarlo.
--
-- Consecuencia en la aplicación, ver app/api/tickets.py `_persist_extracted`:
-- la consulta de idempotencia buscaba solo por hash, así que la segunda
-- empresa que subía el archivo se encontraba con el ticket de la primera y lo
-- devolvía tal cual. El gasto no se guardaba para ella, y de paso se le
-- mostraba un ticket ajeno. Con el índice global, el caso inverso era peor:
-- dos empresas con el mismo archivo, que es legítimo, no se podía guardar.
--
-- POR QUÉ ESTA MIGRACIÓN NUNCA FALLA CON DATOS EXISTENTES
--
-- El índice nuevo es MÁS débil que el viejo: solo exige unicidad dentro de
-- (company_id, source_hash), y el viejo exigía unicidad global. Todo conjunto
-- que era único antes lo es ahora también. Por eso no hace falta deduplicar
-- nada antes de crearlo, y por eso se puede aplicar sin limpiar la tabla.
--
-- COMO APLICARLA (volumen existente):
--
--     docker exec -i expense_pgvector psql -U postgres -d expense_db \
--         -v ON_ERROR_STOP=1 < db/migrations/0005_source_hash_por_empresa.sql
--
-- Es idempotente: se puede correr las veces que haga falta.
-- =====================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------------
-- 1. Índice nuevo antes de tirar el viejo
-- ---------------------------------------------------------------------
--
-- En este orden, y por una razón concreta: si primero se tirara el índice
-- viejo, la ventana entre los dos DROP/CREATE no tiene ninguna garantia de
-- unicidad, y un lote corriendo en ese momento metería un duplicado que
-- después el índice nuevo rechazaría con un error al cliente en vez de
-- devolverle el ticket que ya existía.
--
-- El índice viejo además ocupa el nombre, así que tiene que caer antes del
-- CREATE.

DROP INDEX IF EXISTS ix_tickets_source_hash;

CREATE UNIQUE INDEX IF NOT EXISTS ix_tickets_source_hash
    ON tickets (company_id, source_hash)
    WHERE source_hash IS NOT NULL;

COMMIT;

-- ---------------------------------------------------------------------
-- 2. Verificación
-- ---------------------------------------------------------------------
--
-- Las definiciones tienen que salir distintas. Si `indkey` sale como
-- `company_id, source_hash` está bien; si sale como `source_hash` solo, la
-- migración no llegó a aplicarse.
--
--   SELECT indexdef FROM pg_indexes
--    WHERE tablename = 'tickets' AND indexname = 'ix_tickets_source_hash';
--
-- Tiene que salir VACÍA. Cada fila es un archivo repetido DENTRO de la misma
-- empresa, que es exactamente lo que el índice prevents:
--
--   SELECT company_id, source_hash, count(*)
--     FROM tickets
--    WHERE source_hash IS NOT NULL
--    GROUP BY company_id, source_hash
--   HAVING count(*) > 1;
--
-- Y esto tiene que salir con una fila, aunque el volumen esté vacío. Es la
-- prueba de que la columna nueva se leyó bien y no por un nombre viejo:
--
--   INSERT INTO companies (name, tax_id) VALUES ('Prueba 0005', 'PRB0005' + '000');
--
--   INSERT INTO tickets (company_id, provider_name, total_amount, expense_date)
--   SELECT id, 'PRUEBA', 1.00, CURRENT_DATE FROM companies WHERE tax_id = 'PRB0005000';
--
--   UPDATE tickets SET source_hash = 'deadbeef' WHERE provider_name = 'PRUEBA';
--
-- Este INSERT debe FALLAR con violación de unicidad. Si no falla, el índice
-- viejo sigue puesto y la garantía no existe:
--
--   INSERT INTO tickets (company_id, provider_name, total_amount, expense_date, source_hash)
--   SELECT id, 'PRUEBA 2', 1.00, CURRENT_DATE, 'deadbeef' FROM companies WHERE tax_id = 'PRB0005000';
--
-- Y este SÍ debe funcionar, porque es el caso legítimo que el índice viejo
-- prohibía: el mismo comprobante en dos empresas distintas.
--
--   INSERT INTO tickets (company_id, provider_name, total_amount, expense_date, source_hash)
--   SELECT id, 'PRUEBA 2', 1.00, CURRENT_DATE, 'deadbeef'
--     FROM companies WHERE tax_id <> 'PRB0005000' LIMIT 1;
--
-- Limpieza:
--
--   DELETE FROM tickets WHERE provider_name LIKE 'PRUEBA%';
--   DELETE FROM companies WHERE tax_id LIKE 'PRB0005%';
