"""Distributed lease service for zero-cost cross-process coordination."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.lease import DistributedLease

logger = logging.getLogger(__name__)


class DistributedLeaseService:
    """Provides atomic cross-process mutual exclusion via database-backed leases.

    Compatible with both SQLite WAL and PostgreSQL. Guarantees:
    - Single active owner per lease name across arbitrary processes/containers.
    - Atomic acquisition via single-statement updates or unique primary keys.
    - Automatic crash recovery through expiration TTL.
    - Safe releases: former/expired owners cannot release a newer owner's lease.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def acquire_lease(
        self,
        name: str,
        ttl_seconds: int = 300,
        owner_token: str | None = None,
    ) -> str | None:
        """Atomically acquire or recover an expired distributed lease.

        Args:
            name: Unique name of the distributed lease (e.g. 'centralized_monitoring').
            ttl_seconds: Lease duration in seconds before automatic expiration.
            owner_token: Optional specific owner token; generated as UUID4 if omitted.

        Returns:
            The owner token string if acquired successfully, or None if the lease is
            currently owned and unexpired.
        """
        token = owner_token or str(uuid.uuid4())
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)

        async with self.session_factory() as session:
            try:
                # 1. Attempt atomic update on an already released or expired lease
                stmt = (
                    update(DistributedLease)
                    .where(
                        DistributedLease.name == name,
                        (DistributedLease.released_at.is_not(None))
                        | (DistributedLease.expires_at <= now),
                    )
                    .values(
                        owner_token=token,
                        acquired_at=now,
                        expires_at=expires_at,
                        released_at=None,
                    )
                )
                result = await session.execute(stmt)
                rowcount = result.rowcount if isinstance(result, CursorResult) else 0
                if rowcount > 0:
                    await session.commit()
                    logger.debug(
                        "Acquired existing distributed lease '%s' (token: %s, ttl: %ds)",
                        name,
                        token[:8],
                        ttl_seconds,
                    )
                    return token

                # 2. If rowcount is 0, check if row exists
                existing = await session.scalar(
                    select(DistributedLease.name).where(DistributedLease.name == name)
                )
                if not existing:
                    try:
                        new_lease = DistributedLease(
                            name=name,
                            owner_token=token,
                            acquired_at=now,
                            expires_at=expires_at,
                            released_at=None,
                        )
                        session.add(new_lease)
                        await session.commit()
                        logger.debug(
                            "Initialized and acquired distributed lease '%s' (token: %s, ttl: %ds)",
                            name,
                            token[:8],
                            ttl_seconds,
                        )
                        return token
                    except IntegrityError:
                        # Another process created the row concurrently
                        await session.rollback()
                        logger.debug(
                            "Concurrent initialization conflict for lease '%s'; acquisition failed",
                            name,
                        )
                        return None

                # 3. Row exists and is currently active and unexpired
                await session.rollback()
                logger.debug(
                    "Distributed lease '%s' currently held by active owner; acquisition declined",
                    name,
                )
                return None

            except Exception as exc:
                await session.rollback()
                logger.exception("Unexpected error acquiring distributed lease '%s': %s", name, exc)
                return None

    async def release_lease(
        self,
        name: str,
        owner_token: str,
    ) -> bool:
        """Release a distributed lease if and only if owned by the provided token.

        Prevents expired or stale former owners from releasing a lease that has
        already been taken over by another process.

        Args:
            name: Unique name of the distributed lease.
            owner_token: Token identifying the current owner.

        Returns:
            True if the lease was actively owned and released; False otherwise.
        """
        now = datetime.now(UTC)

        async with self.session_factory() as session:
            try:
                stmt = (
                    update(DistributedLease)
                    .where(
                        DistributedLease.name == name,
                        DistributedLease.owner_token == owner_token,
                        DistributedLease.released_at.is_(None),
                    )
                    .values(released_at=now)
                )
                result = await session.execute(stmt)
                await session.commit()
                rowcount = result.rowcount if isinstance(result, CursorResult) else 0
                released = rowcount > 0
                if released:
                    logger.debug(
                        "Successfully released distributed lease '%s' for token %s",
                        name,
                        owner_token[:8],
                    )
                else:
                    logger.warning(
                        "Failed to release distributed lease '%s': token mismatch or expired",
                        name,
                    )
                return bool(released)
            except Exception as exc:
                await session.rollback()
                logger.exception("Error releasing distributed lease '%s': %s", name, exc)
                return False

    async def renew_lease(
        self,
        name: str,
        owner_token: str,
        ttl_seconds: int = 300,
    ) -> bool:
        """Extend the expiration time of an active lease for the current owner.

        Returns False if the lease expired or ownership was lost.
        """
        now = datetime.now(UTC)
        new_expiry = now + timedelta(seconds=ttl_seconds)

        async with self.session_factory() as session:
            try:
                stmt = (
                    update(DistributedLease)
                    .where(
                        DistributedLease.name == name,
                        DistributedLease.owner_token == owner_token,
                        DistributedLease.released_at.is_(None),
                        DistributedLease.expires_at > now,
                    )
                    .values(expires_at=new_expiry)
                )
                result = await session.execute(stmt)
                await session.commit()
                rowcount = result.rowcount if isinstance(result, CursorResult) else 0
                return bool(rowcount > 0)
            except Exception as exc:
                await session.rollback()
                logger.exception("Error renewing distributed lease '%s': %s", name, exc)
                return False

    async def get_lease(self, name: str) -> DistributedLease | None:
        """Fetch the current state of a distributed lease for monitoring/telemetry."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(DistributedLease).where(DistributedLease.name == name)
            )
            return result.scalar_one_or_none()
