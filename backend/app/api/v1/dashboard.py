import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from app.core.auth import authenticate_request as get_current_user
from app.models.auth import AuthenticatedUser
from app.services.cache import get_revenue_summary
from app.services.reservations import (
    CurrencyRequiredError, PropertyNotFoundError, list_properties,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def require_tenant(current_user: AuthenticatedUser) -> str:
    if not current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant context is required")
    return current_user.tenant_id


@router.get("/dashboard/properties")
async def get_dashboard_properties(current_user: AuthenticatedUser = Depends(get_current_user)):
    tenant_id = require_tenant(current_user)
    try:
        return await list_properties(tenant_id)
    except (SQLAlchemyError, OSError):
        logger.exception("Property lookup failed")
        raise HTTPException(status_code=503, detail="Property data is temporarily unavailable")


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str = Query(min_length=1),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    year: Optional[int] = Query(default=None, ge=1, le=9998),
    currency: Optional[str] = Query(default=None, pattern="^[A-Z]{3}$"),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    tenant_id = require_tenant(current_user)
    if month is not None and year is None:
        raise HTTPException(status_code=422, detail="A year is required with a month")
    try:
        data = await get_revenue_summary(
            property_id, tenant_id, month=month, year=year, currency=currency,
        )
    except PropertyNotFoundError:
        raise HTTPException(status_code=404, detail="Property not found")
    except CurrencyRequiredError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except (SQLAlchemyError, OSError):
        logger.exception("Revenue query failed")
        raise HTTPException(status_code=503, detail="Revenue data is temporarily unavailable")
    return {
        "property_id": data["property_id"],
        "total_revenue": data["total"],
        "currency": data["currency"],
        "reservations_count": data["count"],
        "timezone": data["timezone"],
        "month": data["month"],
        "year": data["year"],
    }
