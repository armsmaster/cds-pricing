"""Data seeding and serializers for reference data management.

Populates rate_indices, calendar_registry, and holidays from the bundled
JSON files on first startup (tables empty). Provides dict serializers for
the API and UI.
"""

from __future__ import annotations

import json
from datetime import date as date_type
from importlib import resources
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session


def _load_json(name: str) -> Any:
    raw = resources.files("cdslib").joinpath("data").joinpath(name).read_text(encoding="utf-8")
    return json.loads(raw)


def _empty(session: Session, model: object) -> bool:
    return session.scalar(select(__import__("sqlalchemy").func.count()).select_from(model)) == 0  # type: ignore[arg-type]


def seed_from_json(session: Session) -> None:
    from app.backend.models import CalendarRegistryModel, HolidayModel, RateIndex

    if _empty(session, RateIndex):
        rates = _load_json("rate_indices.json")
        for name, spec in rates.items():
            session.add(
                RateIndex(
                    name=name.upper(),
                    currency=str(spec.get("currency", "")).upper(),
                    day_count=str(spec.get("day_count", "")),
                    spot_lag=int(spec.get("spot_lag", 0)),
                    payment_lag=int(spec.get("payment_lag", 0)),
                    fixed_frequency=str(spec.get("fixed_frequency", "")),
                    business_day_convention=str(spec.get("business_day_convention", "")),
                )
            )

    if _empty(session, CalendarRegistryModel):
        holidays_data = _load_json("holidays.json")
        for currency, dates in holidays_data.items():
            cur = currency.upper()
            cal = CalendarRegistryModel(
                code=f"{cur}_HOLIDAY",
                description=f"{cur} trading holidays",
                currency=cur,
                is_default_for_ois=(cur == "RUB"),
                is_default_for_cds=False,
            )
            session.add(cal)
            session.flush()
            for d in dates:
                session.add(HolidayModel(calendar_id=cal.id, date=date_type.fromisoformat(d)))

        moex_data = _load_json("moex_calendar.json")
        for currency, dates in moex_data.items():
            cur = currency.upper()
            cal = CalendarRegistryModel(
                code=f"{cur}_MOEX",
                description=f"{cur} MOEX settlement calendar",
                currency=cur,
                is_default_for_ois=False,
                is_default_for_cds=(cur == "RUB"),
            )
            session.add(cal)
            session.flush()
            for d in dates:
                session.add(HolidayModel(calendar_id=cal.id, date=date_type.fromisoformat(d)))

    session.commit()


# --- serializers --------------------------------------------------------


def rate_index_dict(row: object) -> dict[str, Any]:
    return {
        "id": getattr(row, "id", None),
        "name": getattr(row, "name", ""),
        "currency": getattr(row, "currency", ""),
        "day_count": getattr(row, "day_count", ""),
        "spot_lag": getattr(row, "spot_lag", 0),
        "payment_lag": getattr(row, "payment_lag", 0),
        "fixed_frequency": getattr(row, "fixed_frequency", ""),
        "business_day_convention": getattr(row, "business_day_convention", ""),
    }


def calendar_dict(row: object) -> dict[str, Any]:
    dates = getattr(row, "dates", [])
    return {
        "id": getattr(row, "id", None),
        "code": getattr(row, "code", ""),
        "description": getattr(row, "description", ""),
        "currency": getattr(row, "currency", ""),
        "is_default_for_cds": bool(getattr(row, "is_default_for_cds", False)),
        "is_default_for_ois": bool(getattr(row, "is_default_for_ois", False)),
        "date_count": len(dates) if hasattr(dates, "__len__") else 0,
    }


def holiday_dict(row: object) -> dict[str, Any]:
    d = getattr(row, "date", None)
    return {
        "id": getattr(row, "id", None),
        "calendar_id": getattr(row, "calendar_id", None),
        "date": d.isoformat() if d is not None and hasattr(d, "isoformat") else str(d or ""),
    }
