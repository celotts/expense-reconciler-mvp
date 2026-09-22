"""
AI Client Service - Unified interface for OpenAI / Azure OpenAI / Ollama / Local embeddings
"""
import os
import json
import asyncio
import httpx
from typing import List, Dict, Any, Optional, Literal
from dataclasses import dataclass
from enum import Enum

from openai import AsyncOpenAI, AsyncAzureOpenAI
from sentence_transformers import SentenceTransformer
import torch
from rapidfuzz import fuzz, process

from app.core.config import settings


class AIProvider(str, Enum):
    OPENAI = "openai"
    AZURE = "azure"
    OLLAMA = "ollama"
    LOCAL = "local"


@dataclass
class AIResponse:
    content: str
    tokens_used: int
    model: str
    provider: AIProvider


class AIClient:
    """Unified client for OpenAI, Azure OpenAI, and local models"""

    def __init__(self):
        self._openai_client: Optional[AsyncOpenAI] = None
        self._azure_client: Optional[AsyncAzureOpenAI] = None
        self._local_embedding_model: Optional[SentenceTransformer] = None
        self._provider = self._detect_provider()

    def _detect_provider(self) -> AIProvider:
        if settings.OLLAMA_ENABLED and settings.OLLAMA_BASE_URL:
            return AIProvider.OLLAMA
        elif settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_API_KEY:
            return AIProvider.AZURE
        elif settings.OPENAI_API_KEY:
            return AIProvider.OPENAI
        return AIProvider.LOCAL

    @property
    def openai_client(self) -> AsyncOpenAI:
        if self._openai_client is None:
            if not settings.OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not configured")
            self._openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        return self._openai_client

    @property
    def azure_client(self) -> AsyncAzureOpenAI:
        if self._azure_client is None:
            if not settings.AZURE_OPENAI_ENDPOINT or not settings.AZURE_OPENAI_API_KEY:
                raise ValueError("Azure OpenAI not configured")
            self._azure_client = AsyncAzureOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_API_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
        return self._azure_client

    @property
    def local_embedding_model(self) -> SentenceTransformer:
        if self._local_embedding_model is None:
            self._local_embedding_model = SentenceTransformer(
                settings.LOCAL_EMBEDDING_MODEL,
                device="cuda" if torch.cuda.is_available() else "cpu"
            )
        return self._local_embedding_model

    @property
    def ollama_client(self) -> httpx.AsyncClient:
        if not hasattr(self, '_ollama_client') or self._ollama_client is None:
            self._ollama_client = httpx.AsyncClient(
                base_url=settings.OLLAMA_BASE_URL,
                timeout=httpx.Timeout(120.0, connect=10.0)
            )
        return self._ollama_client

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        response_format: Optional[Dict] = None,
    ) -> AIResponse:
        """Chat completion using configured provider"""
        model = model or settings.OPENAI_MODEL

        if self._provider == AIProvider.AZURE:
            deployment = settings.AZURE_OPENAI_DEPLOYMENT or model
            response = await self.azure_client.chat.completions.create(
                model=deployment,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            return AIResponse(
                content=response.choices[0].message.content or "",
                tokens_used=response.usage.total_tokens if response.usage else 0,
                model=model,
                provider=AIProvider.AZURE,
            )

        elif self._provider == AIProvider.OPENAI:
            response = await self.openai_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            return AIResponse(
                content=response.choices[0].message.content or "",
                tokens_used=response.usage.total_tokens if response.usage else 0,
                model=model,
                provider=AIProvider.OPENAI,
            )

        elif self._provider == AIProvider.OLLAMA:
            return await self._ollama_chat_completion(
                messages, model, temperature, max_tokens, response_format
            )

        raise ValueError("No LLM provider configured (need OPENAI_API_KEY, Azure, or Ollama)")

    async def _ollama_chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str],
        temperature: float,
        max_tokens: int,
        response_format: Optional[Dict],
    ) -> AIResponse:
        """Chat completion using Ollama local LLM"""
        model = model or settings.OLLAMA_MODEL
        
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        
        if response_format and response_format.get("type") == "json_object":
            payload["format"] = "json"

        response = await self.ollama_client.post("/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        
        content = data.get("message", {}).get("content", "")
        
        return AIResponse(
            content=content,
            tokens_used=data.get("eval_count", 0) + data.get("prompt_eval_count", 0),
            model=model,
            provider=AIProvider.OLLAMA,
        )

    async def create_embedding(self, text: str) -> List[float]:
        """Create embedding using configured provider"""
        if self._provider == AIProvider.AZURE and settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT:
            response = await self.azure_client.embeddings.create(
                model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
                input=text,
            )
            return response.data[0].embedding

        elif self._provider == AIProvider.OPENAI:
            response = await self.openai_client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=text,
            )
            return response.data[0].embedding

        # Local fallback
        embedding = self.local_embedding_model.encode(text, convert_to_tensor=False)
        return embedding.tolist()

    async def create_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Create embeddings for multiple texts efficiently"""
        if self._provider == AIProvider.AZURE and settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT:
            response = await self.azure_client.embeddings.create(
                model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
                input=texts,
            )
            return [d.embedding for d in response.data]

        elif self._provider == AIProvider.OPENAI:
            response = await self.openai_client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=texts,
            )
            return [d.embedding for d in response.data]

        # Local fallback - batch encode
        embeddings = self.local_embedding_model.encode(texts, convert_to_tensor=False)
        return embeddings.tolist()


class VendorNormalizer:
    """Normalize vendor names using fuzzy matching"""

    def __init__(self):
        self.known_vendors: Dict[str, str] = {}  # normalized -> canonical
        self._load_common_vendors()

    def _load_common_vendors(self):
        """Load known vendor mappings"""
        self.known_vendors = {
            # Supermarkets
            "walmart": "WALMART",
            "wal-mart": "WALMART",
            "walmart supercenter": "WALMART",
            "walmart express": "WALMART",
            "soriana": "SORIANA",
            "soriana hiper": "SORIANA",
            "soriana express": "SORIANA",
            "chedraui": "CHEDRAUI",
            "chedraui select": "CHEDRAUI",
            "costco": "COSTCO",
            "costco wholesale": "COSTCO",
            "sams club": "SAMS CLUB",
            "sam's club": "SAMS CLUB",
            "bodega aurrera": "BODEGA AURRERA",
            "aurrera": "BODEGA AURRERA",

            # Gas stations
            "pemex": "PEMEX",
            "shell": "SHELL",
            "bp": "BP",
            "g500": "G500",
            "total": "TOTAL",
            "mobil": "MOBIL",

            # Pharmacy
            "farmacias del ahorro": "FARMACIAS DEL AHORRO",
            "farmacia del ahorro": "FARMACIAS DEL AHORRO",
            "benavides": "BENAVIDES",
            "farmacias benavides": "BENAVIDES",
            "farmacias similares": "FARMACIAS SIMILARES",
            "similares": "FARMACIAS SIMILARES",

            # Restaurants / Food
            "starbucks": "STARBUCKS",
            "mcdonalds": "MCDONALDS",
            "mcdonald's": "MCDONALDS",
            "burger king": "BURGER KING",
            "kfc": "KFC",
            "subway": "SUBWAY",
            "dominos": "DOMINOS",
            "domino's": "DOMINOS",

            # Retail / Online
            "amazon": "AMAZON",
            "amazon mx": "AMAZON",
            "mercadolibre": "MERCADOLIBRE",
            "mercado libre": "MERCADOLIBRE",
            "liverpool": "LIVERPOOL",
            "sears": "SEARS",
            "palacio de hierro": "PALACIO DE HIERRO",

            # Telecom / Utilities
            "telcel": "TELCEL",
            "movistar": "MOVISTAR",
            "att": "AT&T",
            "izzi": "IZZI",
            "totalplay": "TOTALPLAY",
            "cfe": "CFE",
            "agua": "AGUA",

            # Transport
            "uber": "UBER",
            "didi": "DIDI",
            "cabify": "CABIFY",
        }

    def normalize(self, vendor_name: str, threshold: int = 85) -> str:
        """Normalize vendor name to canonical form"""
        if not vendor_name:
            return "UNKNOWN"

        clean = vendor_name.lower().strip()

        # Direct match
        if clean in self.known_vendors:
            return self.known_vendors[clean]

        # Fuzzy match
        match = process.extractOne(
            clean,
            self.known_vendors.keys(),
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold,
        )

        if match:
            return self.known_vendors[match[0]]

        # Return cleaned original (title case)
        return vendor_name.strip().title()

    def add_mapping(self, variant: str, canonical: str):
        """Add new vendor mapping"""
        self.known_vendors[variant.lower().strip()] = canonical.upper()


# Global instances
ai_client = AIClient()
vendor_normalizer = VendorNormalizer()