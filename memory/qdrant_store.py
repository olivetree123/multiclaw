from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from qdrant_client import QdrantClient, models

from .embeddings import HashEmbeddingProvider
from .models import History, Memory


@dataclass(frozen=True)
class MemorySearchResult:
    type: str
    db_id: str
    content: str
    score: float
    payload: dict[str, Any]


class QdrantMemoryStore:

    def __init__(
        self,
        *,
        url: str,
        collection_name: str,
        embedding_provider: HashEmbeddingProvider,
        api_key: str | None = None,
    ) -> None:
        self.collection_name = collection_name
        self.embedding_provider = embedding_provider
        self.client = QdrantClient(url=url)

    def ensure_collection(self) -> None:
        if self.client.collection_exists(self.collection_name):
            return

        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config={
                "dense":
                models.VectorParams(
                    size=self.embedding_provider.dimensions,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(index=models.SparseIndexParams(on_disk=False))
            },
        )

    def upsert_history(self, rows: list[History]) -> None:
        if not rows:
            return

        self._upsert_points([
            self._point(
                item_type="history",
                db_id=str(row.id),
                content=row.content,
                user_id=row.user_id,
                session_id=str(row.session_id),
                created_at=row.created_at,
                metadata={
                    "role": row.role,
                    "is_summarized": row.is_summarized,
                    "token_count": row.token_count,
                    **(row.extra_metadata or {}),
                },
            ) for row in rows
        ])

    def upsert_memory(self, rows: list[Memory]) -> None:
        if not rows:
            return

        self._upsert_points([
            self._point(
                item_type="summary",
                db_id=str(row.id),
                point_key=f"summary:{row.user_id}:{row.session_id}",
                content=row.summary,
                user_id=row.user_id,
                session_id=str(row.session_id),
                created_at=row.created_at,
                metadata={
                    "source_history_ids": row.source_history_ids,
                    **(row.extra_metadata or {}),
                },
            ) for row in rows
        ])

    def search(self, *, query: str, user_id: str, session_id: str,
               limit: int) -> list[MemorySearchResult]:
        dense = self.embedding_provider.dense(query)
        sparse_indices, sparse_values = self.embedding_provider.sparse(query)
        query_filter = models.Filter(must=[
            models.FieldCondition(
                key="user_id",
                match=models.MatchValue(value=user_id),
            ),
            models.FieldCondition(
                key="session_id",
                match=models.MatchValue(value=session_id),
            )
        ])

        if not sparse_indices:
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=dense,
                using="dense",
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            return [self._search_result(point) for point in response.points]

        try:
            response = self.client.query_points(
                collection_name=self.collection_name,
                prefetch=[
                    models.Prefetch(
                        query=models.SparseVector(indices=sparse_indices, values=sparse_values),
                        using="sparse",
                        filter=query_filter,
                        limit=limit * 2,
                    ),
                    models.Prefetch(
                        query=dense,
                        using="dense",
                        filter=query_filter,
                        limit=limit * 2,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
            points = response.points
        except Exception:
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=dense,
                using="dense",
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            points = response.points

        return [self._search_result(point) for point in points]

    def close(self) -> None:
        self.client.close()

    def _upsert_points(self, points: list[models.PointStruct]) -> None:
        self.client.upsert(collection_name=self.collection_name, points=points)

    def _point(
        self,
        *,
        item_type: str,
        db_id: str,
        point_key: str | None = None,
        content: str,
        user_id: str,
        session_id: str | None,
        created_at: datetime,
        metadata: dict[str, Any],
    ) -> models.PointStruct:
        sparse_indices, sparse_values = self.embedding_provider.sparse(content)
        payload = {
            "type": item_type,
            "db_id": db_id,
            "user_id": user_id,
            "session_id": session_id,
            "content": content,
            "created_at": created_at.isoformat(),
            "metadata": metadata,
        }

        return models.PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, point_key or f"{item_type}:{db_id}")),
            vector={
                "dense": self.embedding_provider.dense(content),
                "sparse": models.SparseVector(indices=sparse_indices, values=sparse_values),
            },
            payload=payload,
        )

    @staticmethod
    def _search_result(point: Any) -> MemorySearchResult:
        payload = point.payload or {}
        return MemorySearchResult(
            type=payload.get("type", ""),
            db_id=payload.get("db_id", ""),
            content=payload.get("content", ""),
            score=float(getattr(point, "score", 0.0) or 0.0),
            payload=payload,
        )
