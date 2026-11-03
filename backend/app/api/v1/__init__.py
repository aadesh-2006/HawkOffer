"""API v1 Router aggregation."""

from fastapi import APIRouter

from app.api.v1.applications import router as applications_router
from app.api.v1.auth import router as auth_router
from app.api.v1.companies import router as companies_router
from app.api.v1.internal import router as internal_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.monitoring import router as monitoring_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.profiles import router as profiles_router
from app.api.v1.push import router as push_router
from app.api.v1.subscriptions import router as subscriptions_router
from app.api.v1.system import router as system_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(system_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(companies_router)
api_v1_router.include_router(profiles_router)
api_v1_router.include_router(jobs_router)
api_v1_router.include_router(notifications_router)
api_v1_router.include_router(applications_router)
api_v1_router.include_router(subscriptions_router)
api_v1_router.include_router(push_router)
api_v1_router.include_router(monitoring_router)
api_v1_router.include_router(internal_router)

__all__ = ["api_v1_router"]
