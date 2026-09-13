#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests Fase A (seguridad) + Fase B (endpoints CRM).
Usa DB temporal y servidor en puerto 5679. No toca mock.db.
Uso: python3 test_fase_a.py
"""
import json, os, sys, hmac, hashlib, threading, tempfile, time
from http.server import HTTPServer
import urllib.request, urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import tester_mock as m

PORT = 5679
PASS = 0
FAIL = 0

def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")

def req(method, path, body=None, token=None, headers=None):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("X-Auth-Token", token)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def main():
    global PASS, FAIL
    tmp = tempfile.mkdtemp(prefix="crmtest_")
    m.DB = os.path.join(tmp, "test.db")
    m.PBKDF2_ITER = 10_000  # rápido para tests (el formato guarda las iteraciones)
    m.WA_SEND_KEY = "test-send-key"
    m.WA_APP_SECRET = "test-app-secret"
    m.WA_VERIFY_TOKEN = "test-verify"
    m.init_db()

    print("== hashing pbkdf2 ==")
    h = m.hash_pw("secreto123")
    check("formato pbkdf2", h.startswith("pbkdf2$"))
    check("verify ok", m.verify_pw("secreto123", h))
    check("verify rechaza mal password", not m.verify_pw("otra", h))
    legacy = hashlib.sha256("vieja123".encode()).hexdigest()
    con = m.get_db()
    con.execute("INSERT INTO users (id,username,pass_hash,display_name,role,created_at) VALUES (?,?,?,?,?,?)",
                ("u-legacy", "legacy", legacy, "Legacy", "agent", m.now_iso()))
    con.commit(); con.close()
    u = m.check_user("legacy", "vieja123")
    check("login con hash legacy funciona", u is not None)
    con = m.get_db()
    newh = con.execute("SELECT pass_hash FROM users WHERE username='legacy'").fetchone()["pass_hash"]
    con.close()
    check("hash legacy migrado a pbkdf2", newh.startswith("pbkdf2$"))
    check("login legacy con mal password falla", m.check_user("legacy", "nope") is None)

    print("== servidor + auth ==")
    srv = HTTPServer(("127.0.0.1", PORT), m.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    code, _ = req("GET", "/webhook/wa-inbox")
    check("inbox sin token -> 401", code == 401)
    code, _ = req("GET", "/webhook/wa-msgs?conv_id=x")
    check("msgs sin token -> 401", code == 401)
    code, _ = req("GET", "/webhook/wa-suggest?conv_id=x")
    check("suggest sin token -> 401", code == 401)
    code, _ = req("GET", "/webhook/contacts")
    check("contacts sin token -> 401", code == 401)
    code, _ = req("GET", "/webhook/opportunities")
    check("opportunities sin token -> 401", code == 401)
    code, _ = req("GET", "/webhook/tickets")
    check("tickets sin token -> 401", code == 401)

    code, txt = req("POST", "/webhook/auth-login", {"username": "matias", "password": "matias123"})
    d = json.loads(txt)
    check("login matias ok", code == 200 and d.get("ok") and d.get("token"))
    tok = d["token"]
    # sesión persistente en DB (sobrevive reinicio)
    con = m.get_db()
    row = con.execute("SELECT user_id FROM sessions WHERE token=?", (tok,)).fetchone()
    con.close()
    check("sesión guardada en DB", row is not None)

    code, txt = req("GET", "/webhook/wa-inbox", token=tok)
    check("inbox con token -> 200 lista", code == 200 and isinstance(json.loads(txt), list))
    code, txt = req("GET", "/webhook/wa-inbox?paged=1&limit=1", token=tok)
    d = json.loads(txt)
    check("paginación envelope", code == 200 and "data" in d and "total" in d and d["limit"] == 1)

    print("== wa-send con WA_SEND_KEY ==")
    code, _ = req("POST", "/webhook/wa-send", {"to": "5491100000001", "text": "hola"}, token=tok)
    check("send sin X-Api-Key -> 401", code == 401)
    code, txt = req("POST", "/webhook/wa-send", {"to": "5491100000001", "text": "hola"}, token=tok,
                    headers={"X-Api-Key": "test-send-key"})
    check("send con key ok", code == 200 and json.loads(txt).get("ok"))

    print("== firma Meta ==")
    raw = json.dumps({"from": "5491100000002", "text": "hola bot"}).encode()
    sig = "sha256=" + hmac.new(b"test-app-secret", raw, hashlib.sha256).hexdigest()
    code, _ = req("POST", "/webhook/wa-inbound", json.loads(raw.decode()))
    check("inbound sin firma -> 403", code == 403)
    # con firma válida (hay que reenviar el body exacto: usamos urllib directo)
    url = f"http://127.0.0.1:{PORT}/webhook/wa-inbound"
    r = urllib.request.Request(url, data=raw, method="POST",
                               headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
    with urllib.request.urlopen(r, timeout=5) as resp:
        code2, txt2 = resp.status, resp.read().decode()
    check("inbound con firma ok", code2 == 200 and json.loads(txt2).get("ok"))
    code, txt = req("GET", "/webhook/wa-inbound?hub.mode=subscribe&hub.verify_token=test-verify&hub.challenge=ABC123")
    check("verify Meta ok devuelve challenge", code == 200 and txt == "ABC123")
    code, _ = req("GET", "/webhook/wa-inbound?hub.mode=subscribe&hub.verify_token=mal&hub.challenge=ABC123")
    check("verify con token malo -> 403", code == 403)

    print("== sanitización ==")
    check("phone corto se rechaza", m.clean_phone("123") == "")
    check("phone válido", m.clean_phone("+54 9 11 1234-5678") == "5491112345678")
    check("texto se trunca", len(m.clean_text("x" * 9000)) == m.MAX_BODY)
    check("tags limitados", len(m.clean_tags([f"t{i}" for i in range(99)])) == m.MAX_TAGS)
    check("tags deduplican", m.clean_tags(["a", "a", "b"]) == ["a", "b"])

    print("== Fase B: pipeline/notas/tickets ==")
    code, txt = req("POST", "/webhook/opportunities",
                    {"phone": "5491100000001", "title": "Venta test", "amount": 1500}, token=tok)
    d = json.loads(txt)
    check("crear oportunidad", code == 200 and d.get("ok"))
    oid = d.get("id")
    code, txt = req("POST", "/webhook/opportunities/move", {"id": oid, "stage": "cotizado"}, token=tok)
    check("mover etapa", code == 200 and json.loads(txt).get("stage") == "cotizado")
    code, txt = req("POST", "/webhook/opportunities/move", {"id": oid, "stage": "invento"}, token=tok)
    check("etapa inválida -> 400", code == 400)
    code, txt = req("GET", "/webhook/opportunities", token=tok)
    check("listar oportunidades", code == 200 and len(json.loads(txt)) >= 1)
    code, txt = req("POST", "/webhook/notes",
                    {"phone": "5491100000001", "kind": "task", "body": "Llamar mañana"}, token=tok)
    nid_ = json.loads(txt).get("id")
    check("crear nota/tarea", code == 200 and nid_)
    code, txt = req("POST", "/webhook/notes/done", {"id": nid_}, token=tok)
    check("completar tarea", code == 200 and json.loads(txt).get("done") == 1)
    code, txt = req("POST", "/webhook/tickets",
                    {"phone": "5491100000001", "subject": "Reclamo test", "priority": "alta"}, token=tok)
    d = json.loads(txt)
    check("crear ticket con SLA", code == 200 and d.get("sla_due"))
    tid = d.get("id")
    code, txt = req("GET", "/webhook/tickets", token=tok)
    tl = json.loads(txt)
    check("ticket lista breached=False", code == 200 and tl and tl[0].get("breached") is False)
    code, _ = req("POST", "/webhook/tickets/status", {"id": tid, "status": "resuelto"}, token=tok)
    check("resolver ticket", code == 200)
    code, txt = req("GET", "/webhook/timeline?phone=5491100000001", token=tok)
    d = json.loads(txt)
    check("timeline 360", code == 200 and d.get("contact") and "opportunities" in d and "tickets" in d and "notes" in d)

    print("== logout ==")
    code, _ = req("POST", "/webhook/auth-logout", {}, token=tok)
    code2, _ = req("GET", "/webhook/wa-inbox", token=tok)
    check("token invalidado tras logout", code2 == 401)

    srv.shutdown()
    print(f"\nRESULTADO: {PASS} ok, {FAIL} fallos")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
