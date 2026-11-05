"""Accounts, household, people, pets and API tokens."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import (ALL_SCOPES, SESSION_COOKIE, Principal, audit, create_session, current_principal, generate_api_token,
                    hash_password, rate_limit, require_admin, require_user, validate_password_strength, verify_password)
from ..config import get_settings
from ..db import get_db
from ..models import ApiToken, Household, PersonProfile, PetProfile, User, UserSession, utcnow
from .common import get_owned, iso, sid

router = APIRouter(prefix="/api/v1", tags=["accounts"])


def user_out(u: User) -> dict:
    return {"id": sid(u.id), "email": u.email, "display_name": u.display_name, "role": u.role, "is_active": u.is_active,
            "last_login_at": iso(u.last_login_at)}


def person_out(p: PersonProfile) -> dict:
    return {"id": sid(p.id), "name": p.name, "kind": p.kind, "user_id": sid(p.user_id), "birth_month": iso(p.birth_month),
            "allergies": p.allergies or [], "dislikes": p.dislikes or [], "dietary_preferences": p.dietary_preferences or [],
            "default_portion": p.default_portion, "notes": p.notes, "colour": p.colour, "updated_at": iso(p.updated_at)}


def pet_out(p: PetProfile) -> dict:
    return {"id": sid(p.id), "name": p.name, "species": p.species, "feeding_plan": p.feeding_plan,
            "feeding_plan_source": p.feeding_plan_source, "notes": p.notes}


def token_out(t: ApiToken) -> dict:
    return {"id": sid(t.id), "name": t.name, "prefix": t.prefix, "scopes": t.scopes, "person_ids": [sid(x) for x in t.person_ids or []],
            "created_at": iso(t.created_at), "expires_at": iso(t.expires_at), "revoked_at": iso(t.revoked_at),
            "last_used_at": iso(t.last_used_at), "rotated_from_id": sid(t.rotated_from_id)}


def _set_cookie(resp: Response, raw: str) -> None:
    s = get_settings()
    resp.set_cookie(SESSION_COOKIE, raw, max_age=s.session_days * 86400, httponly=True, secure=s.cookie_secure,
                    samesite="lax", path="/")


# ---- setup & sessions ----------------------------------------------------------------------------------------------

class SetupIn(BaseModel):
    household_name: str = Field("Our household", max_length=120)
    email: EmailStr
    display_name: str = Field(max_length=120)
    password: str = Field(min_length=10, max_length=200)


@router.get("/setup/status")
def setup_status(db: Session = Depends(get_db)):
    return {"needs_setup": db.scalar(select(func.count()).select_from(User)) == 0}


@router.post("/setup", status_code=201)
def first_run_setup(body: SetupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Creates the household and the administrator. Only works while there are no users."""
    if db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(409, "Setup has already been completed")
    validate_password_strength(body.password)
    h = Household(name=body.household_name, timezone=get_settings().timezone)
    db.add(h)
    db.flush()
    u = User(household_id=h.id, email=body.email.lower(), display_name=body.display_name,
             password_hash=hash_password(body.password), role="admin")
    db.add(u)
    db.flush()
    db.add(PersonProfile(household_id=h.id, user_id=u.id, name=body.display_name, kind="adult"))
    raw, s = create_session(db, u, request.headers.get("user-agent"))
    audit(db, None, "setup.completed", "household", h.id, household_id=h.id)
    db.commit()
    _set_cookie(response, raw)
    return {"user": user_out(u), "csrf_token": s.csrf_token}


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    rate_limit(db, f"login:{ip}", get_settings().login_rate_per_minute)
    u = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if u is None or not u.is_active or not verify_password(u.password_hash, body.password):
        audit(db, None, "auth.login_failed", "user", u.id if u else None, household_id=u.household_id if u else None)
        db.commit()
        raise HTTPException(401, "Email or password is incorrect")
    raw, s = create_session(db, u, request.headers.get("user-agent"))
    audit(db, None, "auth.login", "user", u.id, household_id=u.household_id)
    db.commit()
    _set_cookie(response, raw)
    return {"user": user_out(u), "csrf_token": s.csrf_token}


