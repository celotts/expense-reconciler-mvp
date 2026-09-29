"""Vector search with ChromaDB + Meilisearch"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

import chromadb
from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)


class LocalVectorSearch:
    """ChromaDB for semantic search"""

    def __init__(
        self,
        persist_dir: str = "./data/chroma",
        collection_name: str = "expenses",
        embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    ):
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model
        )
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embedder,
            metadata={"hnsw:space": "cosine"}
        )
        logger.info(f"ChromaDB initialized at {persist_dir}, collection: {collection_name}")

    async def index_expense(self, expense: Dict[str, Any]) -> str:
        expense_id = str(expense.get("id", uuid.uuid4()))
        document = self._build_document(expense)
        metadata = self._build_metadata(expense)

        self.collection.upsert(
            documents=[document],
            metadatas=[metadata],
            ids=[expense_id]
        )
        return expense_id

    async def index_batch(self, expenses: List[Dict[str, Any]]) -> List[str]:
        ids = []
        documents = []
        metadatas = []

        for exp in expenses:
            eid = str(exp.get("id", uuid.uuid4()))
            ids.append(eid)
            documents.append(self._build_document(exp))
            metadatas.append(self._build_metadata(exp))

        self.collection.upsert(documents=documents, metadatas=metadatas, ids=ids)
        return ids

    def _build_document(self, expense: Dict[str, Any]) -> str:
        parts = [
            expense.get("merchant", ""),
            expense.get("merchant_normalized", ""),
            expense.get("category", ""),
            expense.get("subcategory", ""),
            str(expense.get("total_amount", "")),
            expense.get("payment_method", ""),
            expense.get("notes", ""),
        ]
        return " ".join(filter(None, parts))

    def _build_metadata(self, expense: Dict[str, Any]) -> Dict[str, Any]:
        meta = {
            "merchant": expense.get("merchant", "")[:500],
            "merchant_normalized": expense.get("merchant_normalized", "")[:500],
            "category": expense.get("category", ""),
            "subcategory": expense.get("subcategory", "")[:100] if expense.get("subcategory") else "",
            "total_amount": float(expense.get("total_amount", 0)),
            "currency": expense.get("currency", "MXN"),
            "payment_method": expense.get("payment_method", "")[:50] if expense.get("payment_method") else "",
            "expense_date": expense.get("expense_date", "").isoformat() if hasattr(expense.get("expense_date", ""), 'isoformat') else str(expense.get("expense_date", "")),
            "status": expense.get("status", "pending"),
            "confidence_score": float(expense.get("confidence_score", 0)),
            "gl_code": expense.get("gl_code", "")[:50] if expense.get("gl_code") else "",
            "source_image": expense.get("source_image_path", "")[:500] if expense.get("source_image_path") else "",
            "indexed_at": datetime.now().isoformat(),
        }
        return meta

    async def search(
        self,
        query: str,
        n_results: int = 10,
        category: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        where_clause = {}
        if category:
            where_clause["category"] = category
        if min_amount is not None or max_amount is not None:
            where_clause["total_amount"] = {}
            if min_amount is not None:
                where_clause["total_amount"]["$gte"] = min_amount
            if max_amount is not None:
                where_clause["total_amount"]["$lte"] = max_amount

        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where_clause if where_clause else None,
            include=["documents", "metadatas", "distances"]
        )

        hits = []
        for i, (doc, meta, dist) in enumerate(zip(
            results.get("documents", [[]])[0],
            results.get("metadatas", [[]])[0],
            results.get("distances", [[]])[0]
        )):
            hits.append({
                "id": results.get("ids", [[]])[0][i] if results.get("ids") else None,
                "document": doc,
                "metadata": meta,
                "score": 1 - dist,
            })
        return hits

    async def get_by_id(self, expense_id: str) -> Optional[Dict[str, Any]]:
        result = self.collection.get(ids=[expense_id], include=["documents", "metadatas"])
        if result.get("ids"):
            return {
                "id": result["ids"][0],
                "document": result["documents"][0],
                "metadata": result["metadatas"][0]
            }
        return None

    async def delete(self, expense_id: str) -> bool:
        try:
            self.collection.delete(ids=[expense_id])
            return True
        except Exception as e:
            logger.error(f"Failed to delete {expense_id}: {e}")
            return False

    async def get_stats(self) -> Dict[str, Any]:
        count = self.collection.count()
        return {"total_documents": count, "collection": self.collection_name}


class MeilisearchClient:
    """Meilisearch for full-text search"""

    def __init__(self, url: str = "http://localhost:7700", api_key: Optional[str] = None):
        self.url = url
        self.api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import meilisearch
                self._client = meilisearch.Client(self.url, self.api_key)
            except ImportError:
                logger.warning("meilisearch not installed")
                return None
        return self._client

    async def index_expense(self, expense: Dict[str, Any]) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            index = client.index("expenses")
            doc = {
                "id": str(expense.get("id")),
                "merchant": expense.get("merchant", ""),
                "merchant_normalized": expense.get("merchant_normalized", ""),
                "category": expense.get("category", ""),
                "total_amount": float(expense.get("total_amount", 0)),
                "expense_date": expense.get("expense_date", "").isoformat() if hasattr(expense.get("expense_date", ""), 'isoformat') else str(expense.get("expense_date", "")),
                "status": expense.get("status", "pending"),
                "source_image": expense.get("source_image_path", ""),
            }
            index.add_documents([doc])
            return True
        except Exception as e:
            logger.error(f"Meilisearch index failed: {e}")
            return False

    async def search(self, query: str, limit: int = 10, filter: Optional[str] = None) -> List[Dict[str, Any]]:
        client = self._get_client()
        if not client:
            return []
        try:
            index = client.index("expenses")
            result = index.search(query, {"limit": limit, "filter": filter})
            return result.get("hits", [])
        except Exception as e:
            logger.error(f"Meilisearch search failed: {e}")
            return []

    async def setup_index(self) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            index = client.index("expenses")
            index.update_searchable_attributes(["merchant", "merchant_normalized", "category", "notes"])
            index.update_filterable_attributes(["category", "status", "expense_date", "total_amount"])
            index.update_sortable_attributes(["expense_date", "total_amount"])
            return True
        except Exception as e:
            logger.error(f"Meilisearch setup failed: {e}")
            return False