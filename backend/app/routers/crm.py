"""Fase B en backend propio: oportunidades, timeline 360, notas, tickets."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session as SASession
import uuid

from ..config import NOTE_KINDS, PRIORITIES, STAGES, TICKET_STATUS
from ..core import display_name, now_iso, open_ticket, ticket_breached, upsert_contact
from ..deps import clean_phone, clean_text, get_current_user, get_db, page_params
from ..models import Contact, Conversation, Message, Note, Opportunity, Ticket, User

router = APIRouter()


def _contact_or_400(db: SASession, phone, contact_id):
    phone = clean_phone(phone) if phone else ""
    c = None
    if contact_id:
        c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c and phone:
        c = db.query(Contact).filter(Contact.phone == phone).first()
    if not c and phone:
        c = upsert_contact(db, phone, None)
    return c


@router.get("/webhook/opportunities")
def opp_list(stage: str = "", limit: int = 200, offset: int = 0, paged: str = "",
             user: User = Depends(get_current_user),
             db: SASession = Depends(get_db)):
    limit, offset = page_params(limit, offset, max_limit=500)
    q = db.query(Opportunity, Contact).join(Contact, Contact.id == Opportunity.contact_id)
    if stage in STAGES:
        q = q.filter(Opportunity.stage == stage)
    total = q.count()
    out = []
    for o, ct in q.order_by(desc(Opportunity.updated_at)).limit(limit).offset(offset).all():
        out.append({"id": o.id, "contact_id": o.contact_id,
                    "contact_name": ct.name or "Sin nombre", "phone": ct.phone,
                    "title": o.title, "amount": o.amount, "currency": o.currency,
                    "stage": o.stage, "notes": o.notes,
                    "created_by": o.created_by,
                    "created_by_name": display_name(db, o.created_by),
                    "created_at": o.created_at, "updated_at": o.updated_at,
                    "closed_at": o.closed_at})
    if paged == "1":
        return {"data": out, "total": total, "limit": limit, "offset": offset}
    return out


@router.post("/webhook/opportunities")
def opp_create(body: dict,
               actor: User = Depends(get_current_user),
               db: SASession = Depends(get_db)):
    c = _contact_or_400(db, body.get("phone"), body.get("contact_id"))
    if not c:
        return JSONResponse({"ok": False, "error": "falta phone/contact_id"}, 400)
    title = clean_text(body.get("title"), 200).strip()
    if not title:
        return JSONResponse({"ok": False, "error": "falta title"}, 400)
    try:
        amount = float(body.get("amount") or 0)
    except Exception:
        amount = 0
    stage = body.get("stage") or "nuevo"
    if stage not in STAGES:
        stage = "nuevo"
    now = now_iso()
    o = Opportunity(id=str(uuid.uuid4()), contact_id=c.id, title=title, amount=amount,
                    currency=(body.get("currency") or "ARS")[:8], stage=stage,
                    notes=clean_text(body.get("notes"), 1000),
                    created_by=actor.id, created_at=now, updated_at=now,
                    closed_at=now if stage in ("ganado", "perdido") else None)
    db.add(o)
    db.commit()
    print(f"[opp] {actor.display_name} creó '{title}' (${amount:g}) para {c.phone}")
    return {"ok": True, "id": o.id, "stage": stage}


@router.post("/webhook/opportunities/move")
def opp_move(body: dict,
             actor: User = Depends(get_current_user),
             db: SASession = Depends(get_db)):
    stage = body.get("stage") or ""
    if stage not in STAGES:
        return JSONResponse({"ok": False, "error": f"stage invalido (usa {STAGES})"}, 400)
    o = db.query(Opportunity).filter(Opportunity.id == (body.get("id") or "")).first()
    if not o:
        return JSONResponse({"ok": False, "error": "oportunidad no encontrada"}, 404)
    now = now_iso()
    o.stage = stage
    o.updated_at = now
    o.closed_at = now if stage in ("ganado", "perdido") else None
    db.commit()
    return {"ok": True, "id": o.id, "stage": stage}


@router.get("/webhook/timeline")
def timeline(phone: str = "", contact_id: str = "",
             user: User = Depends(get_current_user),
             db: SASession = Depends(get_db)):
    c = _contact_or_400(db, phone, contact_id)
    if not c and (phone or contact_id):
        return JSONResponse({"ok": False, "error": "contacto no encontrado"}, 404)
    if not c:
        return JSONResponse({"ok": False, "error": "falta phone/contact_id"}, 400)
    convs = db.query(Conversation).filter(Conversation.contact_id == c.id)\
        .order_by(desc(Conversation.last_message_at)).all()
    out_convs = []
    for cv in convs:
        msgs = db.query(Message).filter(Message.conversation_id == cv.id)\
            .order_by(Message.created_at).all()
        out_convs.append({
            "id": cv.id, "channel": cv.channel, "status": cv.status,
            "bot_handled": int(cv.bot_handled or 0),
            "last_message_at": cv.last_message_at, "created_at": cv.created_at,
            "messages": [{
                "id": m.id, "direction": m.direction, "message_type": m.message_type,
                "body": m.body, "created_at": m.created_at,
                "created_by_name": display_name(db, m.created_by)} for m in msgs]})
    notes = db.query(Note).filter(Note.contact_id == c.id)\
        .order_by(desc(Note.created_at)).all()
    opps = db.query(Opportunity).filter(Opportunity.contact_id == c.id)\
        .order_by(desc(Opportunity.updated_at)).all()
    ticks = db.query(Ticket).filter(Ticket.contact_id == c.id)\
        .order_by(desc(Ticket.created_at)).all()
    return {
        "contact": {"id": c.id, "phone": c.phone, "name": c.name or "",
                    "tags": c.tags or "[]"},
        "conversations": out_convs,
        "notes": [{"id": n.id, "kind": n.kind, "body": n.body, "due_at": n.due_at,
                   "done": int(n.done or 0), "created_by": n.created_by,
                   "created_at": n.created_at,
                   "created_by_name": display_name(db, n.created_by)} for n in notes],
        "opportunities": [{"id": o.id, "title": o.title, "amount": o.amount,
                           "currency": o.currency, "stage": o.stage,
                           "created_at": o.created_at, "updated_at": o.updated_at,
                           "closed_at": o.closed_at} for o in opps],
        "tickets": [{"id": t.id, "conversation_id": t.conversation_id,
                     "subject": t.subject, "priority": t.priority, "status": t.status,
                     "assignee_id": t.assignee_id, "sla_due": t.sla_due,
                     "created_at": t.created_at, "closed_at": t.closed_at,
                     "breached": ticket_breached(t.status, t.sla_due)} for t in ticks],
    }


@router.get("/webhook/notes")
def notes_list(phone: str = "", contact_id: str = "", pending: str = "",
               limit: int = 100, offset: int = 0,
               user: User = Depends(get_current_user),
               db: SASession = Depends(get_db)):
    c = _contact_or_400(db, phone, contact_id)
    if not c:
        return JSONResponse({"ok": False, "error": "contacto no encontrado"}, 404)
    limit, offset = page_params(limit, offset, max_limit=500)
    q = db.query(Note).filter(Note.contact_id == c.id)
    if pending == "1":
        q = q.filter(Note.done == 0)
    rows = q.order_by(desc(Note.created_at)).limit(limit).offset(offset).all()
    return [{"id": n.id, "contact_id": n.contact_id, "conversation_id": n.conversation_id,
             "kind": n.kind, "body": n.body, "due_at": n.due_at,
             "done": int(n.done or 0), "created_by": n.created_by,
             "created_at": n.created_at} for n in rows]


@router.post("/webhook/notes")
def notes_create(body: dict,
                 actor: User = Depends(get_current_user),
                 db: SASession = Depends(get_db)):
    c = _contact_or_400(db, body.get("phone"), body.get("contact_id"))
    if not c:
        return JSONResponse({"ok": False, "error": "falta phone/contact_id"}, 400)
    kind = body.get("kind") or "note"
    if kind not in NOTE_KINDS:
        kind = "note"
    txt = clean_text(body.get("body"), 2000).strip()
    if not txt:
        return JSONResponse({"ok": False, "error": "falta body"}, 400)
    n = Note(id=str(uuid.uuid4()), contact_id=c.id,
             conversation_id=body.get("conversation_id"), kind=kind, body=txt,
             due_at=body.get("due_at"), done=0,
             created_by=actor.id, created_at=now_iso())
    db.add(n)
    db.commit()
    return {"ok": True, "id": n.id}


@router.post("/webhook/notes/done")
def notes_done(body: dict,
               actor: User = Depends(get_current_user),
               db: SASession = Depends(get_db)):
    done = 0 if str(body.get("done")).lower() in ("0", "false", "no") else 1
    n = db.query(Note).filter(Note.id == (body.get("id") or "")).first()
    if not n:
        return JSONResponse({"ok": False, "error": "nota no encontrada"}, 404)
    n.done = done
    db.commit()
    return {"ok": True, "id": n.id, "done": done}


@router.get("/webhook/tickets")
def tickets_list(status: str = "", limit: int = 100, offset: int = 0,
                 user: User = Depends(get_current_user),
                 db: SASession = Depends(get_db)):
    limit, offset = page_params(limit, offset, max_limit=500)
    q = db.query(Ticket, Contact).outerjoin(Contact, Contact.id == Ticket.contact_id)
    if status in TICKET_STATUS:
        q = q.filter(Ticket.status == status)
    out = []
    for t, ct in q.order_by(desc(Ticket.created_at)).limit(limit).offset(offset).all():
        out.append({"id": t.id, "conversation_id": t.conversation_id,
                    "contact_id": t.contact_id,
                    "contact_name": (ct.name if ct and ct.name else ""),
                    "phone": ct.phone if ct else "",
                    "subject": t.subject, "priority": t.priority, "status": t.status,
                    "assignee_id": t.assignee_id,
                    "assignee_name": display_name(db, t.assignee_id),
                    "sla_due": t.sla_due, "created_at": t.created_at,
                    "closed_at": t.closed_at,
                    "breached": ticket_breached(t.status, t.sla_due)})
    return out


@router.post("/webhook/tickets")
def tickets_create(body: dict,
                   actor: User = Depends(get_current_user),
                   db: SASession = Depends(get_db)):
    conv_id = body.get("conversation_id") or ""
    c = _contact_or_400(db, body.get("phone"), body.get("contact_id"))
    if not c and conv_id:
        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        if conv:
            c = db.query(Contact).filter(Contact.id == conv.contact_id).first()
    if not c:
        return JSONResponse(
            {"ok": False, "error": "falta conversation_id/phone/contact_id"}, 400)
    prio = body.get("priority") or "normal"
    if prio not in PRIORITIES:
        prio = "normal"
    t = open_ticket(db, c.id, conv_id or None,
                    clean_text(body.get("subject"), 200) or "Sin asunto",
                    prio, body.get("assignee_id") or actor.id)
    print(f"[ticket] {actor.display_name} abrió ticket {prio}")
    return {"ok": True, "id": t.id, "sla_due": t.sla_due}


@router.post("/webhook/tickets/status")
def tickets_status(body: dict,
                   actor: User = Depends(get_current_user),
                   db: SASession = Depends(get_db)):
    status = body.get("status") or ""
    if status not in TICKET_STATUS:
        return JSONResponse(
            {"ok": False, "error": f"status invalido (usa {TICKET_STATUS})"}, 400)
    t = db.query(Ticket).filter(Ticket.id == (body.get("id") or "")).first()
    if not t:
        return JSONResponse({"ok": False, "error": "ticket no encontrado"}, 404)
    now = now_iso()
    t.status = status
    t.closed_at = now if status in ("resuelto", "cerrado") else None
    db.commit()
    return {"ok": True, "id": t.id, "status": status}
