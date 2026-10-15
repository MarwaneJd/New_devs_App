"""Regression tests against the assignment's Postgres schema and seed data.

Run: docker compose exec -T backend python -m unittest discover -s tests -v
All fixture writes are rolled back.
"""
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1 import dashboard, login
from app.config import settings
from app.core.auth import auth_cache
from app.core.database_pool import db_pool
from app.core.tenant_resolver import TenantResolver
from app.models.auth import AuthenticatedUser
from app.services import cache
from app.services.reservations import (
    CurrencyRequiredError, PropertyNotFoundError, calculate_monthly_revenue,
    calculate_total_revenue, revenue_bounds,
)


class BoundariesTests(unittest.TestCase):
    def test_user_editable_metadata_cannot_grant_tenant_access(self):
        self.assertIsNone(TenantResolver.resolve_tenant_from_user({
            "user_metadata": {"tenant_id": "tenant-a"},
        }))

    def test_paris_march_covers_dst(self):
        start, end = revenue_bounds(2024, 3, "Europe/Paris")
        self.assertEqual(start.isoformat(), "2024-02-29T23:00:00+00:00")
        self.assertEqual(end.isoformat(), "2024-03-31T22:00:00+00:00")

    def test_new_york_march_covers_dst(self):
        start, end = revenue_bounds(2024, 3, "America/New_York")
        self.assertEqual(start.isoformat(), "2024-03-01T05:00:00+00:00")
        self.assertEqual(end.isoformat(), "2024-04-01T04:00:00+00:00")

    def test_december_and_annual_rollover(self):
        self.assertEqual(revenue_bounds(2024, 12, "UTC")[1].year, 2025)
        self.assertEqual(revenue_bounds(2024, None, "UTC"), (
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 1, 1, tzinfo=timezone.utc),
        ))

    def test_invalid_period(self):
        for year, month in [(2024, 0), (2024, 13), (0, 1), (9999, 12)]:
            with self.subTest(year=year, month=month), self.assertRaises(ValueError):
                revenue_bounds(year, month, "UTC")


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db_pool.initialize()
        self.session = db_pool.get_session()
        await self.session.begin()
        await self.session.execute(text("""
            INSERT INTO properties (id, tenant_id, name, timezone)
            VALUES ('regression-property', 'tenant-a', 'Regression', 'Europe/Paris'),
                   ('regression-property', 'tenant-b', 'Regression B', 'America/New_York')
        """))

    async def asyncTearDown(self):
        await self.session.rollback()
        await self.session.close()
        await db_pool.close()

    async def insert(self, key, when, amount, currency="USD", tenant="tenant-a"):
        await self.session.execute(text("""
            INSERT INTO reservations
                (id, property_id, tenant_id, check_in_date, check_out_date, total_amount, currency)
            VALUES (:id, 'regression-property', :tenant, :when, :when, :amount, :currency)
        """), {"id": key, "tenant": tenant, "when": datetime.fromisoformat(when),
               "amount": Decimal(amount), "currency": currency})

    async def total(self, **filters):
        return await calculate_total_revenue(
            "regression-property", "tenant-a", db_session=self.session, **filters,
        )

    async def test_seeded_march_includes_paris_booking_in_february_utc(self):
        total = await calculate_monthly_revenue(
            "prop-001", 3, 2024, self.session, tenant_id="tenant-a",
        )
        self.assertEqual(total, Decimal("2250.00"))
        feb = await calculate_monthly_revenue(
            "prop-001", 2, 2024, self.session, tenant_id="tenant-a",
        )
        self.assertEqual(feb, Decimal("0.00"))

    async def test_half_open_month_boundaries(self):
        for key, when, amount in [
            ("before", "2024-02-29T22:59:59.999999+00:00", "100"),
            ("start", "2024-02-29T23:00:00+00:00", "1"),
            ("last", "2024-03-31T21:59:59.999999+00:00", "2"),
            ("end", "2024-03-31T22:00:00+00:00", "100"),
        ]:
            await self.insert(key, when, amount)
        result = await self.total(month=3, year=2024)
        self.assertEqual((result["total"], result["count"]), ("3.00", 2))

    async def test_new_york_month_boundaries(self):
        for key, when in [
            ("ny-before", "2024-03-01T04:59:59+00:00"),
            ("ny-start", "2024-03-01T05:00:00+00:00"),
            ("ny-last", "2024-04-01T03:59:59+00:00"),
            ("ny-end", "2024-04-01T04:00:00+00:00"),
        ]:
            await self.insert(key, when, "10", tenant="tenant-b")
        result = await calculate_total_revenue(
            "regression-property", "tenant-b", month=3, year=2024, db_session=self.session,
        )
        self.assertEqual((result["total"], result["count"]), ("20.00", 2))

    async def test_sum_subcents_before_rounding(self):
        for index, amount in enumerate(["333.333", "333.333", "333.334"]):
            await self.insert(f"decimal-{index}", "2024-03-10T00:00:00+00:00", amount)
        self.assertEqual((await self.total())["total"], "1000.00")

    async def test_half_cent_rounding_is_decimal_half_up(self):
        await self.insert("half-cent", "2024-03-10T00:00:00+00:00", "1.005")
        self.assertEqual((await self.total())["total"], "1.01")

    async def test_refund_half_cent_rounding(self):
        await self.insert("refund", "2024-03-10T00:00:00+00:00", "-1.005")
        self.assertEqual((await self.total())["total"], "-1.01")

    async def test_empty_owned_property_and_duplicate_ids_are_isolated(self):
        await self.insert("tenant-b-only", "2024-03-10T00:00:00+00:00", "999", tenant="tenant-b")
        result = await self.total()
        self.assertEqual((result["total"], result["count"]), ("0.00", 0))

    async def test_foreign_and_missing_properties_are_not_zero_or_mock_data(self):
        for property_id in ["prop-004", "does-not-exist"]:
            with self.subTest(property_id=property_id), self.assertRaises(PropertyNotFoundError):
                await calculate_total_revenue(property_id, "tenant-a", db_session=self.session)

    async def test_currencies_are_never_added_together(self):
        await self.insert("usd", "2024-03-10T00:00:00+00:00", "10.005")
        await self.insert("eur", "2024-03-10T00:00:00+00:00", "20.005", currency="EUR")
        with self.assertRaises(CurrencyRequiredError):
            await self.total()
        for currency, expected in [("USD", "10.01"), ("EUR", "20.01")]:
            result = await self.total(currency=currency)
            self.assertEqual((result["currency"], result["total"], result["count"]), (currency, expected, 1))

    async def test_annual_uses_local_year_boundaries(self):
        await self.insert("year-start", "2023-12-31T23:00:00+00:00", "1")
        await self.insert("year-last", "2024-12-31T22:59:59+00:00", "2")
        await self.insert("next-year", "2024-12-31T23:00:00+00:00", "100")
        result = await self.total(year=2024)
        self.assertEqual((result["total"], result["count"]), ("3.00", 2))


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entries = {}
        self.redis = AsyncMock()
        self.redis.get.side_effect = lambda key: self.entries.get(key)

        async def put(key, ttl, value):
            self.assertEqual(ttl, 300)
            self.entries[key] = value
        self.redis.setex.side_effect = put

        async def calculate(property_id, tenant_id, **filters):
            return {"property_id": property_id, "tenant_id": tenant_id,
                    "total": "2250.00" if tenant_id == "tenant-a" else "0.00",
                    "currency": filters.get("currency") or "USD", "count": 4,
                    "timezone": "Europe/Paris", "year": filters.get("year"),
                    "month": filters.get("month")}
        self.calculate = AsyncMock(side_effect=calculate)
        self.patches = [
            patch.object(cache, "redis_client", self.redis),
            patch.object(cache, "calculate_total_revenue", self.calculate),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_both_cache_warming_orders_and_repeated_refresh(self):
        for first, second in [("tenant-a", "tenant-b"), ("tenant-b", "tenant-a")]:
            self.entries.clear()
            for tenant in [first, second, first, second]:
                result = await cache.get_revenue_summary("prop-001", tenant)
                self.assertEqual(result["tenant_id"], tenant)
                self.assertEqual(result["total"], "2250.00" if tenant == "tenant-a" else "0.00")

    async def test_month_year_currency_and_all_time_have_distinct_entries(self):
        for filters in [{}, {"year": 2024}, {"month": 3, "year": 2024},
                        {"month": 2, "year": 2024}, {"month": 3, "year": 2025},
                        {"month": 3, "year": 2024, "currency": "EUR"}]:
            await cache.get_revenue_summary("prop-001", "tenant-a", **filters)
        self.assertEqual(len(self.entries), 6)

    async def test_old_keys_and_wrong_tenant_cache_payload_are_ignored(self):
        self.entries["revenue:prop-001"] = '{"tenant_id":"tenant-b","total":"999.00"}'
        await cache.get_revenue_summary("prop-001", "tenant-a")
        key = next(key for key in self.entries if key.startswith("revenue:v2:"))
        self.entries[key] = json.dumps({"tenant_id": "tenant-b", "total": "999.00"})
        self.assertEqual((await cache.get_revenue_summary("prop-001", "tenant-a"))["total"], "2250.00")

    async def test_malformed_cache_is_ignored(self):
        self.redis.get.side_effect = None
        self.redis.get.return_value = "invalid json"
        self.assertEqual((await cache.get_revenue_summary("prop-001", "tenant-a"))["total"], "2250.00")

    async def test_redis_read_and_write_failure_still_returns_real_result(self):
        self.redis.get.side_effect = RedisConnectionError()
        self.redis.setex.side_effect = RedisConnectionError()
        self.assertEqual((await cache.get_revenue_summary("prop-001", "tenant-a"))["total"], "2250.00")

    async def test_database_failure_is_not_cached(self):
        self.calculate.side_effect = SQLAlchemyError("database unavailable")
        with self.assertRaises(SQLAlchemyError):
            await cache.get_revenue_summary("prop-001", "tenant-a")
        self.redis.setex.assert_not_awaited()

    async def test_no_tenant_never_reads_cache(self):
        with self.assertRaises(ValueError):
            await cache.get_revenue_summary("prop-001", "")
        self.redis.get.assert_not_awaited()


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        auth_cache.clear()
        self.app = FastAPI()
        self.app.include_router(login.router, prefix="/api/v1")
        self.app.include_router(dashboard.router, prefix="/api/v1")
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def sign_in(self, name="sunset", password="client_a_2024"):
        response = await self.client.post("/api/v1/auth/login", json={
            "email": name + "@propertyflow.com", "password": password,
        })
        self.assertEqual(response.status_code, 200)
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    async def test_both_provided_credentials(self):
        for name, password in [("sunset", "client_a_2024"), ("ocean", "client_b_2024")]:
            headers = await self.sign_in(name, password)
            with patch.object(dashboard, "get_revenue_summary", AsyncMock(return_value={
                "property_id": "prop-001", "total": "2250.00", "currency": "USD",
                "count": 4, "timezone": "Europe/Paris", "month": 3, "year": 2024,
            })) as summary:
                response = await self.client.get("/api/v1/dashboard/summary?property_id=prop-001&month=3&year=2024",
                                                 headers={**headers, "X-Simulated-Tenant": "other-tenant"})
                self.assertEqual(response.status_code, 200)
                self.assertIsInstance(response.json()["total_revenue"], str)
                self.assertEqual(summary.await_args.args[1], "tenant-a" if name == "sunset" else "tenant-b")

    async def test_invalid_credentials_including_old_passwordless_admin(self):
        for email in ["sunset@propertyflow.com", "ocean@propertyflow.com", "candidate@propertyflow.com"]:
            response = await self.client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
            self.assertEqual(response.status_code, 401)

    async def test_missing_tampered_and_expired_tokens(self):
        for token in ["", "mock-token-123", jwt.encode({
            "id": "x", "email": "candidate@propertyflow.com", "exp": 1,
            "aud": "authenticated",
        }, settings.secret_key, algorithm="HS256"), jwt.encode({
            "id": "x", "email": "candidate@propertyflow.com", "aud": "authenticated",
            "app_metadata": {"tenant_id": "tenant-a"},
        }, "wrong-signing-key", algorithm="HS256"), jwt.encode({
            "id": "x", "email": "candidate@propertyflow.com", "aud": "authenticated",
            "app_metadata": {"tenant_id": "tenant-a"},
        }, settings.secret_key, algorithm="HS256")]:
            headers = {"Authorization": "Bearer " + token} if token else {}
            response = await self.client.get("/api/v1/dashboard/summary?property_id=prop-001", headers=headers)
            self.assertEqual(response.status_code, 401)

    async def test_unknown_user_has_no_default_tenant(self):
        self.assertIsNone(await TenantResolver.resolve_tenant_id("unknown", "unknown@example.com"))

    async def test_signed_tenant_claim_is_used_instead_of_email_mapping(self):
        token = jwt.encode({
            "id": "x", "email": "sunset@propertyflow.com", "aud": "authenticated",
            "exp": datetime.now(timezone.utc).timestamp() + 3600,
            "app_metadata": {"tenant_id": "tenant-b"},
        }, settings.secret_key, algorithm="HS256")
        with patch.object(dashboard, "list_properties", AsyncMock(return_value=[])) as properties:
            response = await self.client.get("/api/v1/dashboard/properties",
                                             headers={"Authorization": "Bearer " + token})
            self.assertEqual(response.status_code, 200)
            properties.assert_awaited_once_with("tenant-b")

    async def test_missing_tenant_fails_closed(self):
        self.app.dependency_overrides[dashboard.get_current_user] = lambda: AuthenticatedUser(
            id="x", email="x@example.com", permissions=[], cities=[], is_admin=False,
        )
        response = await self.client.get("/api/v1/dashboard/summary?property_id=prop-001")
        self.assertEqual(response.status_code, 403)

    async def test_invalid_filters(self):
        headers = await self.sign_in()
        for query in ["month=3", "month=0&year=2024", "month=13&year=2024",
                      "year=0", "year=9999", "currency=usd"]:
            response = await self.client.get("/api/v1/dashboard/summary?property_id=prop-001&" + query,
                                             headers=headers)
            self.assertEqual(response.status_code, 422)

    async def test_property_not_found_and_database_failure_statuses(self):
        headers = await self.sign_in()
        for error, status in [(PropertyNotFoundError(), 404), (SQLAlchemyError(), 503),
                              (CurrencyRequiredError("Select a currency"), 422)]:
            with patch.object(dashboard, "get_revenue_summary", AsyncMock(side_effect=error)):
                response = await self.client.get("/api/v1/dashboard/summary?property_id=prop-001", headers=headers)
                self.assertEqual(response.status_code, status)

    async def test_expired_auth_cache_cannot_extend_token_validity(self):
        headers = await self.sign_in()
        with patch.object(dashboard, "list_properties", AsyncMock(return_value=[])):
            await self.client.get("/api/v1/dashboard/properties", headers=headers)
            entry = next(iter(auth_cache.values()))
            entry["expires_at"] = 0
            with patch("app.core.auth.jwt.decode", side_effect=Exception("expired")):
                response = await self.client.get("/api/v1/dashboard/properties", headers=headers)
                self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
