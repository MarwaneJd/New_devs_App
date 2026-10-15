import json
import logging
import os
from typing import Any, Dict, Optional

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.services.reservations import calculate_total_revenue

logger = logging.getLogger(__name__)
redis_client = redis.Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    socket_connect_timeout=1, socket_timeout=1,
)


async def get_revenue_summary(
    property_id: str, tenant_id: str, *, month: Optional[int] = None,
    year: Optional[int] = None, currency: Optional[str] = None,
) -> Dict[str, Any]:
    if not tenant_id:
        raise ValueError("Tenant context is required")
    # Versioning prevents reuse of old, unsafe entries. JSON avoids delimiter collisions.
    cache_key = "revenue:v2:" + json.dumps(
        [tenant_id, property_id, year, month, currency], separators=(",", ":"),
    )
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            if (isinstance(data, dict)
                    and data.get("tenant_id") == tenant_id
                    and data.get("property_id") == property_id
                    and data.get("year") == year and data.get("month") == month
                    and (currency is None or data.get("currency") == currency)
                    and isinstance(data.get("total"), str)
                    and all(key in data for key in ("currency", "count", "timezone"))):
                return data
    except (RedisError, ValueError, TypeError):
        logger.warning("Revenue cache unavailable or invalid; querying the database")

    result = await calculate_total_revenue(
        property_id, tenant_id, month=month, year=year, currency=currency,
    )
    try:
        await redis_client.setex(cache_key, 300, json.dumps(result))
    except RedisError:
        logger.warning("Revenue cache write failed; returning database result")
    return result
