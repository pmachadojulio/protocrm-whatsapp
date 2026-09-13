"""Dependencias FastAPI: sesión DB + usuario autenticado (JWT + tabla sessions)."""
import re
import time

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session as SASession

from .db import SessionLocal
from .config import (MAX_BODY, MAX_NAME, MAX_TAG_LEN, MAX_TAGS,
                     MAX_PHONE_DIGITS, MIN_PHONE_DIGITS)
from .models import Session as SessionRow, User
from .security import decode_token


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _token_from_headers(x_auth_token: str | None = Header(default=None),
                        authorization: str | None = Header(default=None)) -> str:
    tok = (x_auth_token or "").strip()
    if not tok and authorization and authorization.lower().startswith("bearer "):
        tok = authorization[7:].strip()
    return tok


def get_current_user(token: str = Depends(_token_from_headers),
                     db: SASession = Depends(get_db)) -> User:
    if not token:
        raise HTTPException(401, "no autenticado")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(401, "no autenticado")
    row = db.query(SessionRow).filter(SessionRow.token == payload.get("jti")).first()
    if not row or (row.exp or 0) < int(time.time()):
        if row:
            db.delete(row)
            db.commit()
        raise HTTPException(401, "no autenticado")
    user = db.query(User).filter(User.id == row.user_id, User.active.is_(True)).first()
    if not user:
        raise HTTPException(401, "no autenticado")
    return user


def clean_phone(v) -> str:
    digits = re.sub(r"[^0-9]", "", str(v or ""))
    if len(digits) < MIN_PHONE_DIGITS or len(digits) > MAX_PHONE_DIGITS:
        return ""
    return digits


def clean_text(v, maxlen: int = MAX_BODY) -> str:
    return str(v or "")[:maxlen]


def clean_name(v) -> str:
    return str(v or "").strip()[:MAX_NAME]


def clean_tags(v) -> list:
    if isinstance(v, str):
        v = [t.strip() for t in v.split(",") if t.strip()]
    if not isinstance(v, list):
        return []
    out = []
    for t in v[:MAX_TAGS]:
        t = str(t).strip()[:MAX_TAG_LEN]
        if t and t not in out:
            out.append(t)
    return out


def page_params(limit: int = 50, offset: int = 0, max_limit: int = 200):
    return max(1, min(limit, max_limit)), max(0, offset)
