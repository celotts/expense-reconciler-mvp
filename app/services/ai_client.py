import httpx
from app.core.config import settings

class AIClient:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url="http://localhost:11434")

    async def extract_data(self, content: bytes) -> dict:
        response = await self.client.post(
            "/api/extract",
            json={
                "model": "qwen2.5-vl:7b",
                "temperature": 0.0,
                "format": "json",
                "content": content.decode("utf-8")
            }
        )
        response.raise_for_status()
        return response.json()