@router.post("/auth/logout")
def logout(response: Response, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    p.session.revoked_at = utcnow()
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    h = db.get(Household, p.household_id)
    out = {"household": {"id": sid(h.id), "name": h.name, "timezone": h.timezone, "currency": h.currency}}
    if p.is_user:
        out.update(user=user_out(p.user), csrf_token=p.session.csrf_token)
        out["features"] = {"ai": get_settings().ai_enabled, "barcode_lookup": get_settings().off_enabled}
    else:
        out.update(token=token_out(p.token))
    return out


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


@router.post("/auth/password")
def change_password(body: PasswordIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    if not verify_password(p.user.password_hash, body.current_password):
        raise HTTPException(403, "Current password is incorrect")
    validate_password_strength(body.new_password)
    p.user.password_hash = hash_password(body.new_password)
    # revoke other sessions
    for s in db.scalars(select(UserSession).where(UserSession.user_id == p.user.id, UserSession.id != p.session.id)):
        s.revoked_at = utcnow()
    audit(db, p, "auth.password_changed", "user", p.user.id)
    db.commit()
    return {"ok": True}


@router.get("/auth/sessions")
def list_sessions(p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(UserSession).where(UserSession.user_id == p.user.id, UserSession.revoked_at.is_(None),
                                                UserSession.expires_at > utcnow()).order_by(UserSession.created_at.desc()))
    return [{"id": sid(s.id), "user_agent": s.user_agent, "created_at": iso(s.created_at), "last_seen_at": iso(s.last_seen_at),
             "current": s.id == p.session.id} for s in rows]


@router.delete("/auth/sessions/{session_id}")
def revoke_session(session_id: uuid.UUID, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    s = db.get(UserSession, session_id)
    if s is None or s.user_id != p.user.id:
        raise HTTPException(404, "Session not found")
    s.revoked_at = utcnow()
    db.commit()
    return {"ok": True}


# ---- users (admin) ------------------------------------------------------------------------------------------------

class UserIn(BaseModel):
    email: EmailStr
    display_name: str = Field(max_length=120)
    password: str = Field(min_length=10, max_length=200)
    role: str = Field("member", pattern="^(admin|member)$")


@router.get("/users")
def list_users(p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    return [user_out(u) for u in db.scalars(select(User).where(User.household_id == p.household_id).order_by(User.created_at))]


@router.post("/users", status_code=201)
def create_user(body: UserIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    validate_password_strength(body.password)
    if db.scalar(select(User).where(func.lower(User.email) == body.email.lower())):
        raise HTTPException(409, "That email is already in use")
    u = User(household_id=p.household_id, email=body.email.lower(), display_name=body.display_name,
             password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.flush()
    if not db.scalar(select(PersonProfile).where(PersonProfile.household_id == p.household_id, PersonProfile.name == body.display_name)):
        db.add(PersonProfile(household_id=p.household_id, user_id=u.id, name=body.display_name, kind="adult"))
    audit(db, p, "user.created", "user", u.id)
    db.commit()
    return user_out(u)


class UserPatch(BaseModel):
    display_name: str | None = None
    role: str | None = Field(None, pattern="^(admin|member)$")
    is_active: bool | None = None


@router.patch("/users/{user_id}")
def update_user(user_id: uuid.UUID, body: UserPatch, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    u = get_owned(db, User, user_id, p.household_id, "User")
    if u.id == p.user.id and (body.is_active is False or body.role == "member"):
        raise HTTPException(409, "You can't remove your own administrator access")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(u, k, v)
    if body.is_active is False:
        for s in db.scalars(select(UserSession).where(UserSession.user_id == u.id)):
            s.revoked_at = utcnow()
    audit(db, p, "user.updated", "user", u.id, body.model_dump(exclude_unset=True))
    db.commit()
    return user_out(u)


# ---- household ---------------------------------------------------------------------------------------------------

class HouseholdPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    timezone: str | None = None


@router.patch("/household")
def update_household(body: HouseholdPatch, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    h = db.get(Household, p.household_id)
    if body.timezone:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(body.timezone)
        except ZoneInfoNotFoundError as e:
            raise HTTPException(422, "Unknown time zone") from e
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(h, k, v)
    db.commit()
    return {"id": sid(h.id), "name": h.name, "timezone": h.timezone}


# ---- people & pets -------------------------------------------------------------------------------------------------

class PersonIn(BaseModel):
    name: str = Field(max_length=80)
    kind: str = Field("adult", pattern="^(adult|child)$")
    birth_month: date | None = None
    allergies: list[str] = []
    dislikes: list[str] = []
    dietary_preferences: list[str] = []
    default_portion: float | None = Field(None, gt=0, le=5, description="Usual servings eaten; defaults to 1 for adults, 0.5 for children")
    notes: str | None = None
    colour: str | None = None


@router.get("/people")
def list_people(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(PersonProfile).where(PersonProfile.household_id == p.household_id).order_by(PersonProfile.kind, PersonProfile.name))
    # Tokens see people they may read intake for; planning tokens (plan:read) see everyone's profile, which planning
    # needs (allergies, preferences, usual portions) — but never intake unless the intake scopes also allow it.
    return [person_out(x) for x in rows if p.is_user or "plan:read" in p.scopes or p.can_read_person_intake(x)]


@router.post("/people", status_code=201)
def create_person(body: PersonIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    data = body.model_dump()
    data["default_portion"] = data["default_portion"] or (0.5 if body.kind == "child" else 1.0)
    person = PersonProfile(household_id=p.household_id, **data)
    db.add(person)
    audit(db, p, "person.created", "person", None, {"name": body.name, "kind": body.kind})
    db.commit()
    return person_out(person)


@router.patch("/people/{person_id}")
def update_person(person_id: uuid.UUID, body: dict, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    person = get_owned(db, PersonProfile, person_id, p.household_id, "Person")
    data = PersonIn(**{**person_out(person), **body}).model_dump()
    data["default_portion"] = data["default_portion"] or (0.5 if data["kind"] == "child" else 1.0)
    for k, v in data.items():
        setattr(person, k, v)
    db.commit()
    return person_out(person)


@router.delete("/people/{person_id}")
def delete_person(person_id: uuid.UUID, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """Deletes the profile and its intake history (personal data deletion)."""
    person = get_owned(db, PersonProfile, person_id, p.household_id, "Person")
    audit(db, p, "person.deleted", "person", person.id, {"name": person.name})
    db.delete(person)
    db.commit()
    return {"ok": True}


class PetIn(BaseModel):
    name: str = Field(max_length=80)
    species: str = "dog"
    feeding_plan: str | None = None
    feeding_plan_source: str | None = None
    notes: str | None = None


@router.get("/pets")
def list_pets(p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    return [pet_out(x) for x in db.scalars(select(PetProfile).where(PetProfile.household_id == p.household_id))]


@router.post("/pets", status_code=201)
def create_pet(body: PetIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    pet = PetProfile(household_id=p.household_id, **body.model_dump())
    db.add(pet)
    db.commit()
    return pet_out(pet)


@router.patch("/pets/{pet_id}")
def update_pet(pet_id: uuid.UUID, body: PetIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    pet = get_owned(db, PetProfile, pet_id, p.household_id, "Pet")
    for k, v in body.model_dump().items():
        setattr(pet, k, v)
    db.commit()
    return pet_out(pet)


# ---- API tokens ----------------------------------------------------------------------------------------------------

class TokenIn(BaseModel):
    name: str = Field(max_length=120)
    scopes: list[str]
    person_ids: list[uuid.UUID] = []
    expires_in_days: int | None = Field(None, ge=1, le=3650)


@router.get("/tokens/scopes")
def token_scopes(_: Principal = Depends(require_user)):
    return ALL_SCOPES


@router.get("/tokens")
def list_tokens(p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ApiToken).where(ApiToken.household_id == p.household_id).order_by(ApiToken.created_at.desc()))
    return [token_out(t) for t in rows]


def _issue(db: Session, p: Principal, body: TokenIn, rotated_from: uuid.UUID | None = None) -> dict:
    unknown = set(body.scopes) - set(ALL_SCOPES)
    if unknown:
        raise HTTPException(422, f"Unknown scopes: {', '.join(sorted(unknown))}")
    for pid in body.person_ids:
        get_owned(db, PersonProfile, pid, p.household_id, "Person")
    raw, prefix, h = generate_api_token()
    t = ApiToken(household_id=p.household_id, created_by_id=p.user_id, name=body.name, prefix=prefix, token_hash=h,
                 scopes=sorted(set(body.scopes)), person_ids=list(body.person_ids), rotated_from_id=rotated_from,
                 expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None)
    db.add(t)
    db.flush()
    audit(db, p, "token.created", "api_token", t.id, {"scopes": t.scopes, "rotated_from": sid(rotated_from)})
    db.commit()
    return {**token_out(t), "token": raw, "note": "Copy this token now; it is shown only once."}


@router.post("/tokens", status_code=201)
def create_token(body: TokenIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    return _issue(db, p, body)


@router.post("/tokens/{token_id}/rotate", status_code=201)
def rotate_token(token_id: uuid.UUID, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    old = get_owned(db, ApiToken, token_id, p.household_id, "Token")
    if old.revoked_at:
        raise HTTPException(409, "Token is already revoked")
    old.revoked_at = utcnow()
    days = None
    if old.expires_at:
        days = max(1, (old.expires_at - old.created_at).days)
    return _issue(db, p, TokenIn(name=old.name, scopes=old.scopes, person_ids=old.person_ids, expires_in_days=days), old.id)


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: uuid.UUID, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    t = get_owned(db, ApiToken, token_id, p.household_id, "Token")
    t.revoked_at = t.revoked_at or utcnow()
    audit(db, p, "token.revoked", "api_token", t.id)
    db.commit()
    return token_out(t)
