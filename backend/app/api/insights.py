"""Insights: spending, usage and waste, unit-price trends and run-out forecasts (read-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import Principal, require
from ..db import get_db
from ..services import insights

router = APIRouter(prefix="/api/v1/insights", tags=["insights"])


@router.get("")
def get_insights(weeks: int = Query(12, ge=4, le=52), p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    """Everything on the Insights screen: weekly spending, usage and waste, price trends and forecasts."""
    return insights.overview(db, p.household_id, weeks)


@router.get("/forecasts")
def get_forecasts(p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    """When regularly used foods are likely to run out, with the basis and a confidence range."""
    return insights.forecasts(db, p.household_id)


@router.get("/prices")
def get_price_trends(days: int = Query(365, ge=30, le=1095), p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    """Unit-price trends (per kg, litre or item) for foods bought at least twice, with the cheapest stores."""
    return insights.price_trends(db, p.household_id, days=days, limit=50)
