"""Lógica compartida: contactos/conversaciones, bot en capas + handoff inteligente."""
import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session as SASession

from . import rag
from .config import (EMPRESA_CTX, HANDOFF_TIMEOUT_MIN, LLM_MODEL,
                     MAX_BODY, OPENROUTER_API_KEY)
from .models import BotRule, Contact, Conversation, Message, Ticket, User

FALLBACK = "Hola, gracias por escribirnos. Un asesor te respondera a la brevedad."

# Fase D: pide humano o reclamo fuerte -> handoff automático + ticket
HUMAN_RE = re.compile(r"humano|persona real|asesor|agente|operador|representante|encargad[oa]|"
                      r"hablar con alguien|quiero hablar|alguien que|defensa del consumidor|"
                      r"libro de quejas|abogad|estafa|denuncia|reclamo formal", re.I)

GREETING_RE = re.compile(r"^(hola|buenas|buen dia|buenos dias|hello|hey|wacho|falopin|sos un bot)")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_name(db: SASession, user_id: str | None) -> str:
    if not user_id:
        return ""
    u = db.query(User).filter(User.id == user_id).first()
    return (u.display_name or u.username) if u else (user_id or "")


# ---------- contactos / conversaciones ----------
def upsert_contact(db: SASession, phone: str, name: str | None) -> Contact:
    c = db.query(Contact).filter(Contact.phone == phone).first()
    now = now_iso()
    if c:
        if name:
            c.name = name
        c.last_seen = now
        c.updated_at = now
    else:
        c = Contact(id=str(uuid.uuid4()), phone=phone, name=name or "Sin nombre",
                    created_at=now, updated_at=now, last_seen=now)
        db.add(c)
    db.commit()
    return c


def upsert_conversation(db: SASession, contact_id: str) -> Conversation:
    conv = db.query(Conversation).filter(
        Conversation.contact_id == contact_id,
        Conversation.channel == "whatsapp").first()
    now = now_iso()
    if conv:
        conv.last_message_at = now
        conv.updated_at = now
        if conv.status in ("closed", "reopened"):
            conv.status = "reopened"
    else:
        conv = Conversation(id=str(uuid.uuid4()), contact_id=contact_id, channel="whatsapp",
                            status="bot", bot_handled=1,
                            last_message_at=now, created_at=now, updated_at=now)
        db.add(conv)
    db.commit()
    return conv


def set_handoff(db: SASession, conv_id: str, to_bot: bool):
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        return
    now = now_iso()
    conv.bot_handled = 1 if to_bot else 0
    conv.status = "bot" if to_bot else "open"
    conv.updated_at = now
    conv.last_message_at = now
    db.commit()


def handoff_activo(db: SASession, conv_id: str) -> bool:
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv or conv.bot_handled == 1:
        return False
    last_out = db.query(Message).filter(
        Message.conversation_id == conv_id, Message.direction == "out"
    ).order_by(Message.created_at.desc()).first()
    if not last_out or not last_out.created_at:
        return True
    try:
        last = datetime.fromisoformat(last_out.created_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        mins = (datetime.now(timezone.utc) - last).total_seconds() / 60
        return mins < HANDOFF_TIMEOUT_MIN
    except Exception:
        return True


def insert_message(db: SASession, conv_id: str, direction: str, body: str,
                   wa_id: str | None = None, mtype: str = "text",
                   created_by: str | None = None) -> str:
    wa = wa_id or f"wamid.{uuid.uuid4().hex[:12]}"
    exists = db.query(Message).filter(Message.wa_message_id == wa).first()
    if exists:
        return wa
    db.add(Message(id=str(uuid.uuid4()), conversation_id=conv_id, direction=direction,
                   message_type=mtype, body=(body or "")[:MAX_BODY],
                   wa_message_id=wa, created_at=now_iso(), created_by=created_by))
    db.commit()
    return wa


def open_ticket(db: SASession, contact_id: str, conv_id: str | None, subject: str,
                priority: str, assignee_id: str | None = None) -> Ticket:
    from .config import SLA_HOURS
    t = Ticket(id=str(uuid.uuid4()), conversation_id=conv_id, contact_id=contact_id,
               subject=subject[:200], priority=priority, status="abierto",
               assignee_id=assignee_id,
               sla_due=datetime.fromtimestamp(
                   datetime.now(timezone.utc).timestamp() + SLA_HOURS[priority] * 3600,
                   tz=timezone.utc).isoformat(),
               created_at=now_iso())
    db.add(t)
    db.commit()
    return t


# ---------- bot ----------
def contexto_empresa_txt() -> str:
    e = EMPRESA_CTX
    return (f"Empresa: {e.get('nombre','')} | Rubro: {e.get('rubro','')} | "
            f"Horarios: {e.get('horarios','')} | Turnos: {e.get('turnos','')} | "
            f"Dirección: {e.get('direccion','')} | Tel: {e.get('telefono','')} | "
            f"Extras: {e.get('extras','')}")


def _llm_reply(text: str, conv_id: str, db: SASession) -> str | None:
    if not OPENROUTER_API_KEY:
        return None
    try:
        import urllib.request
        hist = db.query(Message).filter(Message.conversation_id == conv_id)\
            .order_by(Message.created_at.desc()).limit(8).all()
        hist = list(reversed(hist))
        kb = rag.context_block(text)
        system = (f"Sos el asistente de {EMPRESA_CTX.get('nombre','la empresa')} por WhatsApp. "
                  f"Contexto: {contexto_empresa_txt()}. Respondé en español rioplatense, "
                  f"breve (2-3 frases), amable. No inventes precios.")
        if kb:
            system += f" Datos internos relevantes:\n{kb}"
        msgs = [{"role": "system", "content": system}]
        for h in hist:
            msgs.append({"role": "user" if h.direction == "in" else "assistant",
                         "content": h.body or ""})
        msgs.append({"role": "user", "content": text})
        payload = json.dumps({"model": LLM_MODEL, "messages": msgs,
                              "max_tokens": 200, "temperature": 0.6}).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions", data=payload,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
            reply = (data["choices"][0]["message"]["content"] or "").strip()
            return reply or None
    except Exception as e:
        print(f"[openrouter] fallo, usando fallback: {e}")
        return None


def decide_reply(db: SASession, text: str, conv_id: str | None = None) -> str:
    """Capas: reglas SQL -> contexto empresa -> RAG extractivo -> LLM -> fallback."""
    t = (text or "").lower()
    rules = db.query(BotRule).filter(BotRule.active.is_(True))\
        .order_by(BotRule.priority, BotRule.id).all()
    for r in rules:
        kws = [k.strip().lower() for k in (r.keywords or "").split(",") if k.strip()]
        if any(k in t for k in kws):
            return r.response
    if any(k in t for k in ["horario", "turno", "cuando abren", "direccion", "donde quedan"]):
        return f"{EMPRESA_CTX.get('horarios','')} {EMPRESA_CTX.get('turnos','')}".strip()
    kb_hit = rag.extractive_answer(text)
    if kb_hit:
        return kb_hit
    if conv_id:
        llm = _llm_reply(text, conv_id, db)
        if llm:
            return llm
    if GREETING_RE.match(t):
        return FALLBACK
    return FALLBACK


def wants_human(text: str) -> bool:
    return bool(HUMAN_RE.search(text or ""))


def ticket_breached(status: str | None, sla_due: str | None) -> bool:
    if status in ("resuelto", "cerrado") or not sla_due:
        return False
    try:
        due = datetime.fromisoformat(sla_due)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > due
    except Exception:
        return False
