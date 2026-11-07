"""Control de calidad de la foto ANTES de gastar OCR/Vision.

PLUS 2026-11-06 (rama perf/scan-vision-solo-si-ocr-falla): una foto borrosa,
oscura, con reflejo o con resolucion muy baja va a salir mal la lea quien la
lea, y mandarla a Vision cuesta 250-440 s por foto para devolver un total
inventado o un ticket a la cola sin motivo claro. Este modulo la detecta en
milisegundos, con la misma imagen ya preparada para OCR, y el escaneo la marca
`REVISAR_FOTO` con un mensaje que dice QUE corregir, en vez de gastar minutos
en ella.

REGLAS DE DISENO, Y POR QUE SON ASI
====================================

- Solo se marca lo CLARAMENTE malo. Los umbrales son conservadores a
  proposito: una foto buena nunca se bloquea por un borde. Si la metrica no
  es concluyente, se devuelve `None` y la foto sigue su camino normal. Un
  falso positivo aqui es peor que un falso negativo: bloquear una foto buena
  es perder un comprobante, y dejar pasar una mala solo cuesta el OCR que ya
  se pagaba.
- No se inventa nada. Si la imagen no se puede decodificar o numpy no esta,
  se devuelve `None`: sin evidencia no hay veredicto.
- No decodifica dos veces. Recibe la imagen PIL que `ocr.preparar_imagen` ya
  decodifico, recorto y paso a gris para Tesseract. La metrica corre sobre
  una copia reducida a 800 px de lado largo, asi que el coste es despreciable
  frente a una sola lectura de Tesseract.
- El motivo viaja en el resultado (`calidad_foto`) y en el listado
  `fotos_por_revisar` de la corrida, no solo en un log: quien escanea tiene
  que ver QUE foto retomar y POR QUE, sin abrir logs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Umbrales conservadores. Una foto de ticket normal y legible esta muy por
# encima de todos: media 120-200, varianza de laplaciano > 100, menos del 10%
# de pixeles saturados. Solo se marca lo que esta claramente fuera de rango.
_MIN_LADO_PX = 400  # un ticket legible a menos de 400 px de lado no existe
_MEDIA_OSCURA = 45.0  # media de gris por debajo de esto es casi negro
_VARIANZA_LAPLACIANO_BORROSA = 40.0  # nitidez: < 40 es desenfoque claro
_FRACCION_SATURADA_REFLEJO = 0.30  # >= 250 en mas del 30% del cuadro
_MEDIA_PARA_REFLEJO = 180.0  # ... y cuadro claro: brillo, no papel blanco
_FRACCION_OSCUROS_SIN_TEXTO = 0.003  # < 0.3% de tinta: no hay nada que leer

MENSAJES = {
    "baja_resolucion": (
        "foto con resolución muy baja, vuelve a tomarla más cerca del ticket"
    ),
    "oscura": "foto oscura, vuelve a tomarla con más luz",
    "borrosa": "foto borrosa, vuelve a tomarla con más luz y sin reflejo",
    "reflejo": (
        "foto con reflejo o brillo fuerte, vuelve a tomarla sin flash y sin reflejo"
    ),
    "sin_texto": (
        "no se ve texto en la foto, vuelve a tomarla más cerca del ticket"
    ),
}


def revisar_calidad_foto(imagen_pil) -> dict | None:
    """Juzga una foto ya preparada. `None` = pasa; `dict` = claramente mala.

    `imagen_pil` es la imagen en gris que `ocr.preparar_imagen` devuelve
    (recortada al papel, maximo 2000 px de ancho). `None` cuando no se puede
    juzgar: sin imagen no hay veredicto, y la foto sigue a OCR como antes.

    El `dict` trae `motivos` (claves, en orden de gravedad), `mensaje` (el del
    primer motivo, en lenguaje para el usuario) y `metricas` (los numeros, para
    que el umbral se pueda ajustar con datos y no con opiniones).
    """
    if imagen_pil is None:
        return None
    try:
        import numpy as np
        from PIL import Image as _PILImage
    except ImportError:
        return None
    try:
        ancho, alto = imagen_pil.size
        imagen = imagen_pil.convert("L")
        # Las metricas no necesitan 2000 px: a 800 px de lado largo el
        # desenfoque, la oscuridad y el reflejo se ven igual, y el coste es
        # ~10x menor. `thumbnail` no agranda: una foto pequena se juzga como es.
        if max(ancho, alto) > 800:
            imagen.thumbnail((800, 800), _PILImage.Resampling.LANCZOS)
        arr = np.asarray(imagen, dtype=np.float64)
        if arr.size == 0:
            return None
    except Exception as exc:  # imagen rota o sin pixeles: no se juzga
        logger.debug("calidad de foto no disponible (%s); se continua", exc)
        return None

    media = float(arr.mean())
    fraccion_oscuros = float((arr < 128).mean())
    fraccion_saturada = float((arr >= 250).mean())
    # Varianza del laplaciano: mide bordes, que es lo que el desenfoque mata.
    # Se calcula con numpy y no con OpenCV para no sumar una dependencia a un
    # chequeo de milisegundos: el kernel 3x3 con rebanadas es lo mismo.
    centro = arr[1:-1, 1:-1]
    laplaciano = (
        arr[:-2, 1:-1] + arr[2:, 1:-1] + arr[1:-1, :-2] + arr[1:-1, 2:] - 4 * centro
    )
    varianza_laplaciano = float(laplaciano.var()) if laplaciano.size else 0.0

    metricas = {
        "ancho_px": ancho,
        "alto_px": alto,
        "media_gris": round(media, 1),
        "fraccion_oscuros": round(fraccion_oscuros, 4),
        "fraccion_saturada": round(fraccion_saturada, 4),
        "varianza_laplaciano": round(varianza_laplaciano, 1),
    }

    motivos: list[str] = []
    if min(ancho, alto) < _MIN_LADO_PX:
        motivos.append("baja_resolucion")
    if fraccion_oscuros < _FRACCION_OSCUROS_SIN_TEXTO:
        # Sin tinta no hay texto que leer: es la foto del techo, el papel en
        # blanco o un archivo vacio. Se marca aqui y no en OCR porque el
        # mensaje dice que hacer (retomar la foto), y el de OCR solo diria
        # que no se leyo nada.
        motivos.append("sin_texto")
    elif media < _MEDIA_OSCURA:
        motivos.append("oscura")
    # El desenfoque se juzga solo cuando hay texto que desenfocar: en una
    # foto sin tinta la varianza es cero por definicion y ya se marco arriba.
    if "sin_texto" not in motivos and varianza_laplaciano < _VARIANZA_LAPLACIANO_BORROSA:
        motivos.append("borrosa")
    if fraccion_saturada > _FRACCION_SATURADA_REFLEJO and media > _MEDIA_PARA_REFLEJO:
        # El papel blanco de un ticket normal es claro pero no saturado: su
        # gris ronda 220-245. Mas del 30% a >= 250 con el cuadro claro es
        # brillo quemado (flash o reflejo), que borra la tinta.
        motivos.append("reflejo")

    if not motivos:
        return None
    return {
        "motivos": motivos,
        "mensaje": MENSAJES[motivos[0]],
        "metricas": metricas,
    }
