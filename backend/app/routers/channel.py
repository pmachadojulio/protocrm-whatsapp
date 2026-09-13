"""Canal WhatsApp: verificación + inbound (bot) + send (humano)."""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session as SASession

from ..config import WA_APP_SECRET, WA_SEND_KEY, WA_VERIFY_TOKEN
from ..core import (display_name, greeting_text, handoff_activo, insert_message,
                    now_iso, open_ticket, route_message, set_handoff,
                    upsert_contact, upsert_conversation)
from ..deps import clean_name, clean_phone, clean_text, get_current_user, get_db
from ..models import Contact, User
from ..security import verify_meta_signature
import hmac

router = APIRouter()


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
    phone = name = text = wa_id = None
    try:
        if "entry" in body:
            e = body["entry"][0]
            v = e["changes"][0]["value"]
            m = v["messages"][0]
            phone = clean_phone(m.get("from"))
            wa_id = m.get("id")
            text = clean_text((m.get("text") or {}).get("body")
                              or m.get("button", {}).get("text") or "")
            name = clean_name((v.get("contacts", [{}])[0].get("profile") or {}).get("name"))
        else:
            phone = clean_phone(body.get("from") or body.get("phone"))
            text = clean_text(body.get("text") or body.get("body") or "")
            name = clean_name(body.get("name"))
            wa_id = body.get("id")
    except Exception as e:
        print("parse error", e)
    if not phone or not text:
        return JSONResponse({"ok": False, "error": "faltan phone/text"}, 400)

    contact = upsert_contact(db, phone, name)
    conv = upsert_conversation(db, contact.id)
    insert_message(db, conv.id, "in", text, wa_id)
    conv.last_message_at = now_iso()
    conv.updated_at = now_iso()
    db.commit()

    if handoff_activo(db, conv.id):
        return {"ok": True, "reply": None, "handoff": True}

    # Router conversacional: saludo abierto una vez, intención por confianza,
    # aclaración conversacional (máx 2) o derivación tibia al humano.
    route = route_message(db, text, conv)
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
        return {"ok": True, "reply": "\n".join(firsts + [route["reply"]]),
                "handoff": True, "auto": True, "action": "human",
                "intent": route["intent"], "suggestions": []}

    if route["action"] == "clarify":
        conv.clarify_count = (conv.clarify_count or 0) + 1
        _touch(status="bot", bot=1)
        insert_message(db, conv.id, "out", route["reply"])
        return {"ok": True, "reply": "\n".join(firsts + [route["reply"]]),
                "action": "clarify", "intent": route["intent"],
                "confidence": route["confidence"],
                "suggestions": route["suggestions"]}

    conv.clarify_count = 0  # se resolvió: reset de frustración
    _touch(status="bot", bot=1)
    insert_message(db, conv.id, "out", route["reply"])
    return {"ok": True, "reply": "\n".join(firsts + [route["reply"]]),
            "action": "resolve", "intent": route["intent"],
            "confidence": route["confidence"],
            "suggestions": route["suggestions"]}


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
