#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests Fase C (backend FastAPI) + Fase D (métricas, RAG, handoff auto, resúmenes).

Uso: python3 -m pytest tests/test_backend.py -q   (o python3 tests/test_backend.py)
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="crmback_")
os.environ["DATABASE_URL"] = f"sqlite:///{TMP}/crm_test.db"
os.environ["WA_SEND_KEY"] = "testkey"
os.environ["WA_APP_SECRET"] = "testsecret"
os.environ["WA_VERIFY_TOKEN"] = "testverify"

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import core as _core  # noqa: E402
from backend.app.routers import insights as _insights  # noqa: E402
from backend.app.main import app  # noqa: E402

_core.OPENROUTER_API_KEY = ""       # resúmenes/LLM en modo extractivo (sin red)
_insights.OPENROUTER_API_KEY = ""

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def main():
    global PASS, FAIL
    with TestClient(app) as c:
        r = c.get("/webhook/wa-inbox")
        check("inbox sin token -> 401", r.status_code == 401)
        r = c.get("/webhook/opportunities")
        check("opps sin token -> 401", r.status_code == 401)
        r = c.get("/webhook/metrics/overview")
        check("metrics sin token -> 401", r.status_code == 401)

        r = c.post("/webhook/auth-login", json={"username": "matias", "password": "matias123"})
        check("login matias", r.status_code == 200 and r.json().get("ok"))
        tok = r.json()["token"]
        H = {"X-Auth-Token": tok}

        r = c.get("/webhook/wa-inbox", headers=H)
        check("inbox con token", r.status_code == 200 and isinstance(r.json(), list)
              and len(r.json()) >= 1)
        conv_id = r.json()[0]["conversation_id"]

        # verify Meta
        r = c.get("/webhook/wa-inbound", params={"hub.mode": "subscribe",
                                                 "hub.verify_token": "testverify",
                                                 "hub.challenge": "XYZ"})
        check("verify Meta", r.status_code == 200 and r.text == "XYZ")
        r = c.get("/webhook/wa-inbound", params={"hub.mode": "subscribe",
                                                 "hub.verify_token": "mal",
                                                 "hub.challenge": "XYZ"})
        check("verify malo -> 403", r.status_code == 403)

        # inbound sin firma -> 403
        r = c.post("/webhook/wa-inbound", json={"from": "5491100000001", "text": "hola"})
        check("inbound sin firma -> 403", r.status_code == 403)

        def inbound(payload):
            raw = json.dumps(payload).encode()
            sig = "sha256=" + hmac.new(b"testsecret", raw, hashlib.sha256).hexdigest()
            return c.post("/webhook/wa-inbound", content=raw,
                          headers={"Content-Type": "application/json",
                                   "X-Hub-Signature-256": sig})

        r = inbound({"from": "5491100000001", "name": "Test", "text": "cuanto sale la campera?"})
        check("inbound regla ventas", r.status_code == 200
              and "asesor de ventas" in r.json().get("reply", ""))

        # Fase D: pide humano -> handoff auto + ticket
        r = inbound({"from": "5491100000002", "name": "Queja",
                     "text": "quiero hablar con un humano, esto es una estafa"})
        d = r.json()
        check("handoff automático", r.status_code == 200 and d.get("handoff") is True
              and d.get("auto") is True)
        r = c.get("/webhook/tickets", headers=H)
        auto = [t for t in r.json() if t.get("subject", "").startswith("Derivación")]
        check("ticket auto creado", len(auto) >= 1)

        # wa-send exige key
        r = c.post("/webhook/wa-send", json={"to": "5491100000001", "text": "hola"},
                   headers=H)
        check("send sin key -> 401", r.status_code == 401)
        r = c.post("/webhook/wa-send", json={"to": "5491100000001", "text": "hola soy humano"},
                   headers={**H, "X-Api-Key": "testkey"})
        check("send con key", r.status_code == 200 and r.json().get("ok"))

        # claim: matias toma, asesor1 recibe 409
        r = c.post("/webhook/auth-login", json={"username": "asesor1", "password": "1234"})
        tok2 = r.json()["token"]
        H2 = {"X-Auth-Token": tok2}
        r = c.post("/webhook/conversations/claim", json={"conv_id": conv_id}, headers=H)
        check("claim matias", r.status_code == 200 and r.json().get("ok"))
        r = c.post("/webhook/conversations/claim", json={"conv_id": conv_id}, headers=H2)
        check("claim duplicado -> 409", r.status_code == 409)

        # oportunidades
        r = c.post("/webhook/opportunities",
                   json={"phone": "5491100000001", "title": "Venta BE", "amount": 999},
                   headers=H)
        oid = r.json().get("id")
        check("crear opp", r.status_code == 200 and oid)
        r = c.post("/webhook/opportunities/move", json={"id": oid, "stage": "ganado"},
                   headers=H)
        check("mover a ganado", r.json().get("stage") == "ganado")
        r = c.get("/webhook/opportunities?stage=ganado", headers=H)
        check("filtrar por stage", any(o["id"] == oid for o in r.json()))

        # notas + tickets + timeline
        r = c.post("/webhook/notes",
                   json={"phone": "5491100000001", "kind": "task", "body": "Llamar BE"},
                   headers=H)
        nid_ = r.json().get("id")
        check("crear nota", r.status_code == 200 and nid_)
        r = c.post("/webhook/notes/done", json={"id": nid_}, headers=H)
        check("completar nota", r.json().get("done") == 1)
        r = c.post("/webhook/tickets",
                   json={"phone": "5491100000001", "subject": "T BE", "priority": "critica"},
                   headers=H)
        tid = r.json().get("id")
        check("ticket con SLA", r.status_code == 200 and r.json().get("sla_due"))
        r = c.get("/webhook/timeline?phone=5491100000001", headers=H)
        d = r.json()
        check("timeline 360", all(k in d for k in
                                  ("contact", "conversations", "notes", "opportunities", "tickets")))

        # Fase D: métricas + RAG + resumen
        r = c.get("/webhook/metrics/overview", headers=H)
        d = r.json()
        check("metrics overview", all(k in d for k in
                                      ("conversations_by_status", "opportunities_by_stage",
                                       "pipeline_open_amount", "tickets_open",
                                       "messages_per_day", "avg_first_response_min")))
        r = c.get("/webhook/kb/search", params={"q": "cambios devolucion 30 dias"}, headers=H)
        res = r.json().get("results", [])
        check("RAG encuentra política", len(res) > 0 and "30" in res[0]["text"])
        r = c.post("/webhook/conversations/summarize", json={"conv_id": conv_id}, headers=H)
        d = r.json()
        check("resumen extractivo", r.status_code == 200 and d.get("mode") == "extractivo"
              and len(d.get("summary", "")) > 10)

        # logout invalida
        c.post("/webhook/auth-logout", headers=H)
        r = c.get("/webhook/wa-inbox", headers=H)
        check("logout invalida token", r.status_code == 401)

    print(f"\nRESULTADO: {PASS} ok, {FAIL} fallos")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
