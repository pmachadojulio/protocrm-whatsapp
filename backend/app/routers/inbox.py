"""Inbox del agente: bandeja, historial, sugerencias, handoff, claim/close/reopen."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session as SASession

from ..core import display_name, now_iso, set_handoff
from ..deps import get_current_user, get_db, page_params
from ..models import Conversation, Contact, Message, User

router = APIRouter()


def _inbox_row(db: SASession, c: Conversation) -> dict:
    ct = db.query(Contact).filter(Contact.id == c.contact_id).first()
    last = db.query(Message).filter(Message.conversation_id == c.id)\
        .order_by(desc(Message.created_at)).first()
    return {
        "conversation_id": c.id,
        "contact_id": c.contact_id,
        "phone": ct.phone if ct else "",
        "contact_name": (ct.name if ct and ct.name else "Sin nombre"),
        "status": c.status,
        "bot_handled": int(c.bot_handled or 0),
        "assignee_id": c.assignee_id,
        "assignee_name": display_name(db, c.assignee_id),
        "last_message_at": c.last_message_at,
        "last_message": last.body if last else None,
        "last_direction": last.direction if last else None,
        "last_by": last.created_by if last else None,
    }


@router.get("/webhook/wa-inbox")
def inbox(limit: int = 50, offset: int = 0, paged: str = "",
          user: User = Depends(get_current_user),
          db: SASession = Depends(get_db)):
    limit, offset = page_params(limit, offset)
    total = db.query(func.count(Conversation.id)).scalar()
    convs = db.query(Conversation).order_by(desc(Conversation.last_message_at))\
        .limit(limit).offset(offset).all()
    rows = [_inbox_row(db, c) for c in convs]
    if paged == "1":
        return {"data": rows, "total": total, "limit": limit, "offset": offset}
    return rows


@router.get("/webhook/wa-msgs")
def msgs(conv_id: str = "", limit: int = 500,
         user: User = Depends(get_current_user),
         db: SASession = Depends(get_db)):
    if not conv_id:
        return []
    limit, _ = page_params(limit, 0, max_limit=2000)
    rows = db.query(Message).filter(Message.conversation_id == conv_id)\
        .order_by(Message.created_at).limit(limit).all()
    return [{
        "id": m.id, "conversation_id": m.conversation_id, "direction": m.direction,
        "message_type": m.message_type, "body": m.body, "media_url": m.media_url,
        "created_at": m.created_at, "created_by": m.created_by or "",
        "created_by_name": display_name(db, m.created_by),
    } for m in rows]


@router.get("/webhook/wa-suggest")
def suggest(conv_id: str = "",
            user: User = Depends(get_current_user),
            db: SASession = Depends(get_db)):
    suggestions = []
    if conv_id:
        last = db.query(Message).filter(Message.conversation_id == conv_id)\
            .order_by(desc(Message.created_at)).first()
        txt = ((last.body if last else "") or "").lower()
        if "precio" in txt or "presupuesto" in txt:
            suggestions = ["Te paso un presupuesto estimado en breve.",
                           "Podrias contarme mas detalles para cotizarte?",
                           "Gracias por tu consulta, te deriva un asesor comercial."]
        elif "horario" in txt:
            suggestions = ["Atendemos de lunes a viernes de 9 a 18 hs.",
                           "Podes pasar en ese horario sin turno previo.",
                           "Te agendo una visita si queres."]
        else:
            suggestions = ["Gracias por escribirnos, como podemos ayudarte?",
                           "Te derivo con un asesor a la brevedad.",
                           "Quedo atento a tu respuesta."]
    return {"suggestions": suggestions}


def _handoff(conv_id: str, action: str, db: SASession):
    if conv_id and action in ("bot", "human", "pausar", "reanudar"):
        set_handoff(db, conv_id, to_bot=action in ("bot", "reanudar"))
        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        return {"ok": True, "bot_handled": int(conv.bot_handled) if conv else 0}
    return JSONResponse({"ok": False, "error": "faltan conv_id/action"}, 400)


@router.get("/webhook/wa-handoff")
def handoff_get(conv_id: str = "", action: str = "",
                user: User = Depends(get_current_user),
                db: SASession = Depends(get_db)):
    return _handoff(conv_id, action, db)


@router.post("/webhook/wa-handoff")
def handoff_post(body: dict,
                 user: User = Depends(get_current_user),
                 db: SASession = Depends(get_db)):
    return _handoff(body.get("conv_id") or body.get("convId") or "",
                    body.get("action") or "", db)


def _claim(conv_id: str, actor: User, db: SASession):
    if not conv_id:
        return JSONResponse({"ok": False, "error": "falta conv_id"}, 400)
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        return JSONResponse({"ok": False, "error": "conversacion no encontrada"}, 404)
    if conv.assignee_id and conv.assignee_id != actor.id:
        return JSONResponse(
            {"ok": False, "error": f"ya atendida por {display_name(db, conv.assignee_id)}"}, 409)
    if conv.assignee_id == actor.id:
        return {"ok": True, "already": True}
    conv.assignee_id = actor.id
    conv.status = "open"
    conv.bot_handled = 0
    conv.updated_at = now_iso()
    conv.last_message_at = now_iso()
    db.commit()
    print(f"[claim] {actor.display_name} tomó {conv_id[:8]}")
    return {"ok": True}


@router.get("/webhook/conversations/claim")
def claim_get(conv_id: str = "", convId: str = "",
              actor: User = Depends(get_current_user),
              db: SASession = Depends(get_db)):
    return _claim(conv_id or convId, actor, db)


@router.post("/webhook/conversations/claim")
def claim_post(body: dict,
               actor: User = Depends(get_current_user),
               db: SASession = Depends(get_db)):
    return _claim(body.get("conv_id") or body.get("convId") or "", actor, db)


def _set_status(conv_id: str, status: str, to_bot: bool, db: SASession):
    if not conv_id:
        return JSONResponse({"ok": False, "error": "falta conv_id"}, 400)
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        return JSONResponse({"ok": False, "error": "no encontrada"}, 404)
    conv.status = status
    if to_bot:
        conv.bot_handled = 1
    conv.updated_at = now_iso()
    db.commit()
    return {"ok": True}


@router.get("/webhook/conversations/close")
def close_get(conv_id: str = "", convId: str = "",
              actor: User = Depends(get_current_user),
              db: SASession = Depends(get_db)):
    return _set_status(conv_id or convId, "closed", False, db)


@router.post("/webhook/conversations/close")
def close_post(body: dict,
               actor: User = Depends(get_current_user),
               db: SASession = Depends(get_db)):
    return _set_status(body.get("conv_id") or body.get("convId") or "",
                       "closed", False, db)


@router.get("/webhook/conversations/reopen")
def reopen_get(conv_id: str = "", convId: str = "",
               actor: User = Depends(get_current_user),
               db: SASession = Depends(get_db)):
    return _set_status(conv_id or convId, "open", True, db)


@router.post("/webhook/conversations/reopen")
def reopen_post(body: dict,
                actor: User = Depends(get_current_user),
                db: SASession = Depends(get_db)):
    return _set_status(body.get("conv_id") or body.get("convId") or "",
                       "open", True, db)
