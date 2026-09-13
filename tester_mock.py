#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tester mock sin Docker — imita los 4 webhooks de n8n con SQLite.
Uso: python tester_mock.py  (escucha en http://localhost:5678)
Luego abrir index.html con Base n8n = http://localhost:5678
"""
import json, sqlite3, uuid, re, hashlib, hmac, secrets, time, os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "mock.db")
PORT = 5678
HANDOFF_TIMEOUT_MIN = 30  # minutos que el bot queda pausado tras hablar un humano

# --- límites anti-abuso ---
MAX_BODY = 4000
MAX_NAME = 120
MAX_PHONE_DIGITS = 20
MIN_PHONE_DIGITS = 7
MAX_TAGS = 20
MAX_TAG_LEN = 40
IMPORT_MAX = 2000

def load_env():
    """Carga .env a os.environ (sin pisar variables ya definidas). No imprime valores."""
    try:
        with open(os.path.join(BASE_DIR, ".env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except FileNotFoundError:
        pass

load_env()

def cfg(name, default=""):
    return (os.environ.get(name) or default).strip()

WA_VERIFY_TOKEN = cfg("WA_VERIFY_TOKEN")
WA_SEND_KEY = cfg("WA_SEND_KEY")
WA_APP_SECRET = cfg("WA_APP_SECRET")
try:
    SESSION_DAYS = max(1, int(cfg("SESSION_DAYS", "7")))
except:
    SESSION_DAYS = 7
PBKDF2_ITER = 200_000

# Contexto de empresa para respuestas IA (editable en empresa_contexto.json)
EMPRESA_CTX = {}
try:
    with open(os.path.join(os.path.dirname(__file__), "empresa_contexto.json"), encoding="utf-8") as f:
        EMPRESA_CTX = json.load(f)
except:
    EMPRESA_CTX = {"nombre":"Tu Empresa","horarios":"Lunes a viernes 9 a 18 hs","turnos":"Sin turno, por orden de llegada"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
  id TEXT PRIMARY KEY, phone TEXT UNIQUE NOT NULL, name TEXT,
  tags TEXT DEFAULT '[]', created_at TEXT, updated_at TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, contact_id TEXT NOT NULL, channel TEXT DEFAULT 'whatsapp',
  status TEXT DEFAULT 'open', assignee_id TEXT, bot_handled INTEGER DEFAULT 0,
  last_message_at TEXT, created_at TEXT, updated_at TEXT,
  UNIQUE(contact_id, channel),
  FOREIGN KEY(contact_id) REFERENCES contacts(id)
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, direction TEXT NOT NULL,
  message_type TEXT DEFAULT 'text', body TEXT, media_url TEXT,
  wa_message_id TEXT UNIQUE, status TEXT DEFAULT 'sent', created_at TEXT, created_by TEXT,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);
CREATE TABLE IF NOT EXISTS bot_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, keywords TEXT, response TEXT, priority INTEGER DEFAULT 100, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS intents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, keywords TEXT NOT NULL,
  kind TEXT DEFAULT 'auto', label TEXT DEFAULT '', priority INTEGER DEFAULT 100, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, pass_hash TEXT NOT NULL, display_name TEXT, role TEXT DEFAULT 'agent', created_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT, exp INTEGER,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS opportunities (
  id TEXT PRIMARY KEY, contact_id TEXT NOT NULL, title TEXT NOT NULL,
  amount REAL DEFAULT 0, currency TEXT DEFAULT 'ARS',
  stage TEXT DEFAULT 'nuevo', notes TEXT DEFAULT '',
  created_by TEXT, created_at TEXT, updated_at TEXT, closed_at TEXT,
  FOREIGN KEY(contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY, contact_id TEXT NOT NULL, conversation_id TEXT,
  kind TEXT DEFAULT 'note', body TEXT NOT NULL,
  due_at TEXT, done INTEGER DEFAULT 0,
  created_by TEXT, created_at TEXT,
  FOREIGN KEY(contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, conversation_id TEXT, contact_id TEXT,
  subject TEXT, priority TEXT DEFAULT 'normal', status TEXT DEFAULT 'abierto',
  assignee_id TEXT, sla_due TEXT, created_at TEXT, closed_at TEXT
);
"""

SEED_RULES = [
    ("horarios", "horario,abren,abierto,cierre,dias,feriado,turno,turnos", "Atendemos de lunes a viernes de 9 a 18 hs, sabados 9 a 13 hs. Sin turno previo.", 10),
    ("ventas", "precio,presupuesto,cotizacion,plan,contratar,cuanto,campera,cholo,river", "Te dejamos con un asesor de ventas a la brevedad.", 30),
    ("facturacion", "factura,facturacion,comprobante,recibo,pago", "Para temas de facturacion escribinos a facturacion@tuempresa.com", 20),
]
SEED_USERS = [
    ("matias", "matias123", "Matias", "admin"),
    ("asesor1", "1234", "Asesor 1", "agent"),
    ("asesor2", "1234", "Asesor 2", "agent"),
]
# Router conversacional: auto = lo resuelve el bot; human = deriva (nunca se ofrece de entrada)
SEED_INTENTS = [
    ("saludo", "hola,buenas,buen dia,buenos dias,hello,hey", "auto", "Saludar", 5),
    ("horarios", "horario,abren,abierto,cierre,dias,feriado,turno,turnos,direccion,donde quedan,cuando abren", "auto", "Horarios", 10),
    ("precios", "precio,presupuesto,cotizacion,cuanto,campera,cholo,costo,vale,oferta,descuento", "auto", "Precios", 20),
    ("factura", "factura,facturacion,comprobante,recibo,pago", "auto", "Factura", 30),
    ("cambios", "cambio,devolucion,defecto,falla,garantia,roto,anda mal", "auto", "Cambios", 40),
    ("reclamo", "reclamo,estafa,denuncia,abogado,defensa del consumidor,libro de quejas,formal", "human", "", 50),
    ("humano", "humano,persona real,asesor,agente,operador,representante,encargado,hablar con alguien,quiero hablar,alguien que,persona", "human", "", 60),
]
MAX_CLARIFY = 2
def hash_pw(p):
    """PBKDF2-HMAC-SHA256 con salt aleatoria. Formato: pbkdf2$iter$salt_hex$hash_hex"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", p.encode(), bytes.fromhex(salt), PBKDF2_ITER)
    return f"pbkdf2${PBKDF2_ITER}${salt}${dk.hex()}"

def _is_legacy_hash(h):
    return bool(re.fullmatch(r"[0-9a-f]{64}", h or ""))

def verify_pw(p, stored):
    if not stored:
        return False
    if _is_legacy_hash(stored):
        return hmac.compare_digest(hashlib.sha256(p.encode()).hexdigest(), stored)
    try:
        _, it, salt, want = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", p.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), want)
    except:
        return False

def check_user(username, password):
    con = get_db()
    row = con.execute("SELECT id, username, pass_hash, display_name, role FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        con.close()
        return None
    if not verify_pw(password, row["pass_hash"]):
        con.close()
        return None
    user = dict(row)
    # migración transparente: hash legacy sha256 -> pbkdf2 en el próximo login válido
    if _is_legacy_hash(row["pass_hash"]):
        con.execute("UPDATE users SET pass_hash=? WHERE id=?", (hash_pw(password), row["id"]))
        con.commit()
        print(f"[seguridad] hash migrado a pbkdf2 para '{username}'")
    con.close()
    return user

def create_session(user):
    token = secrets.token_hex(32)
    exp = int(time.time()) + SESSION_DAYS * 86400
    con = get_db()
    con.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))  # una sesión activa por usuario
    con.execute("INSERT INTO sessions (token, user_id, created_at, exp) VALUES (?,?,?,?)",
                (token, user["id"], now_iso(), exp))
    con.commit(); con.close()
    return token

def destroy_session(token):
    if not token:
        return
    con = get_db()
    con.execute("DELETE FROM sessions WHERE token=?", (token,))
    con.execute("DELETE FROM sessions WHERE exp < ?", (int(time.time()),))  # purga expiradas
    con.commit(); con.close()

def _token_from_headers(handler):
    tok = handler.headers.get("X-Auth-Token") or handler.headers.get("x-auth-token") or ""
    if not tok:
        auth = handler.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
    return (tok or "").strip()

def get_user_from_req(handler):
    # header X-Auth-Token o Authorization: Bearer <token>, validado contra tabla sessions
    tok = _token_from_headers(handler)
    if not tok:
        return None
    con = get_db()
    row = con.execute("SELECT s.exp, u.id, u.username, u.display_name, u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (tok,)).fetchone()
    if not row:
        con.close()
        return None
    if (row["exp"] or 0) < int(time.time()):
        con.execute("DELETE FROM sessions WHERE token=?", (tok,))
        con.commit(); con.close()
        return None
    u = {"user_id": row["id"], "username": row["username"], "display_name": row["display_name"], "role": row["role"]}
    con.close()
    return u

def get_db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def migrate_add_column():
    con = get_db()
    try:
        con.execute("SELECT created_by FROM messages LIMIT 1")
    except:
        con.execute("ALTER TABLE messages ADD COLUMN created_by TEXT")
        con.commit()
    for col in ("ALTER TABLE conversations ADD COLUMN greeted INTEGER DEFAULT 0",
                "ALTER TABLE conversations ADD COLUMN clarify_count INTEGER DEFAULT 0"):
        try:
            con.execute(col)
            con.commit()
        except:
            pass
    con.close()

def init_db():
    con = get_db()
    con.executescript(SCHEMA)
    migrate_add_column()
    # users seed
    cur = con.execute("SELECT COUNT(*) as c FROM users")
    if cur.fetchone()["c"] == 0:
        for u, p, disp, role in SEED_USERS:
            con.execute("INSERT INTO users (id, username, pass_hash, display_name, role, created_at) VALUES (?,?,?,?,?,?)",
                        (str(uuid.uuid4()), u, hash_pw(p), disp, role, now_iso()))
        con.commit()
        print(f"[seed] users: {', '.join([u for u,_,_,_ in SEED_USERS])} / pass matias123 / 1234")
    cur = con.execute("SELECT COUNT(*) as c FROM bot_rules")
    if cur.fetchone()["c"] == 0:
        con.executemany("INSERT INTO bot_rules (name,keywords,response,priority) VALUES (?,?,?,?)", SEED_RULES)
        con.commit()
    cur = con.execute("SELECT COUNT(*) as c FROM intents")
    if cur.fetchone()["c"] == 0:
        con.executemany("INSERT INTO intents (name,keywords,kind,label,priority) VALUES (?,?,?,?,?)", SEED_INTENTS)
        con.commit()
        print("[seed] intents del router conversacional")
    cur = con.execute("SELECT COUNT(*) as c FROM contacts WHERE phone='5491130001111'")
    if cur.fetchone()["c"] == 0:
        # demo contact + conversation
        cid = str(uuid.uuid4()); coid = str(uuid.uuid4()); mid1 = str(uuid.uuid4()); mid2 = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        con.execute("INSERT INTO contacts (id,phone,name,created_at,updated_at,last_seen) VALUES (?,?,?,?,?,?)",
                    (cid, "5491130001111", "Cliente Demo", now, now, now))
        con.execute("INSERT INTO conversations (id,contact_id,channel,status,bot_handled,last_message_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (coid, cid, "whatsapp", "open", 1, now, now, now))
        con.execute("INSERT INTO messages (id,conversation_id,direction,message_type,body,wa_message_id,created_at) VALUES (?,?,?,?,?,?,?)",
                    (mid1, coid, "in", "text", "Hola, queria consultar por un presupuesto", "wamid.demo1", now))
        con.execute("INSERT INTO messages (id,conversation_id,direction,message_type,body,wa_message_id,created_at) VALUES (?,?,?,?,?,?,?)",
                    (mid2, coid, "out", "text", "Hola! Gracias por escribirnos. Como podemos ayudarte?", "wamid.demo2", now))
        con.commit()
        print(f"[seed] Demo: {cid[:8]} / conv {coid[:8]}")
        # pipeline demo para la vista kanban
        con.execute("INSERT INTO opportunities (id,contact_id,title,amount,currency,stage,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), cid, "Presupuesto camperas x10", 250000, "ARS", "cotizado", "Seed demo", now, now))
        con.execute("INSERT INTO opportunities (id,contact_id,title,amount,currency,stage,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), cid, "Reposición gorros", 60000, "ARS", "nuevo", "Seed demo", now, now))
        con.commit()
    con.close()

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def handoff_activo(conv_id):
    """True si un humano tomó la conversación y aún no venció el timeout"""
    con = get_db()
    row = con.execute("SELECT bot_handled, last_message_at FROM conversations WHERE id=?", (conv_id,)).fetchone()
    if not row:
        con.close()
        return False
    if row["bot_handled"] == 1:
        con.close()
        return False
    # bot_handled == 0 → humano a cargo. Revisar timeout
    # si hace mucho que no hay mensaje del humano, devolver al bot
    last_out = con.execute("SELECT created_at FROM messages WHERE conversation_id=? AND direction='out' ORDER BY created_at DESC LIMIT 1", (conv_id,)).fetchone()
    con.close()
    if not last_out or not last_out["created_at"]:
        return True
    try:
        last = datetime.fromisoformat(last_out["created_at"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        mins = (datetime.now(timezone.utc) - last).total_seconds() / 60
        return mins < HANDOFF_TIMEOUT_MIN
    except:
        return True

def contexto_empresa_txt():
    e = EMPRESA_CTX
    return f"Empresa: {e.get('nombre','')} | Rubro: {e.get('rubro','')} | Horarios: {e.get('horarios','')} | Turnos: {e.get('turnos','')} | Dirección: {e.get('direccion','')} | Tel: {e.get('telefono','')} | Extras: {e.get('extras','')}"

HUMAN_RE = re.compile(r"humano|persona real|asesor|agente|operador|representante|encargad[oa]|"
                      r"hablar con alguien|quiero hablar|alguien que|defensa del consumidor|"
                      r"libro de quejas|abogad|estafa|denuncia|reclamo formal", re.I)
SLA_HOURS = {"critica": 2, "alta": 8, "normal": 24, "baja": 72}

def insert_ticket(conv_id, contact_id, subject, priority="normal"):
    from datetime import timedelta
    prio = priority if priority in SLA_HOURS else "normal"
    now = now_iso()
    sla_due = (datetime.now(timezone.utc) + timedelta(hours=SLA_HOURS[prio])).isoformat()
    con = get_db()
    tid = str(uuid.uuid4())
    con.execute("INSERT INTO tickets (id,conversation_id,contact_id,subject,priority,status,sla_due,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (tid, conv_id, contact_id, (subject or "Sin asunto")[:200], prio, "abierto", sla_due, now))
    con.commit(); con.close()
    return tid

def match_intent(text, kind=None):
    t = (text or "").lower()
    con = get_db()
    q = "SELECT name, keywords, kind, label FROM intents WHERE active=1"
    args = []
    if kind:
        q += " AND kind=?"
        args.append(kind)
    q += " ORDER BY priority, id"
    rows = list(con.execute(q, args).fetchall())
    con.close()
    for r in rows:
        kws = [k.strip().lower() for k in (r["keywords"] or "").split(",") if k.strip()]
        if any(k in t for k in kws):
            return dict(r)
    return None

def suggestion_labels(k=3):
    con = get_db()
    rows = con.execute("SELECT label FROM intents WHERE active=1 AND kind='auto' AND label<>'' ORDER BY priority, id LIMIT ?", (k,)).fetchall()
    con.close()
    return [r["label"] for r in rows]

def greeting_text():
    return f"¡Hola! Soy el asistente de {EMPRESA_CTX.get('nombre','la empresa')} 😊 ¿En qué te puedo ayudar hoy?"

def clarify_text():
    labels = suggestion_labels()
    hint = f" Puedo ayudarte con: {', '.join(labels)}." if labels else ""
    return ("Quiero darte la respuesta justa — ¿me contás un poco más con tus "
            f"palabras?{hint}")

def answer_with_conf(text, conv_id=None):
    """(respuesta, confianza, intent): reglas .95 -> empresa .8 -> LLM .65 -> fallback .2"""
    t = (text or "").lower()
    # 1) reglas SQL
    con = get_db()
    rows = list(con.execute("SELECT name,keywords,response FROM bot_rules WHERE active=1 ORDER BY priority, id").fetchall())
    con.close()
    for r in rows:
        kws = [k.strip().lower() for k in r["keywords"].split(",") if k.strip()]
        if any(k in t for k in kws):
            return r["response"], 0.95, r["name"]
    # 2) si hay contexto de empresa, usarlo para horarios/turnos antes de fallback genérico
    if any(k in t for k in ["horario","turno","cuando abren","direccion","donde quedan"]):
        return f"{EMPRESA_CTX.get('horarios','')} {EMPRESA_CTX.get('turnos','')}".strip()
    # 3) intento LLM via OpenRouter si hay key (mismo comportamiento que n8n)
    # os.environ manda (tests pueden forzar "" para modo offline); si no existe, se lee .env
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key is None:
        api_key = ""
        try:
            with open(os.path.join(os.path.dirname(__file__), ".env"), encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("OPENROUTER_API_KEY="):
                        api_key = line.strip().split("=",1)[1].strip().strip('"').strip("'")
                        break
        except: pass
    if api_key and conv_id:
        try:
            # armar historial corto
            con = get_db()
            hist = list(con.execute("SELECT direction, body FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 8", (conv_id,)).fetchall())
            con.close()
            hist = list(reversed(hist))
            msgs = [{"role":"system","content": f"Sos el asistente de {EMPRESA_CTX.get('nombre','la empresa')} por WhatsApp. Contexto: {contexto_empresa_txt()}. Respondé en español rioplatense, breve (2-3 frases), amable. No inventes precios."}]
            for h in hist:
                msgs.append({"role":"user" if h["direction"]=="in" else "assistant", "content": h["body"]})
            msgs.append({"role":"user","content": text})
            import urllib.request
            payload = json.dumps({"model": os.environ.get("LLM_MODEL") or "meta-llama/llama-3.3-70b-instruct:free","messages": msgs, "max_tokens": 200, "temperature": 0.6}).encode()
            req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=payload, headers={"Authorization": f"Bearer {api_key}", "Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode())
                reply = data["choices"][0]["message"]["content"].strip()
                if reply:
                    return reply
        except Exception as e:
            print(f"[openrouter] fallo, usando fallback: {e}")
    # 4) fallback genérico
    return "Hola, gracias por escribirnos. Un asesor te respondera a la brevedad.", 0.2, "fallback"

def decide_reply(text, conv_id=None):
    """Compat: solo texto (el router completo es route_message)."""
    return answer_with_conf(text, conv_id)[0]

def route_message(text, conv_id):
    """Router conversacional: human (intención o frustración) | clarify | resolve."""
    suggestions = suggestion_labels()
    human_it = match_intent(text, kind="human")
    if human_it or HUMAN_RE.search(text or ""):
        return {"action": "human", "intent": human_it["name"] if human_it else "humano",
                "confidence": 0.95,
                "reply": "Te derivo con un asesor humano a la brevedad, gracias por tu paciencia.",
                "suggestions": []}
    reply, conf, iname = answer_with_conf(text, conv_id)
    if conf >= 0.6:
        return {"action": "resolve", "intent": iname, "confidence": conf,
                "reply": reply, "suggestions": suggestions}
    con = get_db()
    row = con.execute("SELECT COALESCE(clarify_count,0) as n FROM conversations WHERE id=?", (conv_id,)).fetchone()
    con.close()
    if (row["n"] if row else 0) >= MAX_CLARIFY:
        return {"action": "human", "intent": "frustracion", "confidence": 0.5,
                "reply": "Te derivo con un asesor humano para resolverlo bien, gracias por tu paciencia.",
                "suggestions": []}
    return {"action": "clarify", "intent": iname, "confidence": conf,
            "reply": clarify_text(), "suggestions": suggestions}

def upsert_contact(phone, name):
    con = get_db()
    row = con.execute("SELECT id FROM contacts WHERE phone=?", (phone,)).fetchone()
    nid = now_iso()
    if row:
        cid = row["id"]
        if name:
            con.execute("UPDATE contacts SET name=?, last_seen=?, updated_at=? WHERE id=?", (name, nid, nid, cid))
        else:
            con.execute("UPDATE contacts SET last_seen=?, updated_at=? WHERE id=?", (nid, nid, cid))
        con.commit()
    else:
        cid = str(uuid.uuid4())
        con.execute("INSERT INTO contacts (id,phone,name,created_at,updated_at,last_seen) VALUES (?,?,?,?,?,?)",
                    (cid, phone, name or "Sin nombre", nid, nid, nid))
        con.commit()
    con.close()
    return cid

def upsert_conversation(contact_id):
    con = get_db()
    row = con.execute("SELECT id FROM conversations WHERE contact_id=? AND channel='whatsapp'", (contact_id,)).fetchone()
    nid = now_iso()
    if row:
        coid = row["id"]
        # no pisar bot_handled; solo actualizar timestamps y status si estaba cerrada
        con.execute("UPDATE conversations SET last_message_at=?, updated_at=?, status=CASE WHEN status IN ('closed','reopened') THEN 'reopened' ELSE status END WHERE id=?", (nid, nid, coid))
        con.commit()
    else:
        coid = str(uuid.uuid4())
        con.execute("INSERT INTO conversations (id,contact_id,channel,status,bot_handled,last_message_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (coid, contact_id, "whatsapp", "bot", 1, nid, nid, nid))
        con.commit()
    con.close()
    return coid

def set_handoff(conv_id, to_bot: bool):
    con = get_db()
    nid = now_iso()
    con.execute("UPDATE conversations SET bot_handled=?, updated_at=?, last_message_at=?, status=? WHERE id=?",
                (1 if to_bot else 0, nid, nid, 'bot' if to_bot else 'open', conv_id))
    con.commit(); con.close()

def insert_message(conv_id, direction, body, wa_id=None, mtype="text", created_by=None):
    con = get_db()
    mid = str(uuid.uuid4())
    wa = wa_id or f"wamid.{uuid.uuid4().hex[:12]}"
    try:
        con.execute("INSERT INTO messages (id,conversation_id,direction,message_type,body,wa_message_id,created_at,created_by) VALUES (?,?,?,?,?,?,?,?)",
                    (mid, conv_id, direction, mtype, body, wa, now_iso(), created_by))
        con.commit()
    except sqlite3.IntegrityError:
        pass
    con.close()
    return wa

# ---------- Fase A: sanitización, paginación y firma Meta ----------
def clean_phone(v):
    digits = re.sub(r"[^0-9]", "", str(v or ""))
    if len(digits) < MIN_PHONE_DIGITS or len(digits) > MAX_PHONE_DIGITS:
        return ""
    return digits

def clean_text(v, maxlen=MAX_BODY):
    return str(v or "")[:maxlen]

def clean_name(v):
    return str(v or "").strip()[:MAX_NAME]

def clean_tags(v):
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

def page_params(qs, default_limit=50, max_limit=200):
    try:
        limit = int((qs.get("limit") or [default_limit])[0])
    except:
        limit = default_limit
    try:
        offset = int((qs.get("offset") or [0])[0])
    except:
        offset = 0
    return max(1, min(limit, max_limit)), max(0, offset)

def maybe_paged(qs, rows, total, limit, offset):
    """?paged=1 devuelve {data,total,limit,offset}; si no, array plano (compatibilidad)."""
    if (qs.get("paged") or [""])[0] == "1":
        return {"data": rows, "total": total, "limit": limit, "offset": offset}
    return rows

def verify_meta_signature(raw, sig_header):
    """Valida X-Hub-Signature-256 (sha256=<hex>) contra WA_APP_SECRET."""
    if not WA_APP_SECRET or not sig_header:
        return False
    if not sig_header.startswith("sha256="):
        return False
    calc = hmac.new(WA_APP_SECRET.encode(), raw or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(calc, sig_header[7:])

# ---------- Fase B: pipeline, notas y tickets ----------
STAGES = ["nuevo", "contactado", "cotizado", "ganado", "perdido"]
OPEN_STAGES = ["nuevo", "contactado", "cotizado"]
PRIORITIES = ["baja", "normal", "alta", "critica"]
TICKET_STATUS = ["abierto", "en_progreso", "resuelto", "cerrado"]
SLA_HOURS = {"critica": 2, "alta": 8, "normal": 24, "baja": 72}
NOTE_KINDS = ["note", "task", "call", "visit"]

def resolve_contact(phone=None, contact_id=None):
    """Devuelve dict del contacto o None. Si hay phone nuevo, lo crea (upsert)."""
    phone = clean_phone(phone) if phone else ""
    con = get_db()
    row = None
    if contact_id:
        row = con.execute("SELECT id, phone, COALESCE(name,'') as name, tags FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not row and phone:
        row = con.execute("SELECT id, phone, COALESCE(name,'') as name, tags FROM contacts WHERE phone=?", (phone,)).fetchone()
    con.close()
    if row:
        return dict(row)
    if phone:
        cid = upsert_contact(phone, None)
        return {"id": cid, "phone": phone, "name": "Sin nombre", "tags": "[]"}
    return None

def ticket_breached(status, sla_due):
    if status in ("resuelto", "cerrado") or not sla_due:
        return False
    try:
        due = datetime.fromisoformat(sla_due)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > due
    except:
        return False

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format%args}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Api-Key, X-Auth-Token, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        path = parsed.path
        if path in ("/webhook/auth-me", "/webhook/auth-me/"):
            u = get_user_from_req(self)
            if not u: self._json({"ok": False}, 401); return
            self._json({"ok": True, "user": {k: u[k] for k in ("user_id","username","display_name","role")}})
            return
        elif path in ("/webhook/auth-users", "/webhook/auth-users/"):
            u = get_user_from_req(self)
            if not u: self._json({"ok": False}, 401); return
            con = get_db(); rows = con.execute("SELECT username, display_name, role, created_at FROM users ORDER BY username").fetchall(); con.close()
            self._json([dict(r) for r in rows])
            return
        elif path in ("/webhook/wa-inbox", "/webhook/wa-inbox/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            limit, offset = page_params(qs, 50, 200)
            try:
                con = get_db()
                total = con.execute("SELECT COUNT(*) as c FROM conversations").fetchone()["c"]
                rows = con.execute("""
                  SELECT c.id as conversation_id, ct.id as contact_id, ct.phone, COALESCE(ct.name,'Sin nombre') as contact_name,
                         c.status, c.bot_handled, c.assignee_id, COALESCE(u.display_name,'') as assignee_name,
                         c.last_message_at,
                         (SELECT body FROM messages m WHERE m.conversation_id=c.id ORDER BY created_at DESC LIMIT 1) as last_message,
                         (SELECT direction FROM messages m WHERE m.conversation_id=c.id ORDER BY created_at DESC LIMIT 1) as last_direction,
                         (SELECT created_by FROM messages m WHERE m.conversation_id=c.id ORDER BY created_at DESC LIMIT 1) as last_by
                  FROM conversations c JOIN contacts ct ON ct.id=c.contact_id LEFT JOIN users u ON u.id=c.assignee_id
                  ORDER BY c.last_message_at DESC LIMIT ? OFFSET ?
                """, (limit, offset)).fetchall()
                con.close()
                self._json(maybe_paged(qs, [dict(r) for r in rows], total, limit, offset))
            except Exception as e:
                print(f"[wa-inbox] error: {e}")
                try: con.close()
                except: pass
                self._json({"error": str(e), "hint": "borra mock.db y reinicia el tester"}, 500)
        elif path in ("/webhook/wa-msgs", "/webhook/wa-msgs/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            conv_id = (qs.get("conv_id") or [""])[0]
            if not conv_id:
                self._json([])
                return
            limit, _ = page_params(qs, 500, 2000)
            con = get_db()
            rows = con.execute("SELECT id, conversation_id, direction, message_type, body, media_url, created_at, COALESCE(created_by,'') as created_by, COALESCE((SELECT display_name FROM users WHERE id=created_by), created_by) as created_by_name FROM messages WHERE conversation_id=? ORDER BY created_at LIMIT ?", (conv_id, limit)).fetchall()
            con.close()
            self._json([dict(r) for r in rows])
        elif path in ("/webhook/wa-suggest", "/webhook/wa-suggest/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            conv_id = (qs.get("conv_id") or [""])[0]
            # sugerencias estaticas si no hay OpenRouter; genera 3 variantes basadas en ultimo mensaje
            suggestions = []
            if conv_id:
                con = get_db()
                last = con.execute("SELECT body FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1", (conv_id,)).fetchone()
                con.close()
                txt = (last["body"] if last else "").lower() if last else ""
                if "precio" in txt or "presupuesto" in txt:
                    suggestions = ["Te paso un presupuesto estimado en breve.", "Podrias contarme mas detalles para cotizarte?", "Gracias por tu consulta, te deriva un asesor comercial."]
                elif "horario" in txt:
                    suggestions = ["Atendemos de lunes a viernes de 9 a 18 hs.", "Podes pasar en ese horario sin turno previo.", "Te agendo una visita si queres."]
                else:
                    suggestions = ["Gracias por escribirnos, como podemos ayudarte?", "Te derivo con un asesor a la brevedad.", "Quedo atento a tu respuesta."]
            self._json({"suggestions": suggestions})
        elif path in ("/webhook/contacts", "/webhook/contacts/"):
            # lista de contactos con última interacción (requiere login)
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            limit, offset = page_params(qs, 200, 500)
            try:
                con = get_db()
                total = con.execute("SELECT COUNT(*) as c FROM contacts").fetchone()["c"]
                rows = con.execute("""
                  SELECT c.id, c.phone, COALESCE(c.name,'') as name, c.tags, c.last_seen, c.created_at,
                         (SELECT COUNT(*) FROM conversations conv WHERE conv.contact_id=c.id) as conv_count,
                         (SELECT COUNT(*) FROM messages m JOIN conversations conv ON conv.id=m.conversation_id WHERE conv.contact_id=c.id) as msg_count,
                         (SELECT MAX(m.created_at) FROM messages m JOIN conversations conv ON conv.id=m.conversation_id WHERE conv.contact_id=c.id) as last_interaction
                  FROM contacts c ORDER BY last_interaction DESC NULLS LAST, c.created_at DESC LIMIT ? OFFSET ?
                """, (limit, offset)).fetchall()
                con.close()
                # tags viene como JSON string '[]', lo dejamos tal cual
                self._json(maybe_paged(qs, [dict(r) for r in rows], total, limit, offset))
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        elif path in ("/webhook/wa-handoff", "/webhook/wa-handoff/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            conv_id = (qs.get("conv_id") or qs.get("convId") or [""])[0]
            action = (qs.get("action") or [""])[0]
            if conv_id and action in ("bot","human","pausar","reanudar"):
                to_bot = action in ("bot","reanudar")
                set_handoff(conv_id, to_bot)
                self._json({"ok": True, "bot_handled": 1 if to_bot else 0})
            else:
                self._json({"ok": False, "error": "faltan conv_id/action"}, 400)
        elif path in ("/webhook/conversations/claim", "/webhook/conversations/claim/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            conv_id = (qs.get("conv_id") or qs.get("convId") or [""])[0]
            if not conv_id: self._json({"ok": False, "error": "falta conv_id"}, 400); return
            con = get_db()
            row = con.execute("SELECT id, assignee_id, status FROM conversations WHERE id=?", (conv_id,)).fetchone()
            if not row: con.close(); self._json({"ok": False, "error": "conversacion no encontrada"}, 404); return
            if row["assignee_id"] and row["assignee_id"] != actor["user_id"]:
                # ya atendida por otro
                other = con.execute("SELECT display_name FROM users WHERE id=?", (row["assignee_id"],)).fetchone()
                con.close()
                self._json({"ok": False, "error": f"ya atendida por {other['display_name'] if other else 'otro agente'}"}, 409); return
            if row["assignee_id"] == actor["user_id"]:
                con.close(); self._json({"ok": True, "already": True}); return
            con.execute("UPDATE conversations SET assignee_id=?, status='open', bot_handled=0, updated_at=?, last_message_at=? WHERE id=?", (actor["user_id"], now_iso(), now_iso(), conv_id))
            con.commit(); con.close()
            print(f"[claim] {actor['display_name']} tomó {conv_id[:8]}")
            self._json({"ok": True})
            return
        elif path in ("/webhook/conversations/close", "/webhook/conversations/close/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            conv_id = (qs.get("conv_id") or qs.get("convId") or [""])[0]
            if not conv_id: self._json({"ok": False, "error": "falta conv_id"}, 400); return
            con = get_db()
            con.execute("UPDATE conversations SET status='closed', updated_at=? WHERE id=?", (now_iso(), conv_id))
            con.commit(); con.close()
            self._json({"ok": True})
            return
        elif path in ("/webhook/conversations/reopen", "/webhook/conversations/reopen/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            conv_id = (qs.get("conv_id") or qs.get("convId") or [""])[0]
            if not conv_id: self._json({"ok": False, "error": "falta conv_id"}, 400); return
            con = get_db()
            con.execute("UPDATE conversations SET status='open', bot_handled=1, updated_at=? WHERE id=?", (now_iso(), conv_id))
            con.commit(); con.close()
            self._json({"ok": True})
            return
        elif path in ("/webhook/opportunities", "/webhook/opportunities/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            stage = (qs.get("stage") or [""])[0]
            limit, offset = page_params(qs, 200, 500)
            con = get_db()
            q = """SELECT o.id, o.contact_id, COALESCE(ct.name,'Sin nombre') as contact_name, ct.phone,
                          o.title, o.amount, o.currency, o.stage, o.notes, o.created_by, o.created_at, o.updated_at, o.closed_at,
                          COALESCE((SELECT display_name FROM users WHERE id=o.created_by), '') as created_by_name
                   FROM opportunities o JOIN contacts ct ON ct.id=o.contact_id"""
            args = []
            if stage in STAGES:
                q += " WHERE o.stage=?"
                args.append(stage)
            q += " ORDER BY o.updated_at DESC LIMIT ? OFFSET ?"
            args += [limit, offset]
            rows = con.execute(q, args).fetchall()
            total = con.execute("SELECT COUNT(*) as c FROM opportunities" + (" WHERE stage=?" if stage in STAGES else ""), ([stage] if stage in STAGES else [])).fetchone()["c"]
            con.close()
            self._json(maybe_paged(qs, [dict(r) for r in rows], total, limit, offset))
            return
        elif path in ("/webhook/timeline", "/webhook/timeline/"):
            # ficha 360 del contacto: datos + conversaciones/mensajes + notas + oportunidades + tickets
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            c = resolve_contact((qs.get("phone") or [""])[0], (qs.get("contact_id") or [""])[0])
            if not c: self._json({"ok": False, "error": "contacto no encontrado"}, 404); return
            con = get_db()
            convs = con.execute("SELECT id, channel, status, bot_handled, last_message_at, created_at FROM conversations WHERE contact_id=? ORDER BY last_message_at DESC", (c["id"],)).fetchall()
            out_convs = []
            for cv in convs:
                msgs = con.execute("SELECT id, direction, message_type, body, created_at, COALESCE((SELECT display_name FROM users WHERE id=created_by), created_by, '') as created_by_name FROM messages WHERE conversation_id=? ORDER BY created_at", (cv["id"],)).fetchall()
                d = dict(cv); d["messages"] = [dict(m) for m in msgs]
                out_convs.append(d)
            notes = con.execute("SELECT id, kind, body, due_at, done, created_by, created_at, COALESCE((SELECT display_name FROM users WHERE id=created_by),'') as created_by_name FROM notes WHERE contact_id=? ORDER BY created_at DESC", (c["id"],)).fetchall()
            opps = con.execute("SELECT id, title, amount, currency, stage, notes, created_at, updated_at, closed_at FROM opportunities WHERE contact_id=? ORDER BY updated_at DESC", (c["id"],)).fetchall()
            ticks = con.execute("SELECT id, conversation_id, subject, priority, status, assignee_id, sla_due, created_at, closed_at FROM tickets WHERE contact_id=? ORDER BY created_at DESC", (c["id"],)).fetchall()
            con.close()
            ticks = [dict(t) for t in ticks]
            for t in ticks:
                t["breached"] = ticket_breached(t["status"], t["sla_due"])
            self._json({"contact": c, "conversations": out_convs,
                        "notes": [dict(n) for n in notes],
                        "opportunities": [dict(o) for o in opps],
                        "tickets": ticks})
            return
        elif path in ("/webhook/notes", "/webhook/notes/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            c = resolve_contact((qs.get("phone") or [""])[0], (qs.get("contact_id") or [""])[0])
            if not c: self._json({"ok": False, "error": "contacto no encontrado"}, 404); return
            pending = (qs.get("pending") or [""])[0] == "1"
            limit, offset = page_params(qs, 100, 500)
            con = get_db()
            q = "SELECT id, contact_id, conversation_id, kind, body, due_at, done, created_by, created_at FROM notes WHERE contact_id=?"
            args = [c["id"]]
            if pending:
                q += " AND done=0"
            q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            args += [limit, offset]
            rows = con.execute(q, args).fetchall()
            con.close()
            self._json([dict(r) for r in rows])
            return
        elif path in ("/webhook/tickets", "/webhook/tickets/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            status = (qs.get("status") or [""])[0]
            limit, offset = page_params(qs, 100, 500)
            con = get_db()
            q = """SELECT t.id, t.conversation_id, t.contact_id, COALESCE(ct.name,'') as contact_name, ct.phone,
                          t.subject, t.priority, t.status, t.assignee_id,
                          COALESCE((SELECT display_name FROM users WHERE id=t.assignee_id),'') as assignee_name,
                          t.sla_due, t.created_at, t.closed_at
                   FROM tickets t LEFT JOIN contacts ct ON ct.id=t.contact_id"""
            args = []
            if status in TICKET_STATUS:
                q += " WHERE t.status=?"
                args.append(status)
            q += " ORDER BY t.created_at DESC LIMIT ? OFFSET ?"
            args += [limit, offset]
            rows = [dict(r) for r in con.execute(q, args).fetchall()]
            con.close()
            for t in rows:
                t["breached"] = ticket_breached(t["status"], t["sla_due"])
            self._json(rows)
            return
        elif path in ("/webhook/wa-inbound", "/webhook/wa-inbound/"):
            # verificación del webhook de Meta: GET ?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...
            mode = (qs.get("hub.mode") or [""])[0]
            token = (qs.get("hub.verify_token") or [""])[0]
            challenge = (qs.get("hub.challenge") or [""])[0]
            if mode == "subscribe" and WA_VERIFY_TOKEN and hmac.compare_digest(token, WA_VERIFY_TOKEN):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(challenge.encode())
            else:
                self._json({"ok": False, "error": "verify token invalido"}, 403)
            return
        elif path == "/health":
            self._json({"ok": True})
        else:
            self.send_response(404); self.send_header("Access-Control-Allow-Origin","*"); self.end_headers()
            self.wfile.write(b'{"error":"not found"}')

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except:
            body = {}
        if path in ("/webhook/auth-login", "/webhook/auth-login/"):
            username = (body.get("username") or "").strip()
            password = body.get("password") or ""
            user = check_user(username, password) if username else None
            if not user:
                self._json({"ok": False, "error": "credenciales invalidas"}, 401); return
            tok = create_session(user)
            self._json({"ok": True, "token": tok, "user": {"user_id": user["id"], "username": user["username"], "display_name": user["display_name"], "role": user["role"]}})
            return
        elif path in ("/webhook/auth-logout", "/webhook/auth-logout/"):
            destroy_session(_token_from_headers(self))
            self._json({"ok": True}); return
        elif path in ("/webhook/wa-inbound", "/webhook/wa-inbound/"):
            # firma Meta: si hay WA_APP_SECRET configurado, exigir X-Hub-Signature-256 válida
            if WA_APP_SECRET:
                if not verify_meta_signature(raw, self.headers.get("X-Hub-Signature-256") or ""):
                    self._json({"ok": False, "error": "firma Meta invalida"}, 403); return
            else:
                print("[aviso] WA_APP_SECRET sin configurar: aceptando wa-inbound sin verificar firma (solo desarrollo)")
            # soporta payload Meta real o payload simple {from, text, name}
            phone = name = text = wa_id = None
            try:
                if "entry" in body:
                    e = body["entry"][0]; ch = e["changes"][0]; v = ch["value"]
                    m = v["messages"][0]
                    phone = clean_phone(m.get("from"))
                    wa_id = m.get("id")
                    text = clean_text((m.get("text") or {}).get("body") or m.get("button",{}).get("text") or "")
                    name = clean_name((v.get("contacts",[{}])[0].get("profile") or {}).get("name"))
                else:
                    phone = clean_phone(body.get("from") or body.get("phone"))
                    text = clean_text(body.get("text") or body.get("body") or "")
                    name = clean_name(body.get("name"))
                    wa_id = body.get("id")
            except Exception as e:
                print("parse error", e)
            if not phone or not text:
                self._json({"ok": False, "error": "faltan phone/text"}, 400); return
            cid = upsert_contact(phone, name)
            coid = upsert_conversation(cid)
            insert_message(coid, "in", text, wa_id)
            # si hay handoff humano activo, NO responde el bot
            if handoff_activo(coid):
                con = get_db(); con.execute("UPDATE conversations SET last_message_at=?, updated_at=? WHERE id=?", (now_iso(), now_iso(), coid)); con.commit(); con.close()
                self._json({"ok": True, "reply": None, "handoff": True})
                return
            route = route_message(text, coid)
            # saludo abierto una sola vez por conversación
            firsts = []
            con = get_db()
            g = con.execute("SELECT COALESCE(greeted,0) as g FROM conversations WHERE id=?", (coid,)).fetchone()
            if g and not g["g"]:
                gm = greeting_text()
                con.execute("UPDATE conversations SET greeted=1 WHERE id=?", (coid,))
                con.commit()
                con.close()
                insert_message(coid, "out", gm)
                firsts.append(gm)
            else:
                con.close()
            if route["action"] == "human":
                set_handoff(coid, to_bot=False)
                insert_ticket(coid, cid, f"Derivación auto ({route['intent']}): {(text or '')[:120]}", "alta")
                insert_message(coid, "out", route["reply"])
                self._json({"ok": True, "reply": "\n".join(firsts + [route["reply"]]),
                            "handoff": True, "auto": True, "action": "human",
                            "intent": route["intent"], "suggestions": []})
                return
            # actualizar y marcar que respondió el bot
            con = get_db()
            if route["action"] == "clarify":
                con.execute("UPDATE conversations SET clarify_count=COALESCE(clarify_count,0)+1, last_message_at=?, bot_handled=1, updated_at=?, status='bot' WHERE id=?", (now_iso(), now_iso(), coid))
            else:
                con.execute("UPDATE conversations SET clarify_count=0, last_message_at=?, bot_handled=1, updated_at=?, status='bot' WHERE id=?", (now_iso(), now_iso(), coid))
            con.commit(); con.close()
            insert_message(coid, "out", route["reply"])
            self._json({"ok": True, "reply": "\n".join(firsts + [route["reply"]]),
                        "action": route["action"], "intent": route["intent"],
                        "confidence": route["confidence"], "suggestions": route["suggestions"]})
        elif path in ("/webhook/wa-send", "/webhook/wa-send/"):
            # validar login si hay usuarios (multi-usuario)
            actor = get_user_from_req(self)
            # si hay al menos un usuario en DB, exigir login para trazar quien respondio
            con = get_db(); has_users = con.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"] > 0; con.close()
            if has_users and not actor:
                self._json({"ok": False, "error": "no autenticado - hace login primero"}, 401); return
            # WA_SEND_KEY: segunda cerradura del endpoint de envío (igual que en n8n)
            if WA_SEND_KEY:
                given = (self.headers.get("X-Api-Key") or self.headers.get("x-api-key") or "")
                if not hmac.compare_digest(given, WA_SEND_KEY):
                    self._json({"ok": False, "error": "X-Api-Key invalida"}, 401); return
            else:
                print("[aviso] WA_SEND_KEY sin configurar: wa-send sin segunda cerradura (solo desarrollo)")
            to = clean_phone(body.get("to"))
            text = clean_text(body.get("text"))
            if not to or not text:
                self._json({"ok": False, "error": "faltan to/text"}, 400); return
            phone = to
            con = get_db()
            row = con.execute("SELECT id FROM contacts WHERE phone=?", (phone,)).fetchone()
            con.close()
            if not row:
                cid = upsert_contact(phone, None)
                coid = upsert_conversation(cid)
            else:
                coid = upsert_conversation(row["id"])
            by = actor["user_id"] if actor else None
            by_name = actor["display_name"] if actor else "sistema"
            insert_message(coid, "out", text, created_by=by)
            # humano tomó la conversación → pausar bot y asignar
            set_handoff(coid, to_bot=False)
            if by:
                con = get_db(); con.execute("UPDATE conversations SET assignee_id=?, updated_at=?, last_message_at=? WHERE id=?", (by, now_iso(), now_iso(), coid)); con.commit(); con.close()
            print(f"[wa-send] {by_name} -> {phone}: {text[:60]}")
            self._json({"ok": True, "handoff": "human", "by": by_name})
        elif path in ("/webhook/wa-handoff", "/webhook/wa-handoff/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            conv_id = body.get("conv_id") or body.get("convId") or ""
            action = body.get("action") or ""
            if conv_id and action in ("bot","human","pausar","reanudar"):
                to_bot = action in ("bot","reanudar")
                set_handoff(conv_id, to_bot)
                self._json({"ok": True, "bot_handled": 1 if to_bot else 0})
            else:
                self._json({"ok": False, "error": "faltan conv_id/action"}, 400)
        elif path in ("/webhook/conversations/claim", "/webhook/conversations/claim/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            conv_id = body.get("conv_id") or body.get("convId") or ""
            if not conv_id: self._json({"ok": False, "error": "falta conv_id"}, 400); return
            con = get_db()
            row = con.execute("SELECT id, assignee_id FROM conversations WHERE id=?", (conv_id,)).fetchone()
            if not row: con.close(); self._json({"ok": False, "error": "no encontrada"}, 404); return
            if row["assignee_id"] and row["assignee_id"] != actor["user_id"]:
                other = con.execute("SELECT display_name FROM users WHERE id=?", (row["assignee_id"],)).fetchone()
                con.close(); self._json({"ok": False, "error": f"ya atendida por {other['display_name'] if other else 'otro'}"}, 409); return
            con.execute("UPDATE conversations SET assignee_id=?, status='open', bot_handled=0, updated_at=?, last_message_at=? WHERE id=?", (actor["user_id"], now_iso(), now_iso(), conv_id))
            con.commit(); con.close()
            print(f"[claim POST] {actor['display_name']} tomó {conv_id[:8]}")
            self._json({"ok": True}); return
        elif path in ("/webhook/conversations/close", "/webhook/conversations/close/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            conv_id = body.get("conv_id") or body.get("convId") or ""
            if not conv_id: self._json({"ok": False, "error": "falta conv_id"}, 400); return
            con = get_db(); con.execute("UPDATE conversations SET status='closed', updated_at=? WHERE id=?", (now_iso(), conv_id)); con.commit(); con.close()
            self._json({"ok": True}); return
        elif path in ("/webhook/contacts/import", "/webhook/contacts/import/"):
            # bulk import: {contacts:[{phone,name,tags,custom_fields}]}
            actor = get_user_from_req(self)
            if not actor:
                self._json({"ok": False, "error": "no autenticado"}, 401); return
            arr = body.get("contacts") or body.get("data") or []
            if isinstance(body, list): arr = body
            if not isinstance(arr, list) or not arr:
                self._json({"ok": False, "error": "envia {contacts:[{phone,name}]}"}, 400); return
            if len(arr) > IMPORT_MAX:
                self._json({"ok": False, "error": f"maximo {IMPORT_MAX} por lote"}, 400); return
            ok = 0; errs=[]
            for it in arr:
                phone = clean_phone(it.get("phone"))
                name = clean_name(it.get("name"))
                if not phone: errs.append(str(it)[:80]); continue
                tags = clean_tags(it.get("tags") or [])
                try:
                    upsert_contact(phone, name or None)
                    # guardar tags si vienen
                    if tags:
                        con = get_db()
                        row = con.execute("SELECT id, tags FROM contacts WHERE phone=?", (phone,)).fetchone()
                        if row:
                            try:
                                cur_tags = json.loads(row["tags"]) if row["tags"] else []
                            except: cur_tags=[]
                            merged = list(dict.fromkeys(cur_tags + tags))
                            con.execute("UPDATE contacts SET tags=?, updated_at=? WHERE phone=?", (json.dumps(merged, ensure_ascii=False), now_iso(), phone))
                            con.commit()
                        con.close()
                    ok+=1
                except Exception as e:
                    errs.append(f"{phone}: {e}")
            self._json({"ok": True, "imported": ok, "errors": errs[:10]})
            return
        elif path in ("/webhook/opportunities", "/webhook/opportunities/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            c = resolve_contact(body.get("phone"), body.get("contact_id"))
            if not c: self._json({"ok": False, "error": "falta phone/contact_id"}, 400); return
            title = clean_text(body.get("title"), 200).strip()
            if not title: self._json({"ok": False, "error": "falta title"}, 400); return
            try:
                amount = float(body.get("amount") or 0)
            except:
                amount = 0
            stage = body.get("stage") or "nuevo"
            if stage not in STAGES: stage = "nuevo"
            oid = str(uuid.uuid4()); now = now_iso()
            closed = now if stage in ("ganado", "perdido") else None
            con = get_db()
            con.execute("INSERT INTO opportunities (id,contact_id,title,amount,currency,stage,notes,created_by,created_at,updated_at,closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (oid, c["id"], title, amount, (body.get("currency") or "ARS")[:8], stage, clean_text(body.get("notes"), 1000), actor["user_id"], now, now, closed))
            con.commit(); con.close()
            print(f"[opp] {actor['display_name']} creó '{title}' (${amount:g}) para {c['phone']}")
            self._json({"ok": True, "id": oid, "stage": stage})
            return
        elif path in ("/webhook/opportunities/move", "/webhook/opportunities/move/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            oid = body.get("id") or ""
            stage = body.get("stage") or ""
            if stage not in STAGES: self._json({"ok": False, "error": f"stage invalido (usa {STAGES})"}, 400); return
            con = get_db()
            row = con.execute("SELECT id FROM opportunities WHERE id=?", (oid,)).fetchone()
            if not row: con.close(); self._json({"ok": False, "error": "oportunidad no encontrada"}, 404); return
            now = now_iso()
            closed = now if stage in ("ganado", "perdido") else None
            con.execute("UPDATE opportunities SET stage=?, updated_at=?, closed_at=? WHERE id=?", (stage, now, closed, oid))
            con.commit(); con.close()
            self._json({"ok": True, "id": oid, "stage": stage})
            return
        elif path in ("/webhook/notes", "/webhook/notes/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            c = resolve_contact(body.get("phone"), body.get("contact_id"))
            if not c: self._json({"ok": False, "error": "falta phone/contact_id"}, 400); return
            kind = body.get("kind") or "note"
            if kind not in NOTE_KINDS: kind = "note"
            txt = clean_text(body.get("body"), 2000).strip()
            if not txt: self._json({"ok": False, "error": "falta body"}, 400); return
            nid_ = str(uuid.uuid4())
            con = get_db()
            con.execute("INSERT INTO notes (id,contact_id,conversation_id,kind,body,due_at,done,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (nid_, c["id"], body.get("conversation_id"), kind, txt, body.get("due_at"), 0, actor["user_id"], now_iso()))
            con.commit(); con.close()
            self._json({"ok": True, "id": nid_})
            return
        elif path in ("/webhook/notes/done", "/webhook/notes/done/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            nid_ = body.get("id") or ""
            done = 0 if str(body.get("done")).lower() in ("0", "false", "no") else 1
            con = get_db()
            cur = con.execute("UPDATE notes SET done=? WHERE id=?", (done, nid_))
            con.commit(); con.close()
            if cur.rowcount == 0: self._json({"ok": False, "error": "nota no encontrada"}, 404); return
            self._json({"ok": True, "id": nid_, "done": done})
            return
        elif path in ("/webhook/tickets", "/webhook/tickets/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            conv_id = body.get("conversation_id") or ""
            c = resolve_contact(body.get("phone"), body.get("contact_id"))
            if not c and conv_id:
                con = get_db()
                r = con.execute("SELECT contact_id FROM conversations WHERE id=?", (conv_id,)).fetchone()
                con.close()
                if r:
                    c = {"id": r["contact_id"]}
            if not c: self._json({"ok": False, "error": "falta conversation_id/phone/contact_id"}, 400); return
            prio = body.get("priority") or "normal"
            if prio not in PRIORITIES: prio = "normal"
            tid = str(uuid.uuid4()); now = now_iso()
            sla_due = datetime.fromtimestamp(time.time() + SLA_HOURS[prio] * 3600, tz=timezone.utc).isoformat()
            con = get_db()
            con.execute("INSERT INTO tickets (id,conversation_id,contact_id,subject,priority,status,assignee_id,sla_due,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (tid, conv_id or None, c["id"], clean_text(body.get("subject"), 200) or "Sin asunto", prio, "abierto", body.get("assignee_id") or actor["user_id"], sla_due, now))
            con.commit(); con.close()
            print(f"[ticket] {actor['display_name']} abrió ticket {prio} (SLA {SLA_HOURS[prio]}h)")
            self._json({"ok": True, "id": tid, "sla_due": sla_due})
            return
        elif path in ("/webhook/tickets/status", "/webhook/tickets/status/"):
            actor = get_user_from_req(self)
            if not actor: self._json({"ok": False, "error": "no autenticado"}, 401); return
            tid = body.get("id") or ""
            status = body.get("status") or ""
            if status not in TICKET_STATUS: self._json({"ok": False, "error": f"status invalido (usa {TICKET_STATUS})"}, 400); return
            con = get_db()
            row = con.execute("SELECT id FROM tickets WHERE id=?", (tid,)).fetchone()
            if not row: con.close(); self._json({"ok": False, "error": "ticket no encontrado"}, 404); return
            now = now_iso()
            closed = now if status in ("resuelto", "cerrado") else None
            con.execute("UPDATE tickets SET status=?, closed_at=? WHERE id=?", (status, closed, tid))
            con.commit(); con.close()
            self._json({"ok": True, "id": tid, "status": status})
            return
        else:
            self.send_response(404); self.send_header("Access-Control-Allow-Origin","*"); self.end_headers()
            self.wfile.write(b'{"error":"not found"}')

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

if __name__ == "__main__":
    init_db()
    if not WA_VERIFY_TOKEN: print("[aviso] WA_VERIFY_TOKEN sin configurar: no se puede verificar el webhook en Meta")
    if not WA_SEND_KEY: print("[aviso] WA_SEND_KEY sin configurar: wa-send sin segunda cerradura (solo desarrollo)")
    if not WA_APP_SECRET: print("[aviso] WA_APP_SECRET sin configurar: wa-inbound sin verificar firma (solo desarrollo)")
    print(f"Tester mock en http://localhost:{PORT}  (handoff {HANDOFF_TIMEOUT_MIN} min, contexto: {EMPRESA_CTX.get('nombre','')})")
    print(f"Endpoints: /webhook/wa-inbox  /webhook/wa-msgs?conv_id=  /webhook/wa-suggest?conv_id=  /webhook/wa-inbound  /webhook/wa-send  /webhook/wa-handoff")
    print("Luego abrir index.html con Base n8n = http://localhost:5678")
    print("Ctrl+C para salir")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
