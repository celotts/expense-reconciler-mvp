-- 0012_el_impuesto_es_de_la_partida.sql
--
-- POR QUE ESTA MIGRACION EXISTE
-- ==============================
--
-- El gate comprobaba `subtotal + IVA == total`. Eso asume que un comprobante
-- tiene UNA tasa de impuesto, y en Mexico es falso.
--
-- Medido en los comprobantes reales de esta maquina:
--
--     Walmart, un solo ticket:
--       SUBTOTAL        217.27
--       IVA  16.0%        8.14
--       IEPS  8.0%        8.59     <- el sistema no tiene donde meterlo
--       TOTAL           234.00
--
--     217.27 + 8.14 = 225.41, y el total es 234.00. La diferencia SON los 8.59
--     del IEPS. Con `MONEY_TOLERANCE` de un centimo, este comprobante NUNCA
--     puede pasar el check, aunque los tres numeros se lean perfectos.
--
-- Y el problema de fondo es mas grande que el IEPS: el impuesto depende de la
-- PARTIDA, no del comprobante. En ese mismo Walmart:
--
--     BOLILLO    33.00  T   tasa 0
--     ACTII ESQ  21.00  C   tasa 0
--     ARTIELLIQ  35.00  A   tasa 16
--     ZOTE BARRA 24.00  A   tasa 16
--     PAKETAXO   30.00  C   tasa 0 + IEPS
--
-- Un solo comprobante con tres tratamientos fiscales. Un campo `ieps_amount` en
-- el ticket arregla el caso de una sola tasa y deja roto el mixto, que es el
-- caso normal de un supermercado. Por eso el impuesto se agrega a la LINEA, que
-- es donde vive, y el ticket lo deriva.
--
-- LO QUE ESTA MIGRACION NO HACE
-- ==============================
--
-- No hace el check permisivo. `confidence_gate` sigue exigiendo que los numeros
-- cuadren; lo que cambia es que puede distinguir "falta un impuesto que no tengo
-- en el modelo" de "leiste mal". Los dos mandan a revision, pero por motivos
-- distintos, y solo uno significa que la lectura no es de fiar.

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
-- es un IMPORTE y no una tasa. `iva_linea = 8.14` no significa 8.14%: son
-- 8.14 pesos de IVA sobre esa linea.
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