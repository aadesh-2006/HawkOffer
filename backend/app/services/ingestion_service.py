"""Career site ingestion service coordinating scraping, normalization, and persistence."""

import logging
import time
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.job import Job
from app.repositories.company_repo import CompanyRepository
from app.repositories.job_repo import JobRepository
from app.repositories.log_repo import MonitoringLogRepository
from app.schemas.job import JobCreate, JobUpdate
from app.schemas.log import MonitoringLogCreate
from app.scrapers.base import RawJobPosting
from app.scrapers.exceptions import RateLimitError, ScraperError
from app.scrapers.registry import ScraperRegistry
from app.scrapers.registry import registry as default_registry
from app.services.job_normalizer import JobNormalizer, NormalizedJob

logger = logging.getLogger(__name__)


class IngestionResult(BaseModel):
    """Execution statistics returned after an ingestion run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    company_id: int
    company_name: str
    platform: str
    total_fetched: int = 0
    successfully_normalized: int = 0
    newly_inserted: int = 0
    already_existing: int = 0
    updated_records: int = 0
    failed_records: int = 0
    errors: list[str] = Field(default_factory=list)
    duration_ms: int = 0


class IngestionService:
    """Orchestrates fetching, normalizing, and storing jobs for a company."""

    def __init__(
        self,
        session: AsyncSession,
        registry: ScraperRegistry | None = None,
    ) -> None:
        self.session = session
        self.registry = registry or default_registry
        self.company_repo = CompanyRepository(session)
        self.job_repo = JobRepository(session)
        self.log_repo = MonitoringLogRepository(session)

    async def ingest_company(self, company_id: int) -> IngestionResult:
        """Fetch, normalize and persist job postings for a specific registered company."""
        company = await self.company_repo.get_by_id(company_id)
        if not company:
            raise ValueError(f"Company with ID {company_id} does not exist")

        start_time = time.monotonic()
        adapter = self.registry.get(company.platform)
        identifier = adapter.extract_identifier(company.career_url)

        logger.info(
            "Starting ingestion for company '%s' (ID %d) via platform '%s' (identifier: '%s')",
            company.name,
            company.id,
            company.platform,
            identifier,
        )

        try:
            raw_postings = await adapter.fetch_jobs(identifier)
        except RateLimitError as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.warning("Scraper rate limited for company '%s': %s", company.name, exc)
            await self.log_repo.create(
                MonitoringLogCreate(
                    company_id=company.id,
                    status="RATE_LIMITED",
                    jobs_found=0,
                    error_message=str(exc),
                    execution_time_ms=duration_ms,
                )
            )
            return IngestionResult(
                company_id=company.id,
                company_name=company.name,
                platform=company.platform,
                failed_records=1,
                errors=[str(exc)],
                duration_ms=duration_ms,
            )
        except ScraperError as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.error("Scraper failed for company '%s': %s", company.name, exc)
            await self.log_repo.create(
                MonitoringLogCreate(
                    company_id=company.id,
                    status="FAILED",
                    jobs_found=0,
                    error_message=str(exc),
                    execution_time_ms=duration_ms,
                )
            )
            return IngestionResult(
                company_id=company.id,
                company_name=company.name,
                platform=company.platform,
                failed_records=1,
                errors=[str(exc)],
                duration_ms=duration_ms,
            )

        result = await self.persist_postings(company, raw_postings, start_time)
        return result

    async def persist_postings(
        self,
        company: Company,
        raw_postings: list[RawJobPosting],
        start_time: float | None = None,
    ) -> IngestionResult:
        """Normalize and persist a collection of raw job postings."""
        start = start_time if start_time is not None else time.monotonic()
        now = datetime.now(UTC)

        result = IngestionResult(
            company_id=company.id,
            company_name=company.name,
            platform=company.platform,
            total_fetched=len(raw_postings),
        )

        for raw in raw_postings:
            try:
                # Per-record transaction isolation via nested savepoint
                async with self.session.begin_nested():
                    norm: NormalizedJob = JobNormalizer.normalize(raw, company_id=company.id)
                    result.successfully_normalized += 1

                    # 1. Matching order: Check (company_id, external_id) first if external_id exists
                    existing_job: Job | None = None
                    if norm.external_id:
                        existing_job = await self.job_repo.get_by_external_id(
                            company.id, norm.external_id
                        )

                    # 2. Fallback to fingerprint hash if external_id not found
                    if not existing_job:
                        existing_job = await self.job_repo.get_by_fingerprint(norm.fingerprint_hash)

                    # 3. Transition fallback: If still not found and norm.external_id is present,
                    # check if the job was previously ingested without an external_id
                    if not existing_job and norm.external_id:
                        legacy_fp = JobNormalizer.compute_fingerprint(
                            company_id=company.id,
                            title=norm.title,
                            location=norm.location,
                            clean_url=norm.application_url,
                            external_id=None,
                        )
                        candidate = await self.job_repo.get_by_fingerprint(legacy_fp)
                        if candidate and candidate.external_id is None:
                            existing_job = candidate

                    if existing_job:
                        # Check if content, metadata, or identity attributes were modified
                        has_content_changed = existing_job.content_hash != norm.content_hash
                        has_ext_id_changed = (
                            norm.external_id is not None
                            and existing_job.external_id != norm.external_id
                        )

                        if has_content_changed or has_ext_id_changed:
                            await self.job_repo.update(
                                existing_job.id,
                                JobUpdate(
                                    title=norm.title,
                                    location=norm.location,
                                    department=norm.department,
                                    external_id=norm.external_id,
                                    description=norm.description,
                                    application_url=norm.application_url,
                                    employment_type=norm.employment_type,
                                    job_type=norm.job_type,
                                    graduation_year=norm.graduation_year,
                                    posted_at=norm.posted_at,
                                    fingerprint_hash=norm.fingerprint_hash,
                                    content_hash=norm.content_hash,
                                    status="UPDATED",
                                    last_seen_at=now,
                                ),
                            )
                            result.updated_records += 1
                            logger.debug(
                                "Updated existing job %d: '%s'", existing_job.id, norm.title
                            )
                        else:
                            # Avoid redundant SELECT; update entity directly in session
                            existing_job.last_seen_at = now
                            self.session.add(existing_job)
                            await self.session.flush()
                            result.already_existing += 1
                    else:
                        # 3. New posting insertion
                        job_create = JobCreate(
                            company_id=company.id,
                            title=norm.title,
                            location=norm.location,
                            department=norm.department,
                            external_id=norm.external_id,
                            description=norm.description,
                            application_url=norm.application_url,
                            employment_type=norm.employment_type,
                            job_type=norm.job_type,
                            graduation_year=norm.graduation_year,
                            posted_at=norm.posted_at,
                            fingerprint_hash=norm.fingerprint_hash,
                            content_hash=norm.content_hash,
                            status="NEW",
                            first_seen_at=now,
                            last_seen_at=now,
                        )
                        await self.job_repo.create(job_create)
                        result.newly_inserted += 1
                        logger.debug(
                            "Inserted new job for company %d: '%s'", company.id, norm.title
                        )

            except Exception as exc:
                err_msg = f"Failed processing posting '{raw.title}' (ID {raw.external_id}): {exc}"
                logger.warning(err_msg)
                result.failed_records += 1
                result.errors.append(err_msg)

        result.duration_ms = int((time.monotonic() - start) * 1000)

        # Record audit log with accurate status
        if result.failed_records == 0:
            log_status = "SUCCESS"
        elif result.newly_inserted > 0 or result.already_existing > 0 or result.updated_records > 0:
            log_status = "PARTIAL"
        else:
            log_status = "FAILED"

        try:
            async with self.session.begin_nested():
                await self.log_repo.create(
                    MonitoringLogCreate(
                        company_id=company.id,
                        status=log_status,
                        jobs_found=result.total_fetched,
                        error_message="; ".join(result.errors[:5]) if result.errors else None,
                        execution_time_ms=result.duration_ms,
                    )
                )
        except Exception as log_err:
            logger.error(
                "Failed to persist monitoring log for company '%s': %s", company.name, log_err
            )

        logger.info(
            "Completed ingestion for '%s': fetched=%d, new=%d, existing=%d, "
            "updated=%d, failed=%d in %dms",
            company.name,
            result.total_fetched,
            result.newly_inserted,
            result.already_existing,
            result.updated_records,
            result.failed_records,
            result.duration_ms,
        )
        return result
