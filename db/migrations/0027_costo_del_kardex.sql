-- 0027_costo_del_kardex.sql
--
-- La valoracion del inventario y el costo de ventas salen del kardex.
--
-- Hasta aqui `movimientos_inventario` sabia CUANTAS piezas movio, pero no a que
-- costo. `compra_items.costo_unitario` tenia el costo de la linea, y `confirmar_compra`
-- no lo copiaba al movimiento: para valorar el inventario habia que reconstruir
-- la compra desde el ticket. Eso no es una suma del kardex, es una opinion que
-- depende de que la compra siga existiendo.
--
-- La decision esta escrita en docs/known-issues.md §0.b: promedio ponderado
-- MOVIL. El costo se CONGELA en cada fila:
--
--   - ENTRADA de compra: compra_items.costo_unitario de la linea.
--   - SALIDA de venta: promedio ponderado de las entradas hasta ese instante.
--
-- Congelarlo en la SALIDA es lo que impide que una compra posterior reescriba
-- el costo de una venta ya registrada. El kardex es append-only; el costo es un
-- hecho del movimiento, no un campo calculado que se puede recalcular mañana.
--
-- NULL significa "no se conocia el costo", NO cero. Un cero inventado daria un
-- costo de ventas de $0.00 con apariencia de dato medido. Por eso la columna es
-- nullable y los agregados que la usan excluyen las filas sin costo y lo dicen
-- en la respuesta (`completo=false`).

ALTER TABLE movimientos_inventario
    ADD COLUMN IF NOT EXISTS costo_unitario NUMERIC(14, 4);

ALTER TABLE movimientos_inventario DROP CONSTRAINT IF EXISTS ck_movimientos_costo_unitario;
ALTER TABLE movimientos_inventario ADD CONSTRAINT ck_movimientos_costo_unitario
    CHECK (costo_unitario IS NULL OR costo_unitario >= 0);

-- La pregunta del reporte: salidas con costo en un periodo. La indice parcial
-- evita recorrer las entradas, que son las mas numerosas.
CREATE INDEX IF NOT EXISTS ix_movimientos_salidas_periodo
    ON movimientos_inventario (company_id, created_at)
    WHERE tipo = 'SALIDA' AND costo_unitario IS NOT NULL;

COMMENT ON COLUMN movimientos_inventario.costo_unitario IS
    'Costo unitario congelado al escribir el movimiento. NULL = no medido, nunca cero.';
