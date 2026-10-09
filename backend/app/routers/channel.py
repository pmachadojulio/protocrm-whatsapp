"""Canal WhatsApp: verificación + inbound (bot) + send (humano).

Bloque 1 (robustez de canal):
- payload de Meta se procesa como array de mensajes (no solo [0]),
  incluyendo statuses (delivered/read) que se responden ack rápido.
- tipos de media (image/audio/video/document/sticker/location/contacts/
  reaction/interactive/button) se persisten con message_type/media_url y
  se responde amable si llegan sin texto.
- idempotencia por wa_message_id (duplicados no re-procesan).
- rate-limit por teléfono (ventana 60s) y blocklist silenciosa.
- el trabajo de DB + bot corre en threadpool para no bloquear el event loop.
Mantiene el formato legacy {"from","name","text"} usado por tests y el mock.
"""
import hmac
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session as SASession
from starlette.concurrency import run_in_threadpool

from ..config import (WA_APP_SECRET, WA_BLOCKLIST, WA_MAX_MSGS_PER_MIN,
                      WA_SEND_KEY, WA_VERIFY_TOKEN)
from ..core import (display_name, greeting_text, handoff_activo, insert_message,
                    now_iso, open_ticket, route_message, set_handoff,
                    upsert_contact, upsert_conversation)
from ..deps import clean_name, clean_phone, clean_text, get_current_user, get_db
from ..models import Contact, Message, User
from ..security import verify_meta_signature

router = APIRouter()

MEDIA_LABEL = {"image": "imagen", "audio": "audio", "video": "video",
               "document": "documento", "sticker": "sticker",
               "location": "ubicación", "contacts": "contacto"}

# rate-limit por teléfono: deque de timestamps (monotónico) en la última ventana
_RATE: dict[str, deque] = defaultdict(deque)
_RATE_LOCK = __import__("threading").Lock()


def rate_allowed(phone: str) -> bool:
    """Ventana deslizante de WA_MAX_MSGS_PER_MIN mensajes por teléfono."""
    now = time.monotonic()
    with _RATE_LOCK:
        q = _RATE[phone]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= WA_MAX_MSGS_PER_MIN:
            return False
        q.append(now)
        return True


def _extract_message(m: dict) -> dict | None:
    """Normaliza un mensaje de Meta -> dict(phone,name,wa_id,text,mtype,media_url).
    Devuelve None para eventos sin contenido (reaction, statuses)."""
    if not isinstance(m, dict):
        return None
    phone = clean_phone(m.get("from"))
    if not phone:
        return None
    mtype = m.get("type") or "text"
    media_url = ""
    text = ""
    if mtype == "text":
        text = clean_text((m.get("text") or {}).get("body") or "")
    elif mtype in ("image", "video", "document"):
        node = m.get(mtype) or {}
        media_url = node.get("url") or ""
        text = clean_text(node.get("caption") or "")
    elif mtype == "audio":
        media_url = (m.get("audio") or {}).get("url") or ""
    elif mtype == "sticker":
        media_url = (m.get("sticker") or {}).get("url") or ""
    elif mtype == "location":
        loc = m.get("location") or {}
        text = clean_text(f"ubicacion {loc.get('latitude')},{loc.get('longitude')}")
    elif mtype == "contacts":
        for c in m.get("contacts") or []:
            nm = (c.get("name") or {}).get("formatted_name")
            nm = (c.get("name") or {}).get("first_name") if not nm else nm
            text = clean_text(f"contacto {nm}" if nm else "contacto")
            if text:
                break
    elif mtype == "button":
        text = clean_text((m.get("button") or {}).get("text") or "")
    elif mtype == "interactive":
        inter = m.get("interactive") or {}
        r = inter.get("button_reply") or inter.get("list_reply") or {}
        text = clean_text(r.get("title") or r.get("id") or "")
    elif mtype == "reaction":
        return None
    return {"phone": phone, "wa_id": m.get("id"), "mtype": mtype,
            "text": text, "media_url": media_url}


