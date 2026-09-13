"""Hashing pbkdf2 (compatible con el mock, incluye migración de sha256 legacy) + JWT."""
import hashlib
import hmac
import re
import secrets
import time
import uuid

import jwt

from .config import JWT_SECRET, SESSION_DAYS, PBKDF2_ITER

ALG = "HS256"


def hash_pw(p: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", p.encode(), bytes.fromhex(salt), PBKDF2_ITER)
    return f"pbkdf2${PBKDF2_ITER}${salt}${dk.hex()}"


def is_legacy_hash(h: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", h or ""))


def verify_pw(p: str, stored: str) -> bool:
    if not stored:
        return False
    if is_legacy_hash(stored):
        return hmac.compare_digest(hashlib.sha256(p.encode()).hexdigest(), stored)
    try:
        _, it, salt, want = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", p.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), want)
    except Exception:
        return False


def create_access_token(user_id: str) -> tuple[str, str, int]:
    """Devuelve (token, jti, exp). El jti se persiste en sessions para poder revocar."""
    jti = uuid.uuid4().hex
    exp = int(time.time()) + SESSION_DAYS * 86400
    token = jwt.encode({"sub": user_id, "jti": jti, "exp": exp}, JWT_SECRET, algorithm=ALG)
    return token, jti, exp


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALG])
    except Exception:
        return None


def verify_meta_signature(raw: bytes, sig_header: str, app_secret: str) -> bool:
    if not app_secret or not sig_header or not sig_header.startswith("sha256="):
        return False
    calc = hmac.new(app_secret.encode(), raw or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(calc, sig_header[7:])
