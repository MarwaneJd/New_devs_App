from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.database_pool import db_pool


class PropertyNotFoundError(LookupError):
    pass


class CurrencyRequiredError(ValueError):
    pass


def revenue_bounds(year: int, month: Optional[int], property_timezone: str):
    """Half-open UTC bounds for a calendar month/year in the property's zone."""
    if not 1 <= year <= 9998 or (month is not None and not 1 <= month <= 12):
        raise ValueError("Invalid reporting period")
    zone = ZoneInfo(property_timezone)
    start = datetime(year, month or 1, 1, tzinfo=zone)
    if month is None or month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=zone)
    else:
        end = datetime(year, month + 1, 1, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


@asynccontextmanager
async def revenue_session(db_session=None):
    if db_session is not None:
        yield db_session
    else:
        await db_pool.initialize()
        async with db_pool.get_session() as session:
            yield session


async def list_properties(tenant_id: str, db_session=None):
    if not tenant_id:
        raise ValueError("Tenant context is required")
    async with revenue_session(db_session) as session:
        result = await session.execute(text("""
            SELECT p.id, p.name, p.timezone,
                   COALESCE(ARRAY_AGG(DISTINCT r.currency ORDER BY r.currency)
                       FILTER (WHERE r.currency IS NOT NULL), ARRAY['USD']) AS currencies
            FROM properties p
            LEFT JOIN reservations r ON r.property_id = p.id AND r.tenant_id = p.tenant_id
            WHERE p.tenant_id = :tenant_id
            GROUP BY p.id, p.tenant_id, p.name, p.timezone
            ORDER BY p.id
        """), {"tenant_id": tenant_id})
        return [dict(row) for row in result.mappings()]


async def calculate_monthly_revenue(
    property_id: str, month: int, year: int, db_session=None, *, tenant_id: str,
    currency: Optional[str] = None,
) -> Decimal:
    result = await calculate_total_revenue(
        property_id, tenant_id, month=month, year=year,
        currency=currency, db_session=db_session,
    )
    return Decimal(result["total"])


async def calculate_total_revenue(
    property_id: str, tenant_id: str, *, month: Optional[int] = None,
    year: Optional[int] = None, currency: Optional[str] = None, db_session=None,
) -> Dict[str, Any]:
    """Sum exact stored amounts by check-in, rounding once for the report."""
    if not tenant_id:
        raise ValueError("Tenant context is required")
    if month is not None and year is None:
        raise ValueError("A year is required with a month")

    async with revenue_session(db_session) as session:
        result = await session.execute(text("""
            SELECT timezone FROM properties
            WHERE id = :property_id AND tenant_id = :tenant_id
        """), {"property_id": property_id, "tenant_id": tenant_id})
        property_timezone = result.scalar_one_or_none()
        if property_timezone is None:
            raise PropertyNotFoundError("Property not found")

        conditions = ["property_id = :property_id", "tenant_id = :tenant_id"]
        params = {"property_id": property_id, "tenant_id": tenant_id}
        if year is not None:
            params["start"], params["end"] = revenue_bounds(year, month, property_timezone)
            conditions.extend(["check_in_date >= :start", "check_in_date < :end"])
        if currency is not None:
            conditions.append("currency = :currency")
            params["currency"] = currency

        result = await session.execute(text(f"""
            SELECT currency, SUM(total_amount) AS total, COUNT(*) AS count
            FROM reservations
            WHERE {' AND '.join(conditions)}
            GROUP BY currency
        """), params)
        rows = result.mappings().all()
        if len(rows) > 1:
            raise CurrencyRequiredError("Select a currency; different currencies cannot be added together")
        row = rows[0] if rows else None
        # NUMERIC is returned as Decimal. Never round individual reservations.
        total = (row["total"] if row else Decimal("0")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )
        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": format(total, ".2f"),
            "currency": row["currency"] if row else (currency or "USD"),
            "count": row["count"] if row else 0,
            "timezone": property_timezone,
            "month": month,
            "year": year,
        }
