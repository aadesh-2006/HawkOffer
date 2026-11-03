#!/usr/bin/env python3
"""HawkOffer Production Deployment & Cloud Smoke-Test Suite.

Automated verification script to validate local daemon or live public deployment
across REST API endpoints, SPA routing, authentication lifecycle, company catalog,
application tracker CRUD, Web Push readiness, and cron webhook authentication.

Usage:
    # Test local development or Docker container
    python scripts/smoke_test.py --api-url http://127.0.0.1:8000/api/v1 --cron-secret <secret>

    # Test live public deployment (Render + Cloudflare Pages)
    python scripts/smoke_test.py \
        --api-url https://hawkoffer-api.onrender.com/api/v1 \
        --web-url https://hawkoffer.pages.dev \
        --cron-secret <secret>
"""

import argparse
import sys
import time
import uuid
from dataclasses import dataclass

try:
    import httpx
except ImportError:
    print("Error: 'httpx' is required to run the smoke test. Install via: pip install httpx")
    sys.exit(1)


@dataclass
class TestResult:
    """Individual smoke test step outcome."""

    name: str
    target: str
    status: str  # PASS, FAIL, SKIP
    details: str = ""
    duration_ms: float = 0.0


class SmokeTestRunner:
    """Executes automated end-to-end smoke verification against HawkOffer services."""

    def __init__(
        self,
        api_url: str,
        web_url: str | None = None,
        cron_secret: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        # Derive root URL from API URL (e.g. https://api.com from https://api.com/api/v1)
        if self.api_url.endswith("/api/v1"):
            self.root_url = self.api_url[:-7]
        else:
            self.root_url = self.api_url
            self.api_url = f"{self.api_url}/api/v1"

        self.web_url = web_url.rstrip("/") if web_url else None
        self.cron_secret = cron_secret
        self.timeout = timeout
        self.results: list[TestResult] = []

        # Session state
        self.auth_token: str | None = None
        self.test_user_email = f"smoketest_{uuid.uuid4().hex[:8]}@hawkoffer.dev"
        self.test_password = f"HawkOffer!{uuid.uuid4().hex[:12]}"
        self.created_app_id: int | None = None

    def record(
        self,
        name: str,
        target: str,
        status: str,
        details: str = "",
        duration_ms: float = 0.0,
    ) -> None:
        self.results.append(
            TestResult(
                name=name,
                target=target,
                status=status,
                details=details,
                duration_ms=duration_ms,
            )
        )

    def test_backend_health_and_root(self, client: httpx.Client) -> bool:
        """Verify root GET / and healthcheck GET /api/v1/health."""
        # 1. Root API metadata
        t0 = time.monotonic()
        try:
            r = client.get(f"{self.root_url}/", timeout=self.timeout)
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 200 and "status" in r.json():
                self.record(
                    "Backend Root Metadata",
                    f"GET {self.root_url}/",
                    "PASS",
                    f"HTTP {r.status_code}",
                    d,
                )
            else:
                self.record(
                    "Backend Root Metadata",
                    f"GET {self.root_url}/",
                    "FAIL",
                    f"HTTP {r.status_code}: {r.text[:80]}",
                    d,
                )
                return False
        except Exception as exc:
            self.record(
                "Backend Root Metadata",
                f"GET {self.root_url}/",
                "FAIL",
                f"Connection failed: {exc}",
            )
            return False

        # 2. System Healthcheck
        t0 = time.monotonic()
        try:
            r = client.get(f"{self.api_url}/health", timeout=self.timeout)
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 200:
                data = r.json()
                db_status = data.get("database", "unknown")
                self.record(
                    "Health Endpoint",
                    f"GET {self.api_url}/health",
                    "PASS",
                    f"DB: {db_status}",
                    d,
                )
                return True
            self.record(
                "Health Endpoint",
                f"GET {self.api_url}/health",
                "FAIL",
                f"HTTP {r.status_code}: {r.text[:80]}",
                d,
            )
            return False
        except Exception as exc:
            self.record(
                "Health Endpoint",
                f"GET {self.api_url}/health",
                "FAIL",
                f"Connection failed: {exc}",
            )
            return False

    def test_cron_authentication(self, client: httpx.Client) -> None:
        """Verify internal cron endpoint rejects unauthorized and accepts valid secret."""
        endpoint = f"{self.api_url}/internal/cron/monitor"

        # 1. Reject missing credentials
        t0 = time.monotonic()
        try:
            r = client.post(endpoint, timeout=self.timeout)
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 401:
                self.record(
                    "Cron Auth Reject (Missing)",
                    f"POST {endpoint}",
                    "PASS",
                    "HTTP 401 Unauthorized",
                    d,
                )
            else:
                self.record(
                    "Cron Auth Reject (Missing)",
                    f"POST {endpoint}",
                    "FAIL",
                    f"Expected 401, got HTTP {r.status_code}",
                    d,
                )
        except Exception as exc:
            self.record("Cron Auth Reject (Missing)", f"POST {endpoint}", "FAIL", str(exc))

        # 2. Reject invalid credentials
        t0 = time.monotonic()
        try:
            r = client.post(
                endpoint,
                headers={"X-Cron-Secret": "invalid-dummy-secret-token"},
                timeout=self.timeout,
            )
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 401:
                self.record(
                    "Cron Auth Reject (Invalid)",
                    f"POST {endpoint}",
                    "PASS",
                    "HTTP 401 Unauthorized",
                    d,
                )
            else:
                self.record(
                    "Cron Auth Reject (Invalid)",
                    f"POST {endpoint}",
                    "FAIL",
                    f"Expected 401, got HTTP {r.status_code}",
                    d,
                )
        except Exception as exc:
            self.record("Cron Auth Reject (Invalid)", f"POST {endpoint}", "FAIL", str(exc))

        # 3. Accept valid secret if configured
        if self.cron_secret:
            t0 = time.monotonic()
            try:
                r = client.post(
                    endpoint,
                    headers={"X-Cron-Secret": self.cron_secret},
                    timeout=max(self.timeout, 45.0),
                )
                d = (time.monotonic() - t0) * 1000
                if r.status_code == 200:
                    status_info = r.json().get("status", "ok")
                    self.record(
                        "Cron Auth Accept (Valid Secret)",
                        f"POST {endpoint}",
                        "PASS",
                        f"HTTP 200, status={status_info}",
                        d,
                    )
                else:
                    self.record(
                        "Cron Auth Accept (Valid Secret)",
                        f"POST {endpoint}",
                        "FAIL",
                        f"HTTP {r.status_code}: {r.text[:80]}",
                        d,
                    )
            except Exception as exc:
                self.record(
                    "Cron Auth Accept (Valid Secret)",
                    f"POST {endpoint}",
                    "FAIL",
                    str(exc),
                )
        else:
            self.record(
                "Cron Auth Accept (Valid Secret)",
                f"POST {endpoint}",
                "SKIP",
                "No --cron-secret argument provided",
            )

    def test_user_authentication_lifecycle(self, client: httpx.Client) -> bool:
        """Verify user registration, login, token reception, and identity retrieval."""
        reg_endpoint = f"{self.api_url}/auth/register"
        login_endpoint = f"{self.api_url}/auth/login"
        me_endpoint = f"{self.api_url}/auth/me"

        # 1. Register test user
        t0 = time.monotonic()
        try:
            r = client.post(
                reg_endpoint,
                json={"email": self.test_user_email, "password": self.test_password},
                timeout=self.timeout,
            )
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 201:
                self.record(
                    "User Registration",
                    f"POST {reg_endpoint}",
                    "PASS",
                    f"User {self.test_user_email}",
                    d,
                )
            else:
                self.record(
                    "User Registration",
                    f"POST {reg_endpoint}",
                    "FAIL",
                    f"HTTP {r.status_code}: {r.text[:80]}",
                    d,
                )
                return False
        except Exception as exc:
            self.record("User Registration", f"POST {reg_endpoint}", "FAIL", str(exc))
            return False

        # 2. Login test user
        t0 = time.monotonic()
        try:
            r = client.post(
                login_endpoint,
                json={"email": self.test_user_email, "password": self.test_password},
                timeout=self.timeout,
            )
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 200:
                tokens = r.json()
                self.auth_token = tokens.get("access_token")
                self.record(
                    "User Login & JWT Issuance",
                    f"POST {login_endpoint}",
                    "PASS",
                    f"Token length: {len(self.auth_token or '')}",
                    d,
                )
            else:
                self.record(
                    "User Login & JWT Issuance",
                    f"POST {login_endpoint}",
                    "FAIL",
                    f"HTTP {r.status_code}: {r.text[:80]}",
                    d,
                )
                return False
        except Exception as exc:
            self.record("User Login & JWT Issuance", f"POST {login_endpoint}", "FAIL", str(exc))
            return False

        # 3. Authenticated Identity (GET /auth/me)
        t0 = time.monotonic()
        try:
            r = client.get(
                me_endpoint,
                headers={"Authorization": f"Bearer {self.auth_token}"},
                timeout=self.timeout,
            )
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 200 and r.json().get("email") == self.test_user_email:
                uid = r.json().get("id")
                self.record(
                    "Authenticated Identity",
                    f"GET {me_endpoint}",
                    "PASS",
                    f"Verified user_id={uid}",
                    d,
                )
                return True
            self.record(
                "Authenticated Identity",
                f"GET {me_endpoint}",
                "FAIL",
                f"HTTP {r.status_code}: {r.text[:80]}",
                d,
            )
            return False
        except Exception as exc:
            self.record("Authenticated Identity", f"GET {me_endpoint}", "FAIL", str(exc))
            return False

    def test_catalog_and_application_tracker(self, client: httpx.Client) -> None:
        """Verify companies catalog, subscriptions, and Application Tracker CRUD."""
        headers = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}

        # 1. Company catalog
        t0 = time.monotonic()
        try:
            r = client.get(f"{self.api_url}/companies", headers=headers, timeout=self.timeout)
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 200:
                companies = r.json().get("items", [])
                self.record(
                    "Company Catalog",
                    f"GET {self.api_url}/companies",
                    "PASS",
                    f"{len(companies)} items",
                    d,
                )
            else:
                self.record(
                    "Company Catalog",
                    f"GET {self.api_url}/companies",
                    "FAIL",
                    f"HTTP {r.status_code}: {r.text[:80]}",
                    d,
                )
        except Exception as exc:
            self.record("Company Catalog", f"GET {self.api_url}/companies", "FAIL", str(exc))

        # 2. Application Tracker - Create
        t0 = time.monotonic()
        try:
            r = client.post(
                f"{self.api_url}/applications",
                headers=headers,
                json={
                    "company_name": "Smoke Test Corp",
                    "role_title": "Site Reliability Engineer",
                    "stage": "APPLIED",
                    "notes": "Automated smoke test application",
                },
                timeout=self.timeout,
            )
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 201:
                app_data = r.json()
                self.created_app_id = app_data.get("id")
                self.record(
                    "Application Tracker: Create",
                    f"POST {self.api_url}/applications",
                    "PASS",
                    f"id={self.created_app_id}",
                    d,
                )
            else:
                self.record(
                    "Application Tracker: Create",
                    f"POST {self.api_url}/applications",
                    "FAIL",
                    f"HTTP {r.status_code}: {r.text[:80]}",
                    d,
                )
        except Exception as exc:
            self.record(
                "Application Tracker: Create",
                f"POST {self.api_url}/applications",
                "FAIL",
                str(exc),
            )

        # 3. Application Tracker - List & Update
        if self.created_app_id:
            t0 = time.monotonic()
            try:
                r = client.patch(
                    f"{self.api_url}/applications/{self.created_app_id}",
                    headers=headers,
                    json={"stage": "INTERVIEWING"},
                    timeout=self.timeout,
                )
                d = (time.monotonic() - t0) * 1000
                if r.status_code == 200 and r.json().get("stage") == "INTERVIEWING":
                    self.record(
                        "Application Tracker: Update",
                        f"PATCH {self.api_url}/applications/{self.created_app_id}",
                        "PASS",
                        "Stage updated to INTERVIEWING",
                        d,
                    )
                else:
                    self.record(
                        "Application Tracker: Update",
                        f"PATCH {self.api_url}/applications/{self.created_app_id}",
                        "FAIL",
                        f"HTTP {r.status_code}: {r.text[:80]}",
                        d,
                    )
            except Exception as exc:
                self.record(
                    "Application Tracker: Update",
                    f"PATCH {self.api_url}/applications",
                    "FAIL",
                    str(exc),
                )

            # 4. Application Tracker - Delete
            t0 = time.monotonic()
            try:
                r = client.delete(
                    f"{self.api_url}/applications/{self.created_app_id}",
                    headers=headers,
                    timeout=self.timeout,
                )
                d = (time.monotonic() - t0) * 1000
                if r.status_code == 200:
                    self.record(
                        "Application Tracker: Delete",
                        f"DELETE {self.api_url}/applications/{self.created_app_id}",
                        "PASS",
                        f"Deleted id={self.created_app_id}",
                        d,
                    )
                else:
                    self.record(
                        "Application Tracker: Delete",
                        f"DELETE {self.api_url}/applications/{self.created_app_id}",
                        "FAIL",
                        f"HTTP {r.status_code}: {r.text[:80]}",
                        d,
                    )
            except Exception as exc:
                self.record(
                    "Application Tracker: Delete",
                    f"DELETE {self.api_url}/applications",
                    "FAIL",
                    str(exc),
                )

    def test_web_push_endpoints(self, client: httpx.Client) -> None:
        """Verify Web Push VAPID key and subscription query endpoints."""
        headers = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}

        # 1. Public VAPID Key
        t0 = time.monotonic()
        try:
            r = client.get(f"{self.api_url}/push/vapid-public-key", timeout=self.timeout)
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 200:
                key = r.json().get("public_key", "")
                self.record(
                    "Web Push VAPID Key",
                    f"GET {self.api_url}/push/vapid-public-key",
                    "PASS",
                    f"Key len: {len(key)}",
                    d,
                )
            elif r.status_code == 404:
                self.record(
                    "Web Push VAPID Key",
                    f"GET {self.api_url}/push/vapid-public-key",
                    "SKIP",
                    "VAPID keys not configured",
                    d,
                )
            else:
                self.record(
                    "Web Push VAPID Key",
                    f"GET {self.api_url}/push/vapid-public-key",
                    "FAIL",
                    f"HTTP {r.status_code}: {r.text[:80]}",
                    d,
                )
        except Exception as exc:
            self.record(
                "Web Push VAPID Key",
                f"GET {self.api_url}/push/vapid-public-key",
                "FAIL",
                str(exc),
            )

        # 2. Push Subscriptions List
        t0 = time.monotonic()
        try:
            r = client.get(
                f"{self.api_url}/push/subscriptions",
                headers=headers,
                timeout=self.timeout,
            )
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 200:
                subs = r.json()
                self.record(
                    "Push Subscriptions Endpoint",
                    f"GET {self.api_url}/push/subscriptions",
                    "PASS",
                    f"{len(subs)} subscriptions",
                    d,
                )
            else:
                self.record(
                    "Push Subscriptions Endpoint",
                    f"GET {self.api_url}/push/subscriptions",
                    "FAIL",
                    f"HTTP {r.status_code}: {r.text[:80]}",
                    d,
                )
        except Exception as exc:
            self.record(
                "Push Subscriptions Endpoint",
                f"GET {self.api_url}/push/subscriptions",
                "FAIL",
                str(exc),
            )

    def test_frontend_spa_edge_routing(self, client: httpx.Client) -> None:
        """Verify frontend root and SPA deep link fallbacks (Cloudflare Pages _redirects)."""
        if not self.web_url:
            self.record("Frontend Root", "N/A", "SKIP", "No --web-url provided")
            self.record("Frontend SPA Deep Links", "N/A", "SKIP", "No --web-url provided")
            return

        # 1. Frontend Root
        t0 = time.monotonic()
        try:
            r = client.get(self.web_url, timeout=self.timeout)
            d = (time.monotonic() - t0) * 1000
            if r.status_code == 200 and "<html" in r.text.lower():
                self.record("Frontend Root", f"GET {self.web_url}", "PASS", "HTTP 200 HTML", d)
            else:
                self.record(
                    "Frontend Root",
                    f"GET {self.web_url}",
                    "FAIL",
                    f"HTTP {r.status_code}",
                    d,
                )
        except Exception as exc:
            self.record("Frontend Root", f"GET {self.web_url}", "FAIL", str(exc))

        # 2. SPA Deep links (/tracker, /settings, /jobs)
        routes = ["/tracker", "/settings", "/jobs"]
        for route in routes:
            url = f"{self.web_url}{route}"
            t0 = time.monotonic()
            try:
                r = client.get(url, timeout=self.timeout)
                d = (time.monotonic() - t0) * 1000
                if r.status_code == 200 and "<html" in r.text.lower():
                    self.record(
                        f"SPA Deep Link ({route})",
                        f"GET {url}",
                        "PASS",
                        "HTTP 200 (SPA Redirects Active)",
                        d,
                    )
                else:
                    self.record(
                        f"SPA Deep Link ({route})",
                        f"GET {url}",
                        "FAIL",
                        f"HTTP {r.status_code} (Missing _redirects fallback)",
                        d,
                    )
            except Exception as exc:
                self.record(f"SPA Deep Link ({route})", f"GET {url}", "FAIL", str(exc))

    def run_all(self) -> bool:
        """Execute complete smoke test suite."""
        print("=" * 80)
        print("HawkOffer Smoke-Test Suite — Production Deployment Verification")
        print(f"Target API URL:      {self.api_url}")
        print(f"Target Web URL:      {self.web_url or '(None provided)'}")
        print(f"Cron Secret:         {'[CONFIGURED]' if self.cron_secret else '[NOT CONFIGURED]'}")
        print("=" * 80)

        with httpx.Client(follow_redirects=True) as client:
            backend_alive = self.test_backend_health_and_root(client)
            if backend_alive:
                self.test_cron_authentication(client)
                auth_ok = self.test_user_authentication_lifecycle(client)
                if auth_ok:
                    self.test_catalog_and_application_tracker(client)
                    self.test_web_push_endpoints(client)
            self.test_frontend_spa_edge_routing(client)

        return self.print_summary()

    def print_summary(self) -> bool:
        """Print structured test result table and return overall success."""
        print("\n" + "=" * 80)
        print(f"{'TEST CASE':<36} | {'TARGET':<22} | {'STATUS':<6} | {'DETAILS'}")
        print("-" * 80)

        passed = 0
        failed = 0
        skipped = 0

        for r in self.results:
            if r.status == "PASS":
                passed += 1
                color_code = "\033[92mPASS\033[0m"
            elif r.status == "FAIL":
                failed += 1
                color_code = "\033[91mFAIL\033[0m"
            else:
                skipped += 1
                color_code = "\033[93mSKIP\033[0m"

            target_display = (r.target[:20] + "..") if len(r.target) > 22 else r.target
            print(f"{r.name:<36} | {target_display:<22} | {color_code:<15} | {r.details}")

        print("=" * 80)
        print(
            f"Summary: {passed} passed, {failed} failed, {skipped} skipped "
            f"(Total: {len(self.results)})"
        )
        print("=" * 80)

        return failed == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HawkOffer Production Deployment Smoke-Test Runner"
    )
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8765/api/v1",
        help="Backend API base URL (e.g. http://127.0.0.1:8765/api/v1 or https://api.hawkoffer.app/api/v1)",
    )
    parser.add_argument(
        "--web-url",
        default=None,
        help="Frontend website URL (e.g. https://hawkoffer.pages.dev)",
    )
    parser.add_argument(
        "--cron-secret",
        default=None,
        help="External cron secret key for validating /internal/cron/monitor",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds (default: 15.0)",
    )

    args = parser.parse_args()
    runner = SmokeTestRunner(
        api_url=args.api_url,
        web_url=args.web_url,
        cron_secret=args.cron_secret,
        timeout=args.timeout,
    )
    success = runner.run_all()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
