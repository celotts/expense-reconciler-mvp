-- =====================================================================
-- 0011 - Productos que se crean solos, y como distinguirlos
-- =====================================================================
--
-- POR QUE EXISTE ESTE ARCHIVO
--
-- `0010` dejo las lineas de compra con `producto_id` NULL y una cola para que
-- una persona asignara cada una. Eso es correcto y es lo que se pidio al
-- principio, pero tiene un costo que con el tiempo se vuelve el problema entero:
-- una compra no se puede confirmar hasta que alguien haya asignado N productos,
-- y en un lote de 60 comprobantes eso son cientos de decisiones manuales antes
-- de que el inventario sirva de algo.
--
-- Esta migracion anade el camino automatico, y —esto es lo importante— la forma
-- de distinguir un producto que alguien dio de alta de uno que salio de leer un
-- papel a las 11 de la noche.
--
--
-- `origen`: QUIEN LO PUSO
--
-- 'MANUAL' lo creo una persona, 'OCR' salio de una linea de comprobante leida.
-- No es cosmetico: decide si el producto merece fe. Uno manual es una decision
-- tomada con el producto a la vista; uno de OCR es un texto que el sistema
-- creyo que era un producto.
--
--
-- `verificado`: SI ALGUIEN LO CONFIRMO
--
-- Es la columna que hace manejable el camino automatico. Un producto creado por
-- OCR nace con `verificado = false`, y eso no lo bloquea ni impide que entre al
-- inventario: entra, se mueve el stock, la compra se puede confirmar. Lo que
-- hace es que quede en una cola de REVISION, que es un problema distinto y con
-- otra solucion.
--
-- La diferencia entre las dos salidas, y por que importa:
--
--   - Sin crearlos: la compra NO se puede confirmar, y el inventario no refleja
--     lo que se compro. Un sistema que no refleja lo que pasa es peor que uno
--     que lo refleja con ruido.
--   - Creandolos sin marcar: el catalogo se llena de "Reginen de", "Regin de" y
--     "REGINA DE 12 PULG", el stock se reparte entre tres productos que son el
--     mismo, y en seis meses hay 500 filas y nadie sabe cuales fusionar.
--   - Creandolos marcados: el inventario funciona desde el primer dia, y hay una
--     lista ordenada de "estas 40 filas hay que revisarlas" que se puede atacar
--     con un criterio: las que mas veces aparecen son las que importan.
--
--
-- POR QUE NO SE USA CODIGO DE BARRAS PARA DECIDIR
--
-- El codigo es lo unico que el OCR no cambia. Cuando el papel lo trae, el
-- producto se crea con el y queda `verificado = true`, porque un codigo de
-- barras es una identidad y no una descripcion. Cuando no lo trae —muchos
-- comprobantes de autocomERCio no lo imprimen— se crea con la descripcion y
-- queda para revisar.
--
-- Esa es la razon de que `verificado` NO se ponga en false siempre, y de que
-- `origen` tenga dos valores y no uno.


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