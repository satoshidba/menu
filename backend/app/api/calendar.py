"""Subscribable iCalendar feed of planned meals and who's out.

Calendar apps can't send headers, so the feed authenticates with a dedicated API token in the URL that only has the
`calendar:read` scope (create it in Settings → Connected apps → Calendar feed; revoke or rotate it there). Tokens in
URLs are redacted from access logs. Medicines and intake are never included.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import sha256
from ..config import get_settings
from ..db import get_db
from ..models import Absence, ApiToken, Household, MealPlan, PersonProfile, utcnow

router = APIRouter(prefix="/api/v1", tags=["calendar"])

SLOT_TIMES = {"breakfast": (time(8, 0), 30), "lunch": (time(12, 30), 45), "snack": (time(15, 30), 20), "dinner": (time(18, 0), 60)}
BYDAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
REASONS = {"social": "socialising", "work": "work", "study": "study", "travel": "away", "other": "out"}


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    raw = line.encode()
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode()
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur.decode())
            cur = b""
        cur += b
    parts.append(cur.decode())
    return "\r\n ".join(parts)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(db: Session, household_id, base_url: str) -> str:
    h = db.get(Household, household_id)
    tz = ZoneInfo(h.timezone)
    today = datetime.now(tz).date()
    start, end = today - timedelta(days=14), today + timedelta(days=90)
    names = {p.id: p.name for p in db.scalars(select(PersonProfile).where(PersonProfile.household_id == household_id))}
    stamp = _utc(utcnow())
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Larder//Household meals//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{_esc(h.name)} meals", f"X-WR-TIMEZONE:{h.timezone}", "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
             "X-PUBLISHED-TTL:PT1H"]

    for m in db.scalars(select(MealPlan).where(MealPlan.household_id == household_id, MealPlan.date >= start, MealPlan.date <= end,
                                               MealPlan.status != "skipped")):
        t, minutes = SLOT_TIMES.get(m.slot, (time(18, 0), 60))
        begin = datetime.combine(m.date, t, tz)
        who = ", ".join(names.get(x, "?") for x in m.participant_ids or [])
        desc = f"{m.servings:g} servings" + (f" · {who}" if who else "") + (f"\n{m.notes}" if m.notes else "")
        lines += ["BEGIN:VEVENT", f"UID:meal-{m.id}@larder", f"DTSTAMP:{stamp}", f"DTSTART:{_utc(begin)}",
                  f"DTEND:{_utc(begin + timedelta(minutes=minutes))}", f"SUMMARY:{_esc(m.slot.capitalize() + ': ' + m.title)}",
                  f"DESCRIPTION:{_esc(desc)}", f"URL:{base_url}/plan", "TRANSP:TRANSPARENT",
                  f"STATUS:{'CONFIRMED' if m.status == 'cooked' else 'TENTATIVE'}", "END:VEVENT"]

    absences = db.scalars(select(Absence).where(Absence.household_id == household_id, Absence.start_date <= end,
                                                (Absence.end_date.is_(None)) | (Absence.end_date >= start)))
    for a in absences:
        who = names.get(a.person_id, "Someone")
        what = "all day" if not a.slots else " & ".join(a.slots)
        summary = f"{who} out ({REASONS.get(a.reason, a.reason)}): {what}" + (f" — {a.note}" if a.note else "")
        ev = ["BEGIN:VEVENT", f"UID:away-{a.id}@larder", f"DTSTAMP:{stamp}", f"SUMMARY:{_esc(summary)}", "TRANSP:TRANSPARENT",
              f"URL:{base_url}/plan"]
        if a.slots:
            t, _ = SLOT_TIMES.get(a.slots[0], (time(18, 0), 60))
            t_end, mins_end = SLOT_TIMES.get(a.slots[-1], (time(18, 0), 60))
            first = a.start_date
            if a.weekdays:  # first occurrence on a matching weekday
                while first.weekday() not in a.weekdays:
                    first += timedelta(days=1)
            b = datetime.combine(first, t, tz)
            e = datetime.combine(first, t_end, tz) + timedelta(minutes=mins_end)
            ev += [f"DTSTART:{_utc(b)}", f"DTEND:{_utc(e)}"]
            last = a.end_date
            until = f";UNTIL={_utc(datetime.combine(last, time(23, 59), tz))}" if last else ""
            if a.weekdays:
                ev.append(f"RRULE:FREQ=WEEKLY;BYDAY={','.join(BYDAY[d] for d in sorted(a.weekdays))}{until}")
            elif last and last > a.start_date:
                ev.append(f"RRULE:FREQ=DAILY{until}")
        else:
            first = a.start_date
            if a.weekdays:
                while first.weekday() not in a.weekdays:
                    first += timedelta(days=1)
                ev += [f"DTSTART;VALUE=DATE:{first:%Y%m%d}", f"DTEND;VALUE=DATE:{first + timedelta(days=1):%Y%m%d}"]
                until = f";UNTIL={a.end_date:%Y%m%d}" if a.end_date else ""
                ev.append(f"RRULE:FREQ=WEEKLY;BYDAY={','.join(BYDAY[d] for d in sorted(a.weekdays))}{until}")
            else:
                ev += [f"DTSTART;VALUE=DATE:{a.start_date:%Y%m%d}", f"DTEND;VALUE=DATE:{(a.end_date or a.start_date) + timedelta(days=1):%Y%m%d}"]
        lines += ev + ["END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


@router.get("/calendar.ics", include_in_schema=True)
def calendar_feed(request: Request, token: str = Query(..., description="A token with the calendar:read scope"), db: Session = Depends(get_db)):
    """iCalendar feed (planned meals and who's out) for phone and desktop calendar apps."""
    t = db.scalar(select(ApiToken).where(ApiToken.token_hash == sha256(token)))
    if t is None or t.revoked_at is not None or (t.expires_at and t.expires_at < utcnow()) or "calendar:read" not in (t.scopes or []):
        raise HTTPException(401, "Invalid or revoked calendar token")
    if t.last_used_at is None or utcnow() - t.last_used_at > timedelta(minutes=30):
        t.last_used_at = utcnow()
        db.commit()
    base = get_settings().public_url.rstrip("/")
    return Response(build_ics(db, t.household_id, base), media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "private, max-age=300", "Content-Disposition": 'inline; filename="larder.ics"'})

