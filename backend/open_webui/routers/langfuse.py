import datetime
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_session
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.team_scope import resolve_dashboard_scope, scope_metric_rows
from open_webui.langfuse.metrics import (
    get_today_so_far,
    get_last_day,
    get_last_week,
    get_last_month,
    get_current_month,
    get_custom_days,
)

log = logging.getLogger(__name__)
router = APIRouter()


class MetricRow(BaseModel):
    user: str
    model: str
    tokens: int
    cost: float
    observations: int = 0


class MetricsResponse(BaseModel):
    # `from` is a Python keyword, so the field is named `from_` and aliased.
    # FastAPI serialises by alias, so the JSON key is `from`.
    from_: str = Field(..., alias="from")
    to: str
    rows: List[MetricRow]

    model_config = ConfigDict(populate_by_name=True)


class MyUsage(BaseModel):
    month: int
    year: int
    total_tokens: int
    total_cost: float


@router.get("/metrics", response_model=MetricsResponse)
async def get_langfuse_metrics(
    period: str = "week",
    days: Optional[int] = None,
    team_id: Optional[str] = None,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Fetch Langfuse token/cost metrics per user, plus the exact UTC window used.

    period: today | day | week | month | current_month | custom
    days: required when period=custom
    team_id: scope the rows to one team; omit for the instance-wide view

    Depends on `get_verified_user`. `resolve_dashboard_scope`, the first line of
    the body, refuses any non-admin who omits `team_id`; without it any logged-in
    user could read the whole instance's spend.
    """
    # Called outside the `try` so the `except` below cannot turn a refusal into
    # a 502.
    scope = await resolve_dashboard_scope(user, team_id, db=db)

    try:
        if period == "today":
            from_ts, to_ts, rows = get_today_so_far()
        elif period == "day":
            from_ts, to_ts, rows = get_last_day()
        elif period == "week":
            from_ts, to_ts, rows = get_last_week()
        elif period == "month":
            from_ts, to_ts, rows = get_last_month()
        elif period == "current_month":
            from_ts, to_ts, rows = get_current_month()
        elif period == "custom":
            if not days or days <= 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="days parameter is required and must be positive when period=custom",
                )
            from_ts, to_ts, rows = get_custom_days(days)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown period '{period}'. Use: today, day, week, month, current_month, custom",
            )
        # Rows are scoped here on the server, never on the frontend: Langfuse has
        # no notion of a team, so it always returns instance-wide rows.
        return MetricsResponse(
            **{
                "from": from_ts,
                "to": to_ts,
                "rows": scope_metric_rows(rows, scope, team_id),
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Langfuse metrics error: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch Langfuse metrics: {str(e)}",
        )


@router.get("/my-usage", response_model=MyUsage)
async def get_my_langfuse_usage(
    user=Depends(get_verified_user),
):
    """
    Returns the current user's aggregated token/cost usage for the current calendar month.
    Matches Langfuse userId against the user's email.
    """
    try:
        _, _, rows = get_current_month()
    except Exception as e:
        log.error(f"Langfuse my-usage error: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch Langfuse usage: {str(e)}",
        )

    user_email = user.email if hasattr(user, "email") else ""
    user_rows = [r for r in rows if r.get("user", "") == user_email]

    now = datetime.datetime.utcnow()
    return MyUsage(
        month=now.month,
        year=now.year,
        total_tokens=sum(r["tokens"] for r in user_rows),
        total_cost=sum(r["cost"] for r in user_rows),
    )
