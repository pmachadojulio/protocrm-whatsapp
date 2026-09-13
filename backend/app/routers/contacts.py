"""Contactos: lista + importación masiva."""
import json

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session as SASession

from ..config import IMPORT_MAX
from ..core import now_iso, upsert_contact
from ..deps import clean_name, clean_phone, clean_tags, get_current_user, get_db, page_params
from ..models import Contact, Conversation, Message, User

router = APIRouter()


@router.get("/webhook/contacts")
def contacts(limit: int = 200, offset: int = 0, paged: str = "",
             user: User = Depends(get_current_user),
             db: SASession = Depends(get_db)):
    limit, offset = page_params(limit, offset, max_limit=500)
    total = db.query(func.count(Contact.id)).scalar()
    rows = db.query(Contact).order_by(desc(Contact.last_seen)).limit(limit).offset(offset).all()
    out = []
    for c in rows:
        conv_ids = [r[0] for r in db.query(Conversation.id)
                    .filter(Conversation.contact_id == c.id).all()]
        msg_count = db.query(func.count(Message.id))\
            .filter(Message.conversation_id.in_(conv_ids)).scalar() if conv_ids else 0
        last = db.query(func.max(Message.created_at))\
            .filter(Message.conversation_id.in_(conv_ids)).scalar() if conv_ids else None
        out.append({"id": c.id, "phone": c.phone, "name": c.name or "",
                    "tags": c.tags or "[]", "last_seen": c.last_seen,
                    "created_at": c.created_at, "conv_count": len(conv_ids),
                    "msg_count": msg_count, "last_interaction": last})
    if paged == "1":
        return {"data": out, "total": total, "limit": limit, "offset": offset}
    return out


@router.post("/webhook/contacts/import")
def contacts_import(body: dict,
                    user: User = Depends(get_current_user),
                    db: SASession = Depends(get_db)):
    arr = body.get("contacts") or body.get("data") or []
    if isinstance(body, list):
        arr = body
    if not isinstance(arr, list) or not arr:
        return JSONResponse({"ok": False, "error": "envia {contacts:[{phone,name}]}"}, 400)
    if len(arr) > IMPORT_MAX:
        return JSONResponse({"ok": False, "error": f"maximo {IMPORT_MAX} por lote"}, 400)
    ok, errs = 0, []
    for it in arr:
        phone = clean_phone((it or {}).get("phone"))
        name = clean_name((it or {}).get("name"))
        if not phone:
            errs.append(str(it)[:80])
            continue
        tags = clean_tags((it or {}).get("tags") or [])
        try:
            c = upsert_contact(db, phone, name or None)
            if tags:
                try:
                    cur = json.loads(c.tags) if c.tags else []
                except Exception:
                    cur = []
                merged = cur + [t for t in tags if t not in cur]
                c.tags = json.dumps(merged, ensure_ascii=False)
                c.updated_at = now_iso()
                db.commit()
            ok += 1
        except Exception as e:
            errs.append(f"{phone}: {e}")
    return {"ok": True, "imported": ok, "errors": errs[:10]}
