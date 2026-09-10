#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tester mock sin Docker — imita los 4 webhooks de n8n con SQLite.
Uso: python tester_mock.py  (escucha en http://localhost:5678)
Luego abrir index.html con Base n8n = http://localhost:5678
"""
import json, sqlite3, uuid, re, hashlib, time, os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock.db")
PORT = 5678
HANDOFF_TIMEOUT_MIN = 30  # minutos que el bot queda pausado tras hablar un humano

# Contexto de empresa para respuestas IA (editable en empresa_contexto.json)
import os
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
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, pass_hash TEXT NOT NULL, display_name TEXT, role TEXT DEFAULT 'agent', created_at TEXT
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
SESSIONS = {}  # token -> {user_id, username, display_name, role, exp}

def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()
def check_user(username, password):
    con = get_db()
    row = con.execute("SELECT id, username, pass_hash, display_name, role FROM users WHERE username=?", (username,)).fetchone()
    con.close()
    if not row: return None
    if row["pass_hash"] != hash_pw(password): return None
    return dict(row)
def create_session(user):
    token = uuid.uuid4().hex
    SESSIONS[token] = {"user_id": user["id"], "username": user["username"], "display_name": user["display_name"], "role": user["role"], "exp": time.time()+86400}
    return token
def get_user_from_req(handler):
    # header X-Auth-Token o Authorization: Bearer <token>
    tok = handler.headers.get("X-Auth-Token") or handler.headers.get("x-auth-token") or ""
    if not tok:
        auth = handler.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
    if not tok or tok not in SESSIONS: return None
    s = SESSIONS[tok]
    if s["exp"] < time.time():
        del SESSIONS[tok]
        return None
    return s

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

def decide_reply(text, conv_id=None):
    t = (text or "").lower()
    # 1) reglas SQL
    con = get_db()
    rows = list(con.execute("SELECT keywords,response FROM bot_rules WHERE active=1 ORDER BY priority, id").fetchall())
    con.close()
    for r in rows:
        kws = [k.strip().lower() for k in r["keywords"].split(",") if k.strip()]
        if any(k in t for k in kws):
            return r["response"]
    # 2) si hay contexto de empresa, usarlo para horarios/turnos antes de fallback genérico
    if any(k in t for k in ["horario","turno","cuando abren","direccion","donde quedan"]):
        return f"{EMPRESA_CTX.get('horarios','')} {EMPRESA_CTX.get('turnos','')}".strip()
    # 3) intento LLM via OpenRouter si hay key (mismo comportamiento que n8n)
    api_key = os.environ.get("OPENROUTER_API_KEY") or ""
    # también buscar en .env
    if not api_key:
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
    if re.match(r"^(hola|buenas|buen dia|buenos dias|hello|hey|wacho|falopin|sos un bot)", t):
        return "Hola, gracias por escribirnos. Un asesor te respondera a la brevedad."
    return "Hola, gracias por escribirnos. Un asesor te respondera a la brevedad."

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
            try:
                con = get_db()
                rows = con.execute("""
                  SELECT c.id as conversation_id, ct.id as contact_id, ct.phone, COALESCE(ct.name,'Sin nombre') as contact_name,
                         c.status, c.bot_handled, c.assignee_id, COALESCE(u.display_name,'') as assignee_name,
                         c.last_message_at,
                         (SELECT body FROM messages m WHERE m.conversation_id=c.id ORDER BY created_at DESC LIMIT 1) as last_message,
                         (SELECT direction FROM messages m WHERE m.conversation_id=c.id ORDER BY created_at DESC LIMIT 1) as last_direction,
                         (SELECT created_by FROM messages m WHERE m.conversation_id=c.id ORDER BY created_at DESC LIMIT 1) as last_by
                  FROM conversations c JOIN contacts ct ON ct.id=c.contact_id LEFT JOIN users u ON u.id=c.assignee_id
                  ORDER BY c.last_message_at DESC LIMIT 50
                """).fetchall()
                con.close()
                data = [dict(r) for r in rows]
                self._json(data)
            except Exception as e:
                print(f"[wa-inbox] error: {e}")
                try: con.close()
                except: pass
                self._json({"error": str(e), "hint": "borra mock.db y reinicia el tester"}, 500)
        elif path in ("/webhook/wa-msgs", "/webhook/wa-msgs/"):
            conv_id = (qs.get("conv_id") or [""])[0]
            if not conv_id:
                self._json([])
                return
            con = get_db()
            rows = con.execute("SELECT id, conversation_id, direction, message_type, body, media_url, created_at, COALESCE(created_by,'') as created_by, COALESCE((SELECT display_name FROM users WHERE id=created_by), created_by) as created_by_name FROM messages WHERE conversation_id=? ORDER BY created_at", (conv_id,)).fetchall()
            con.close()
            self._json([dict(r) for r in rows])
        elif path in ("/webhook/wa-suggest", "/webhook/wa-suggest/"):
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
            # lista de contactos con última interacción
            try:
                con = get_db()
                rows = con.execute("""
                  SELECT c.id, c.phone, COALESCE(c.name,'') as name, c.tags, c.last_seen, c.created_at,
                         (SELECT COUNT(*) FROM conversations conv WHERE conv.contact_id=c.id) as conv_count,
                         (SELECT COUNT(*) FROM messages m JOIN conversations conv ON conv.id=m.conversation_id WHERE conv.contact_id=c.id) as msg_count,
                         (SELECT MAX(m.created_at) FROM messages m JOIN conversations conv ON conv.id=m.conversation_id WHERE conv.contact_id=c.id) as last_interaction
                  FROM contacts c ORDER BY last_interaction DESC NULLS LAST, c.created_at DESC LIMIT 200
                """).fetchall()
                con.close()
                # tags viene como JSON string '[]', lo dejamos tal cual
                self._json([dict(r) for r in rows])
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        elif path in ("/webhook/wa-handoff", "/webhook/wa-handoff/"):
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
            tok = self.headers.get("X-Auth-Token") or self.headers.get("x-auth-token") or ""
            if not tok:
                auth = self.headers.get("Authorization") or ""
                if auth.lower().startswith("bearer "): tok = auth[7:].strip()
            if tok in SESSIONS: del SESSIONS[tok]
            self._json({"ok": True}); return
        elif path in ("/webhook/wa-inbound", "/webhook/wa-inbound/"):
            # soporta payload Meta real o payload simple {from, text, name}
            phone = name = text = wa_id = None
            try:
                if "entry" in body:
                    e = body["entry"][0]; ch = e["changes"][0]; v = ch["value"]
                    m = v["messages"][0]
                    phone = m.get("from")
                    wa_id = m.get("id")
                    text = (m.get("text") or {}).get("body") or m.get("button",{}).get("text") or ""
                    name = (v.get("contacts",[{}])[0].get("profile") or {}).get("name")
                else:
                    phone = body.get("from") or body.get("phone")
                    text = body.get("text") or body.get("body") or ""
                    name = body.get("name")
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
            reply = decide_reply(text, coid)
            # actualizar y marcar que respondió el bot
            con = get_db(); con.execute("UPDATE conversations SET last_message_at=?, bot_handled=1, updated_at=?, status='bot' WHERE id=?", (now_iso(), now_iso(), coid)); con.commit(); con.close()
            insert_message(coid, "out", reply)
            self._json({"ok": True, "reply": reply})
        elif path in ("/webhook/wa-send", "/webhook/wa-send/"):
            # validar login si hay usuarios (multi-usuario)
            actor = get_user_from_req(self)
            # si hay al menos un usuario en DB, exigir login para trazar quien respondio
            con = get_db(); has_users = con.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"] > 0; con.close()
            if has_users and not actor:
                self._json({"ok": False, "error": "no autenticado - hace login primero"}, 401); return
            to = (body.get("to") or "").strip()
            text = (body.get("text") or "").strip()
            if not to or not text:
                self._json({"ok": False, "error": "faltan to/text"}, 400); return
            phone = re.sub(r"[^0-9]", "", to)
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
            con = get_db(); has_users = con.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"] > 0; con.close()
            if has_users and not actor:
                self._json({"ok": False, "error": "no autenticado"}, 401); return
            arr = body.get("contacts") or body.get("data") or []
            if isinstance(body, list): arr = body
            if not isinstance(arr, list) or not arr:
                self._json({"ok": False, "error": "envia {contacts:[{phone,name}]}"}, 400); return
            ok = 0; errs=[]
            for it in arr:
                phone = re.sub(r"[^0-9]", "", str(it.get("phone") or ""))
                name = (it.get("name") or "").strip()
                if not phone: errs.append(str(it)); continue
                tags = it.get("tags") or []
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
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
    print(f"Tester mock en http://localhost:{PORT}  (handoff {HANDOFF_TIMEOUT_MIN} min, contexto: {EMPRESA_CTX.get('nombre','')})")
    print(f"Endpoints: /webhook/wa-inbox  /webhook/wa-msgs?conv_id=  /webhook/wa-suggest?conv_id=  /webhook/wa-inbound  /webhook/wa-send  /webhook/wa-handoff")
    print("Luego abrir index.html con Base n8n = http://localhost:5678")
    print("Ctrl+C para salir")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
