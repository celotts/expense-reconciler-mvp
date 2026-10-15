"""Cuanto se va en la llamada al modelo de vision, y en que parte.

Mide UNA llamada y lee los tiempos que Ollama devuelve en la respuesta
(`load_duration`, `prompt_eval_duration`, `eval_duration`, `total_duration`),
que es lo que separa "el modelo tarda" de "la imagen cuesta procesarla".

    docker compose exec expense-api python scripts/medir_vision.py /tickets/IMG_4387.jpeg
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

import httpx  # noqa: E402

from app.core import archivo_real  # noqa: E402,F401
from app.services import ai_extractor as ae  # noqa: E402
from app.services import capture  # noqa: E402
from app.services.ai_extractor import VISION_EXTRACTION_PROMPT  # noqa: E402


async def main() -> None:
    ruta = Path(sys.argv[1])
    datos = ruta.read_bytes()
    s = capture.settings

    # La MISMA imagen que le llega al modelo, hecha por el mismo codigo.
    jpeg = ae._a_jpeg(datos)
    if jpeg is None:
        print("no se pudo convertir a JPEG")
        return
    print(f"archivo            {ruta.name}  ({len(datos) / 1024:.0f} KB)")
    print(f"imagen al modelo   {len(jpeg) / 1024:.0f} KB (base64: "
          f"{len(base64.b64encode(jpeg)) / 1024:.0f} KB)")
    print(f"modelo             {s.OLLAMA_VISION_MODEL}")
    print(f"num_predict        {s.AI_MAX_TOKENS}")
    print(f"base_url           {s.OLLAMA_BASE_URL}")
    print(f"keep_alive         {s.OLLAMA_KEEP_ALIVE}")
    print("-" * 72)

    payload = {
        "model": s.OLLAMA_VISION_MODEL,
        "prompt": VISION_EXTRACTION_PROMPT,
        "images": [base64.b64encode(jpeg).decode()],
        "stream": False,
        "keep_alive": s.OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": s.AI_TEMPERATURE,
            "num_predict": s.AI_MAX_TOKENS,
        },
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(s.OLLAMA_TIMEOUT, connect=10.0)) as c:
        t0 = time.perf_counter()
        r = await c.post(f"{s.OLLAMA_BASE_URL}/api/generate", json=payload)
        elapsed = time.perf_counter() - t0

    r.raise_for_status()
    d = r.json()
    ns = 1e9
    print(f"pared              {elapsed:7.1f}s")
    print(f"  load_duration      {d.get('load_duration', 0) / ns:7.1f}s  (cargar el modelo en RAM)")
    print(f"  prompt_eval_dur    {d.get('prompt_eval_duration', 0) / ns:7.1f}s  "
          f"({d.get('prompt_eval_count')} tokens = LA IMAGEN)")
    print(f"  eval_duration      {d.get('eval_duration', 0) / ns:7.1f}s  "
          f"({d.get('eval_count')} tokens generados)")
    print(f"  total_duration     {d.get('total_duration', 0) / ns:7.1f}s")
    print(f"  done_reason        {d.get('done_reason')}")
    print("-" * 72)
    pe = d.get("prompt_eval_duration", 0) / ns
    print(f"la imagen se come   {pe / elapsed * 100:.0f}% de la llamada")
    print(f"por 9 archivos:    ~{elapsed * 9 / 60:.1f} min a este ritmo")


if __name__ == "__main__":
    asyncio.run(main())