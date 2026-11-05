"""El decodificador de HEIC, en un solo sitio.

## POR QUE ESTE ARCHIVO EXISTE

Hasta ahora `pillow_heif.register_heif_opener()` se llamaba **solo** en
`app/services/ai_extractor.py`. Es decir: el soporte de HEIC no era una propiedad
de quien decodifica imagenes, era un efecto secundario del orden en que se
importaban los modulos.

Medido: importando `app.services.ocr` sin `ai_extractor` primero, `_preparar` de
una foto HEIC falla con

    cannot identify image file <_io.BytesIO object at 0x...>

y devuelve los bytes originales. Despues `pytesseract` lanza

    TypeError: Unsupported image object

que no menciona HEIC ni los bytes: el operador ve un error de OCR y busca en el
OCR. Y no es hipotetico — paso de verdad, midiendo el recorte de papel sobre
`IMG_4316.HEIC` con un script propio.

La app hoy funciona porque tres modulos importan `ai_extractor` antes de que
corra el OCR (`scan_service.py`, `capture.py`, `app/api/tickets.py`). Eso no es
una garantia: es una coincidencia de orden de imports. El dia que un script, un
test o un refactor llegue al OCR sin pasar por ahi, las fotos de iPhone —que son
la mitad del producto— dejan de leerse.

## POR QUE NO SE ENGANCHA EN `archivo_real.py`

Porque ese modulo no importa Pillow, y no debe: `sniff_tipo` responde "de que
formato es esto" leyendo BYTES, sin decodificar. Un ticket HEIC se identifica
igual sin Pillow. Meter aqui el registro haria que un modulo que hoy funciona sin
dependencias dejara de funcionar sin ellas.

## POR QUE SIGUE SIENDO OPCIONAL

Porque `pillow-heif` no siempre esta. Si falta, `DISPONIBLE` queda en `False` y
todo lo demas sigue funcionando: HEIC simplemente no se abre, y eso es un
reporte honesto, no una excepcion. Un `try/except ImportError` al arrancar un
modulo que se usa en cada foto es lo que permite que la app pueda DECIR que no
hay soporte, en vez de reventar.
"""

# La excepcion original, para poder decir QUE fallo y no solo QUE fallo. El tipo
# se declara aqui y no en la rama del `except` por una razon de tipo, no de
# estilo: al declararlo ahi, el `None` del `else` de abajo es una asignacion
# invalida, porque el type checker ya sabe que la variable es un `str`. Y el
# sintoma de equivocarse es invisible: solo aparece si `pillow-heif` esta
# instalado, que es justo la maquina donde nadie lo prueba.
MOTIVO_EXC: str | None = None

try:  # pragma: no cover - depende del entorno
    import pillow_heif

    pillow_heif.register_heif_opener()
    DISPONIBLE = True
    MOTIVO = None
except ImportError as exc:  # pragma: no cover - depende del entorno
    DISPONIBLE = False
    MOTIVO = (
        "easyocr y pillow-heif no estan instalados. Agrega "
        "'pillow-heif>=0.18.0' a requirements.txt."
    )
    MOTIVO_EXC = str(exc)
else:
    MOTIVO_EXC = None