def parse_inbound(body: dict) -> tuple[list[dict], dict]:
    """Devuelve (mensajes, contacts_por_waid). Soportes Meta real y legacy."""
    msgs, profiles = [], {}
    if not isinstance(body, dict):
        return msgs, profiles
    if "entry" in body:
        for e in body.get("entry") or []:
            for ch in e.get("changes") or []:
                v = ch.get("value") or {}
                for c in v.get("contacts") or []:
                    if c.get("wa_id"):
                        profiles[c["wa_id"]] = clean_name(
                            (c.get("profile") or {}).get("name"))
                for m in v.get("messages") or []:
                    parsed = _extract_message(m)
                    if parsed:
                        parsed["name"] = profiles.get(parsed["phone"], "")
                        msgs.append(parsed)
    else:
        phone = clean_phone(body.get("from") or body.get("phone"))
        if phone:
            msgs.append({"phone": phone, "wa_id": body.get("id"),
                         "mtype": "text",
                         "text": clean_text(body.get("text") or body.get("body") or ""),
                         "media_url": "",
                         "name": clean_name(body.get("name"))})
    return msgs, profiles


def _reply_media(mtype: str) -> str:
    label = MEDIA_LABEL.get(mtype, "archivo")
    return (f"¡Recibimos tu {label}! Por ahora el asistente lee solo mensajes de "
            f"texto. Contanos en palabras qué necesitás y te ayudamos a la brevedad.")


def handle_inbound(db: SASession, body: dict) -> dict:
    """Procesa el payload entrante de WhatsApp; se ejecuta en threadpool.
    Hilos por mensaje: blocklist -> rate-limit -> idempotencia -> bot."""
    msgs, _ = parse_inbound(body)
    if not msgs:
        # statuses o payload sin mensajes: ack rápido, no procesar bot
        return {"ok": True, "ack": True, "statuses_only": True, "reply": None}

    last = None
    for msg in msgs:
        phone = msg["phone"]
        if phone in WA_BLOCKLIST:
            return {"ok": True, "blocked": True, "reply": None}

        contact = upsert_contact(db, phone, msg["name"])
        conv = upsert_conversation(db, contact.id)

        if not rate_allowed(phone):
            # guarda el mensaje para que el humano lo vea, pero el bot no responde
            read = msg["mtype"] != "text"
            insert_message(db, conv.id, "in",
                           msg["text"] or (f"[{msg['mtype']}]" if read else ""),
                           msg["wa_id"], mtype=msg["mtype"],
                           media_url=msg["media_url"] or None)
            conv.last_message_at = now_iso()
            conv.updated_at = now_iso()
            db.commit()
            continue

        text = msg["text"]
        media = bool(msg["media_url"])
        already = bool(msg["wa_id"] and db.query(Message).filter(
            Message.wa_message_id == msg["wa_id"]).first())
        insert_message(db, conv.id, "in", text or f"[{msg['mtype']}]",
                       msg["wa_id"], mtype=msg["mtype"],
                       media_url=msg["media_url"] or None)
        conv.last_message_at = now_iso()
        conv.updated_at = now_iso()
        db.commit()
        if already:
            continue  # duplicado: no re-procesar el bot

        if handoff_activo(db, conv.id):
            continue  # respondió un humano; no pisar la conversación

        # media sin texto: ida amable pidiendo texto (no entra al router)
        if media and not text:
            reply = _reply_media(msg["mtype"])
            insert_message(db, conv.id, "out", reply)
            last = {"ok": True, "reply": reply, "action": "media",
                    "intent": msg["mtype"], "confidence": 0.6, "suggestions": []}
            continue

        # Router conversacional: saludo abierto una vez, intención por
        # confianza, aclaración (máx 2) o derivación tibia al humano.
        route = route_message(db, text or "", conv)
        firsts = []
        if not conv.greeted:
            g = greeting_text(db)
            insert_message(db, conv.id, "out", g)
            firsts.append(g)
            conv.greeted = 1
            db.commit()

        def _touch(status=None, bot=1):
            conv.last_message_at = now_iso()
            conv.updated_at = now_iso()
            conv.bot_handled = bot
            if status:
                conv.status = status
            db.commit()

        if route["action"] == "human":
            set_handoff(db, conv.id, to_bot=False)
            summary = f"Derivación auto ({route['intent']}): {(text or '')[:120]}"
            open_ticket(db, contact.id, conv.id, summary, "alta")
            insert_message(db, conv.id, "out", route["reply"])
            _touch()
            last = {"ok": True, "reply": "\n".join(firsts + [route["reply"]]),
                    "handoff": True, "auto": True, "action": "human",
                    "intent": route["intent"], "suggestions": []}
            continue

        if route["action"] == "clarify":
            conv.clarify_count = (conv.clarify_count or 0) + 1
            _touch(status="bot", bot=1)
            insert_message(db, conv.id, "out", route["reply"])
            last = {"ok": True, "reply": "\n".join(firsts + [route["reply"]]),
                    "action": "clarify", "intent": route["intent"],
                    "confidence": route["confidence"],
                    "suggestions": route["suggestions"]}
            continue

        conv.clarify_count = 0  # se resolvió: reset de frustración
        _touch(status="bot", bot=1)
        insert_message(db, conv.id, "out", route["reply"])
        last = {"ok": True, "reply": "\n".join(firsts + [route["reply"]]),
                "action": "resolve", "intent": route["intent"],
                "confidence": route["confidence"],
                "suggestions": route["suggestions"]}

    return last or {"ok": True, "reply": None}


