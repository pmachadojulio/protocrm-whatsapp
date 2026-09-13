"""Auth: login/logout/me/users. JWT + tabla sessions (revocables)."""
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as SASession

from ..core import now_iso
from ..deps import get_current_user, get_db
from ..models import Session as SessionRow, User
from ..security import create_access_token, decode_token, hash_pw, is_legacy_hash, verify_pw

router = APIRouter()


def _pub(u: User) -> dict:
    return {"user_id": u.id, "username": u.username,
            "display_name": u.display_name, "role": u.role}


@router.post("/webhook/auth-login")
def login(body: dict, db: SASession = Depends(get_db)):
    username = (body.get("username") or "").strip()
    user = db.query(User).filter(User.username == username,
                                 User.active.is_(True)).first()
    if not user or not verify_pw(body.get("password") or "", user.pass_hash):
        return JSONResponse({"ok": False, "error": "credenciales invalidas"}, 401)
    if is_legacy_hash(user.pass_hash):
        user.pass_hash = hash_pw(body.get("password") or "")
        print(f"[seguridad] hash migrado a pbkdf2 para '{username}'")
    db.query(SessionRow).filter(SessionRow.user_id == user.id).delete()
    token, jti, exp = create_access_token(user.id)
    db.add(SessionRow(token=jti, user_id=user.id, created_at=now_iso(), exp=exp))
    db.commit()
    return {"ok": True, "token": token, "user": _pub(user)}


@router.post("/webhook/auth-logout")
def logout(request: Request, db: SASession = Depends(get_db)):
    tok = (request.headers.get("x-auth-token") or "")
    if not tok:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
    payload = decode_token(tok) if tok else None
    if payload:
        db.query(SessionRow).filter(SessionRow.token == payload.get("jti")).delete()
    db.query(SessionRow).filter(SessionRow.exp < int(time.time())).delete()
    db.commit()
    return {"ok": True}


@router.get("/webhook/auth-me")
def me(user: User = Depends(get_current_user)):
    return {"ok": True, "user": _pub(user)}


@router.get("/webhook/auth-users")
def users(user: User = Depends(get_current_user), db: SASession = Depends(get_db)):
    rows = db.query(User).order_by(User.username).all()
    return [{"username": u.username, "display_name": u.display_name,
             "role": u.role, "created_at": u.created_at} for u in rows]
