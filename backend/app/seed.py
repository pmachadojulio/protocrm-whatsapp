"""Seed inicial (mismos usuarios/reglas/demo que el mock)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session as SASession

from .config import EMPRESA_CTX
from .models import BotRule, Contact, Conversation, Message, User
from .security import hash_pw

SEED_RULES = [
    ("horarios", "horario,abren,abierto,cierre,dias,feriado,turno,turnos",
     "Atendemos de lunes a viernes de 9 a 18 hs, sabados 9 a 13 hs. Sin turno previo.", 10),
    ("ventas", "precio,presupuesto,cotizacion,plan,contratar,cuanto,campera,cholo,river",
     "Te dejamos con un asesor de ventas a la brevedad.", 30),
    ("facturacion", "factura,facturacion,comprobante,recibo,pago",
     "Para temas de facturacion escribinos a facturacion@tuempresa.com", 20),
]
SEED_USERS = [
    ("matias", "matias123", "Matias", "admin"),
    ("asesor1", "1234", "Asesor 1", "agent"),
    ("asesor2", "1234", "Asesor 2", "agent"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_seed(db: SASession):
    if db.query(User).count() == 0:
        for u, p, disp, role in SEED_USERS:
            db.add(User(id=str(uuid.uuid4()), username=u, pass_hash=hash_pw(p),
                        display_name=disp, role=role, active=True, created_at=now_iso()))
        db.commit()
        print("[seed] users: matias / asesor1 / asesor2")
    if db.query(BotRule).count() == 0:
        for name, kw, resp, prio in SEED_RULES:
            db.add(BotRule(name=name, keywords=kw, response=resp, priority=prio, active=True))
        now = now_iso()
        cid, coid = str(uuid.uuid4()), str(uuid.uuid4())
        db.add(Contact(id=cid, phone="5491130001111", name="Cliente Demo",
                       created_at=now, updated_at=now, last_seen=now))
        db.add(Conversation(id=coid, contact_id=cid, channel="whatsapp", status="open",
                            bot_handled=1, last_message_at=now, created_at=now, updated_at=now))
        db.add(Message(id=str(uuid.uuid4()), conversation_id=coid, direction="in",
                       message_type="text", body="Hola, queria consultar por un presupuesto",
                       wa_message_id="wamid.demo1", created_at=now))
        db.add(Message(id=str(uuid.uuid4()), conversation_id=coid, direction="out",
                       message_type="text", body="Hola! Gracias por escribirnos. Como podemos ayudarte?",
                       wa_message_id="wamid.demo2", created_at=now))
        db.commit()
        print(f"[seed] demo ({EMPRESA_CTX.get('nombre', '')})")