@router.get("/webhook/wa-inbound")
def inbound_verify(hub_mode: str = Query("", alias="hub.mode"),
                   hub_verify_token: str = Query("", alias="hub.verify_token"),
                   hub_challenge: str = Query("", alias="hub.challenge")):
    if hub_mode == "subscribe" and WA_VERIFY_TOKEN and \
            hmac.compare_digest(hub_verify_token, WA_VERIFY_TOKEN):
        return PlainTextResponse(hub_challenge)
    return JSONResponse({"ok": False, "error": "verify token invalido"}, 403)


@router.post("/webhook/wa-inbound")
async def inbound(request: Request, db: SASession = Depends(get_db)):
    raw = await request.body()
    if WA_APP_SECRET:
        if not verify_meta_signature(raw, request.headers.get("x-hub-signature-256") or "",
                                     WA_APP_SECRET):
            return JSONResponse({"ok": False, "error": "firma Meta invalida"}, 403)
    else:
        print("[aviso] WA_APP_SECRET sin configurar: aceptando wa-inbound sin firma (solo desarrollo)")
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await run_in_threadpool(handle_inbound, db, body)


@router.post("/webhook/wa-send")
def send(body: dict, request: Request,
         actor: User = Depends(get_current_user),
         db: SASession = Depends(get_db)):
    if WA_SEND_KEY:
        if not hmac.compare_digest(request.headers.get("x-api-key") or "", WA_SEND_KEY):
            return JSONResponse({"ok": False, "error": "X-Api-Key invalida"}, 401)
    else:
        print("[aviso] WA_SEND_KEY sin configurar: wa-send sin segunda cerradura (solo desarrollo)")
    to = clean_phone(body.get("to"))
    text = clean_text(body.get("text"))
    if not to or not text:
        return JSONResponse({"ok": False, "error": "faltan to/text"}, 400)
    row = db.query(Contact).filter(Contact.phone == to).first()
    contact = upsert_contact(db, to, None) if not row else row
    conv = upsert_conversation(db, contact.id)
    insert_message(db, conv.id, "out", text, created_by=actor.id)
    set_handoff(db, conv.id, to_bot=False)
    conv.assignee_id = actor.id
    conv.last_message_at = now_iso()
    conv.updated_at = now_iso()
    db.commit()
    print(f"[wa-send] {actor.display_name} -> {to}: {text[:60]}")
    return {"ok": True, "handoff": "human", "by": actor.display_name}
