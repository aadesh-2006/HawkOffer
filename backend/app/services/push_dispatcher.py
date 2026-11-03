"""Asynchronous Web Push dispatcher service consuming pending notifications."""

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.vapid import get_vapid_claims, get_vapid_private_key
from app.models.company import Company
from app.models.job import Job
from app.models.notification import Notification
from app.models.push import NotificationPushDelivery, PushSubscription
from app.schemas.notification import DeliveryStatus

logger = logging.getLogger(__name__)


class WebPushDispatchError(Exception):
    """Exception raised when pywebpush delivery fails."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Web Push delivery error (HTTP {status_code}): {message}")
        self.status_code = status_code
        self.message = message


@dataclass
class PushDispatchResult:
    """Execution telemetry for a Web Push dispatch cycle."""

    status: str = "completed"
    notifications_claimed: int = 0
    attempted_pushes: int = 0
    successful_pushes: int = 0
    transient_failures: int = 0
    pruned_subscriptions: int = 0
    completed_notifications: int = 0
    retry_notifications: int = 0
    failed_notifications: int = 0
    duration_ms: int = 0


class WebPushDispatcher:
    """Dispatches pending in-app notifications to registered browser Web Push endpoints.

    Invariants and guarantees:
    - In-app notification records are NEVER deleted or altered if a push fails.
    - Single-owner delivery lease prevents duplicate pushes across multiple worker processes.
    - Device-level tracking via NotificationPushDelivery ensures already-delivered devices
      do not receive duplicate pushes during retries.
    - HTTP 410 Gone / 404 Not Found subscriptions are automatically pruned.
    - Transient provider errors trigger bounded exponential backoff.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        batch_size: int | None = None,
        lease_ttl_seconds: int | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.batch_size = batch_size or settings.push_dispatch_batch_size
        self.lease_ttl_seconds = lease_ttl_seconds or settings.push_lease_ttl_seconds
        self.max_attempts = max_attempts or settings.push_max_attempts

    def _send_webpush(
        self,
        subscription_info: dict[str, Any],
        data: str,
        vapid_private_key: str,
        vapid_claims: dict[str, str],
    ) -> int:
        """Synchronously execute RFC 8291 / 8292 Web Push call in a worker thread."""
        try:
            from pywebpush import WebPushException, webpush

            response = webpush(
                subscription_info=subscription_info,
                data=data,
                vapid_private_key=vapid_private_key,
                vapid_claims=vapid_claims,
                timeout=10,
            )
            return int(getattr(response, "status_code", 201))
        except WebPushException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", 500)
            logger.debug("pywebpush WebPushException HTTP %s: %s", status_code, exc)
            raise WebPushDispatchError(status_code=status_code, message=str(exc)) from exc
        except Exception as exc:
            logger.debug("pywebpush generic exception: %s", exc)
            raise WebPushDispatchError(status_code=500, message=str(exc)) from exc

    async def _claim_pending_notifications(
        self, session: AsyncSession
    ) -> tuple[list[int], str]:
        """Atomically claim a batch of pending/retry notifications with a unique lease token."""
        now = datetime.now(UTC)
        token = str(uuid.uuid4())
        lease_expires = now + timedelta(seconds=self.lease_ttl_seconds)

        stmt = (
            select(Notification.id)
            .where(
                Notification.user_id.is_not(None),
                Notification.delivery_status.in_(
                    [DeliveryStatus.PENDING, DeliveryStatus.RETRY]
                ),
                (Notification.next_delivery_attempt_at.is_(None))
                | (Notification.next_delivery_attempt_at <= now),
                (Notification.delivery_lease_expires_at.is_(None))
                | (Notification.delivery_lease_expires_at <= now),
            )
            .order_by(Notification.created_at.asc(), Notification.id.asc())
            .limit(self.batch_size)
        )
        res = await session.execute(stmt)
        claimed_ids = list(res.scalars().all())

        if claimed_ids:
            claim_update = (
                update(Notification)
                .where(Notification.id.in_(claimed_ids))
                .values(
                    delivery_status=DeliveryStatus.PROCESSING,
                    delivery_lease_token=token,
                    delivery_lease_expires_at=lease_expires,
                )
            )
            await session.execute(claim_update)
            await session.commit()
            logger.info("Claimed %d notifications for Web Push dispatch", len(claimed_ids))
        else:
            await session.rollback()

        return claimed_ids, token

    async def run_once(self) -> PushDispatchResult:
        """Execute one complete batch dispatch cycle."""
        start_perf = time.perf_counter()
        result = PushDispatchResult()

        async with self.session_factory() as claim_session:
            claimed_ids, token = await self._claim_pending_notifications(claim_session)

        result.notifications_claimed = len(claimed_ids)
        if not claimed_ids:
            result.duration_ms = int((time.perf_counter() - start_perf) * 1000)
            return result

        # Resolve server VAPID keys once for the entire batch
        try:
            vapid_priv = get_vapid_private_key()
            vapid_claims = get_vapid_claims()
        except Exception as exc:
            logger.error("VAPID resolution failed; marking batch for retry: %s", exc)
            async with self.session_factory() as rollback_session:
                await rollback_session.execute(
                    update(Notification)
                    .where(Notification.id.in_(claimed_ids))
                    .values(
                        delivery_status=DeliveryStatus.RETRY,
                        delivery_lease_token=None,
                        delivery_lease_expires_at=None,
                        last_delivery_error="VAPID key configuration error",
                    )
                )
                await rollback_session.commit()
            result.status = "failed"
            result.duration_ms = int((time.perf_counter() - start_perf) * 1000)
            return result

        for notif_id in claimed_ids:
            await self._process_notification(
                notif_id=notif_id,
                token=token,
                vapid_priv=vapid_priv,
                vapid_claims=vapid_claims,
                result=result,
            )

        result.duration_ms = int((time.perf_counter() - start_perf) * 1000)
        return result

    async def _process_notification(
        self,
        notif_id: int,
        token: str,
        vapid_priv: str,
        vapid_claims: dict[str, str],
        result: PushDispatchResult,
    ) -> None:
        """Process a single claimed notification, dispatching pushes to its user's devices."""
        now = datetime.now(UTC)

        async with self.session_factory() as session:
            # 1. Fetch notification and verify lease token ownership
            stmt = select(Notification).where(Notification.id == notif_id)
            notif = (await session.execute(stmt)).scalar_one_or_none()
            if not notif or notif.delivery_lease_token != token:
                logger.warning(
                    "Skipping notification %d: lease token mismatch or missing", notif_id
                )
                return

            # 2. Fetch user's active push subscriptions
            sub_stmt = (
                select(PushSubscription)
                .where(PushSubscription.user_id == notif.user_id)
                .order_by(PushSubscription.id.asc())
            )
            subs = list((await session.execute(sub_stmt)).scalars().all())

            # 3. If user has zero push subscriptions, complete cleanly as NOT_REQUIRED
            if not subs:
                notif.delivery_status = DeliveryStatus.NOT_REQUIRED
                notif.delivery_lease_token = None
                notif.delivery_lease_expires_at = None
                await session.commit()
                result.completed_notifications += 1
                return

            # 4. Filter out subscriptions that already received this notification (idempotency)
            delivered_stmt = select(NotificationPushDelivery.subscription_id).where(
                NotificationPushDelivery.notification_id == notif.id,
                NotificationPushDelivery.status == "DELIVERED",
            )
            delivered_ids = set((await session.execute(delivered_stmt)).scalars().all())
            pending_subs = [s for s in subs if s.id not in delivered_ids]

            # If all devices were already delivered, mark completed
            if not pending_subs:
                notif.delivery_status = DeliveryStatus.SENT
                notif.delivered_at = notif.delivered_at or now
                notif.delivery_lease_token = None
                notif.delivery_lease_expires_at = None
                await session.commit()
                result.completed_notifications += 1
                return

            # 5. Load Job & Company for notification payload
            job_stmt = (
                select(Job, Company.name.label("company_name"))
                .join(Company, Company.id == Job.company_id)
                .where(Job.id == notif.job_id)
            )
            job_res = (await session.execute(job_stmt)).first()
            if job_res:
                job, company_name = job_res
                job_title = job.title
                job_loc = job.location or "Remote"
                app_url = job.application_url or ""
            else:
                job_title = "Matching Career Opportunity"
                company_name = "Monitored Company"
                job_loc = "Remote"
                app_url = ""

            payload_data = json.dumps(
                {
                    "title": f"New Job: {job_title}",
                    "body": f"{company_name} • {job_loc}",
                    "url": f"/jobs/{notif.job_id}",
                    "tag": f"job-{notif.job_id}",
                    "data": {
                        "notification_id": notif.id,
                        "job_id": notif.job_id,
                        "title": job_title,
                        "company_name": company_name,
                        "application_url": app_url,
                    },
                }
            )

            # Pre-load existing delivery records for this notification for safe upsert
            existing_del_stmt = select(NotificationPushDelivery).where(
                NotificationPushDelivery.notification_id == notif.id
            )
            existing_deliveries = {
                d.subscription_id: d
                for d in (await session.execute(existing_del_stmt)).scalars().all()
            }

            # 6. Dispatch to each pending subscription
            device_failures = 0
            dead_subscription_ids: list[int] = []

            for sub in pending_subs:
                result.attempted_pushes += 1
                sub_info = {
                    "endpoint": sub.endpoint,
                    "keys": {
                        "p256dh": sub.p256dh,
                        "auth": sub.auth,
                    },
                }

                try:
                    await asyncio.to_thread(
                        self._send_webpush,
                        sub_info,
                        payload_data,
                        vapid_priv,
                        vapid_claims,
                    )
                    # Push succeeded on this device
                    result.successful_pushes += 1
                    if sub.id in existing_deliveries:
                        dev_rec = existing_deliveries[sub.id]
                        dev_rec.status = "DELIVERED"
                        dev_rec.attempt_count += 1
                        dev_rec.delivered_at = now
                        dev_rec.error_message = None
                        dev_rec.updated_at = now
                    else:
                        dev_rec = NotificationPushDelivery(
                            notification_id=notif.id,
                            subscription_id=sub.id,
                            status="DELIVERED",
                            attempt_count=1,
                            delivered_at=now,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(dev_rec)
                        existing_deliveries[sub.id] = dev_rec
                    sub.last_used_at = now
                    sub.failure_count = 0

                except WebPushDispatchError as exc:
                    if exc.status_code in (404, 410):
                        # Subscription permanently dead / unregistered by user
                        logger.info(
                            "Push endpoint %s returned HTTP %d; pruning subscription %d",
                            sub.endpoint[:40],
                            exc.status_code,
                            sub.id,
                        )
                        result.pruned_subscriptions += 1
                        dead_subscription_ids.append(sub.id)
                    else:
                        # Transient failure (HTTP 429, 500, 502, 503, timeout)
                        logger.warning(
                            "Transient Web Push failure on sub %d (HTTP %d): %s",
                            sub.id,
                            exc.status_code,
                            exc.message[:100],
                        )
                        result.transient_failures += 1
                        device_failures += 1
                        sub.failure_count += 1
                        if sub.id in existing_deliveries:
                            dev_rec = existing_deliveries[sub.id]
                            dev_rec.status = "FAILED"
                            dev_rec.attempt_count += 1
                            dev_rec.error_message = exc.message[:255]
                            dev_rec.updated_at = now
                        else:
                            dev_rec = NotificationPushDelivery(
                                notification_id=notif.id,
                                subscription_id=sub.id,
                                status="FAILED",
                                attempt_count=1,
                                error_message=exc.message[:255],
                                created_at=now,
                                updated_at=now,
                            )
                            session.add(dev_rec)
                            existing_deliveries[sub.id] = dev_rec

            # 7. Prune dead subscriptions from database
            if dead_subscription_ids:
                await session.execute(
                    delete(PushSubscription).where(
                        PushSubscription.id.in_(dead_subscription_ids)
                    )
                )

            # 8. Update notification overall delivery status
            if device_failures == 0:
                if dead_subscription_ids and not any(
                    sub.id not in dead_subscription_ids for sub in pending_subs
                ):
                    # All subscriptions were pruned as dead/invalid
                    notif.delivery_status = DeliveryStatus.NOT_REQUIRED
                    notif.last_delivery_error = "All push subscriptions expired or invalid"
                else:
                    # All active devices succeeded!
                    notif.delivery_status = DeliveryStatus.SENT
                    notif.delivered_at = now
                    notif.last_delivery_error = None
                result.completed_notifications += 1
            else:
                # One or more devices failed transiently; schedule retry
                notif.delivery_attempts += 1
                if notif.delivery_attempts >= self.max_attempts:
                    notif.delivery_status = DeliveryStatus.FAILED
                    notif.last_delivery_error = (
                        f"Max delivery attempts ({self.max_attempts}) exhausted"
                    )
                    result.failed_notifications += 1
                else:
                    notif.delivery_status = DeliveryStatus.RETRY
                    base_delay = 10 * (2**notif.delivery_attempts)
                    backoff = min(300, int(base_delay + random.uniform(1, 5)))
                    notif.next_delivery_attempt_at = now + timedelta(seconds=backoff)
                    result.retry_notifications += 1

            # Always release the claim lease
            notif.delivery_lease_token = None
            notif.delivery_lease_expires_at = None
            await session.commit()
