-- 0021_scan_files_status_revisar_foto.sql
-- ---------------------------------------------------------------------
-- El enum `ScanStatus` (`app/core/enums.py`) suma `REVISAR_FOTO`: las
-- fotos claramente malas (borrosas, oscuras, con reflejo) se marcan asi
-- SIN gastar OCR ni Vision, y el operador vuelve a tomarla. El DDL nunca
-- se entero: la constraint de la 0008 solo admite cinco estados, asi que
-- en una base YA creada, y en init.sql en una base NUEVA, un UPDATE a
-- status='REVISAR_FOTO' viola la constraint y aborta la transaccion —
-- la corrida entera del escaneo volvia en rollback silencioso.
-- ---------------------------------------------------------------------

ALTER TABLE scan_files
    DROP CONSTRAINT IF EXISTS ck_scan_files_status;

ALTER TABLE scan_files
    ADD CONSTRAINT ck_scan_files_status
        CHECK (status IN ('PENDIENTE', 'PROCESADO', 'DUPLICADO', 'ERROR',
                          'NO_SOPORTADO', 'REVISAR_FOTO'));
