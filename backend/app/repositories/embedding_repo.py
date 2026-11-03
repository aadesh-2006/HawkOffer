"""Repository for caching and querying JobEmbedding records."""

import logging

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.base import utc_now
from app.models.semantic import JobEmbedding
from app.services.semantic.embedding_service import BaseEmbeddingService

logger = logging.getLogger(__name__)


class EmbeddingRepository:
    """Async repository for cached job embeddings in SQLite."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_job_and_version(
        self,
        job_id: int,
        model_version: str,
    ) -> JobEmbedding | None:
        """Fetch cached embedding for a specific job and model version."""
        stmt = select(JobEmbedding).where(
            JobEmbedding.job_id == job_id,
            JobEmbedding.model_version == model_version,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def save_embedding(
        self,
        job_id: int,
        model_version: str,
        content_hash: str,
        embedding: list[float],
    ) -> JobEmbedding:
        """Upsert a cached embedding vector for a job."""
        now = utc_now()
        stmt = sqlite_insert(JobEmbedding).values(
            job_id=job_id,
            model_version=model_version,
            content_hash=content_hash,
            embedding=embedding,
            created_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["job_id", "model_version"],
            set_={
                "content_hash": content_hash,
                "embedding": embedding,
                "created_at": now,
            },
        )
        await self.session.execute(stmt)
        record = await self.get_by_job_and_version(job_id, model_version)
        if record is None:
            raise RuntimeError(f"Failed to upsert embedding for job_id={job_id}")
        await self.session.refresh(record)
        return record

    async def delete_by_job_id(self, job_id: int) -> int:
        """Delete all cached embeddings for a given job."""
        stmt = delete(JobEmbedding).where(JobEmbedding.job_id == job_id)
        res = await self.session.execute(stmt)
        return int(getattr(res, "rowcount", 0) or 0)

    async def get_or_compute_job_embedding(
        self,
        job_id: int,
        content_hash: str,
        job_text: str,
        embedding_service: BaseEmbeddingService,
    ) -> list[float]:
        """Fetch cached embedding if content_hash matches, otherwise compute and upsert."""
        model_version = embedding_service.model_version
        cached = await self.get_by_job_and_version(job_id, model_version)
        if cached is not None and cached.content_hash == content_hash:
            return cached.to_vector()

        # Compute fresh embedding and cache it
        vec = embedding_service.embed_text(job_text)
        await self.save_embedding(
            job_id=job_id,
            model_version=model_version,
            content_hash=content_hash,
            embedding=vec,
        )
        return vec
