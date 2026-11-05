"""Who is away for which meals, and what that means for planned meals.

Absences are informational until applied: they set defaults for new meals and flag conflicts on existing ones,
but a planned meal's participants and servings only change when someone chooses to apply the absences.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Absence, MealPlan, PersonProfile


def absences_between(db: Session, household_id: uuid.UUID, start: date, end: date) -> list[Absence]:
    return list(db.scalars(select(Absence).where(Absence.household_id == household_id, Absence.start_date <= end,
                                                 Absence.end_date >= start).order_by(Absence.start_date)))


def away_ids(absences: list[Absence], d: date, slot: str) -> set[uuid.UUID]:
    return {a.person_id for a in absences if a.covers(d, slot)}


def present_people(db: Session, household_id: uuid.UUID, d: date, slot: str) -> list[PersonProfile]:
    away = away_ids(absences_between(db, household_id, d, d), d, slot)
    people = db.scalars(select(PersonProfile).where(PersonProfile.household_id == household_id))
    return [p for p in people if p.id not in away]


def suggested_servings(people: list[PersonProfile]) -> float:
    """Sum of each person's usual portion (adults 1, toddlers typically 0.5), at least half a serving."""
    return max(0.5, round(sum(p.default_portion or 1 for p in people) * 2) / 2)


def meal_conflicts(db: Session, mp: MealPlan, absences: list[Absence] | None = None) -> list[Absence]:
    absences = absences if absences is not None else absences_between(db, mp.household_id, mp.date, mp.date)
    parts = set(mp.participant_ids or [])
    return [a for a in absences if a.covers(mp.date, mp.slot) and a.person_id in parts]


def apply_to_meal(db: Session, mp: MealPlan) -> dict:
    """Remove away people from a planned meal and reduce servings by their usual portions."""
    conflicts = meal_conflicts(db, mp)
    gone = {a.person_id for a in conflicts}
    if not gone:
        return {"changed": False, "removed": []}
    people = {p.id: p for p in db.scalars(select(PersonProfile).where(PersonProfile.id.in_(gone)))}
    reduce_by = sum((people[g].default_portion or 1) for g in gone if g in people)
    mp.participant_ids = [x for x in mp.participant_ids if x not in gone]
    mp.servings = max(0.5, round((mp.servings - reduce_by) * 2) / 2)
    return {"changed": True, "removed": [people[g].name for g in gone if g in people], "servings": mp.servings}
