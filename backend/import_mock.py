#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Importa datos de mock.db (tester_mock.py) al backend FastAPI.
Uso: python3 backend/import_mock.py [ruta_mock.db]
Copia: users (hashes intactos, legacy incluido), contacts, conversations,
messages, bot_rules, opportunities, notes, tickets. No pisa existentes (por id).
"""
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, ROOT)

from backend.app.config import DATABASE_URL  # noqa: E402
from backend.app.db import SessionLocal, init_db  # noqa: E402
from backend.app import models  # noqa: E402

TABLES = ["users", "contacts", "conversations", "messages", "bot_rules",
          "opportunities", "notes", "tickets"]

MODEL = {"users": models.User, "contacts": models.Contact,
         "conversations": models.Conversation, "messages": models.Message,
         "bot_rules": models.BotRule, "opportunities": models.Opportunity,
         "notes": models.Note, "tickets": models.Ticket}


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "mock.db")
    if not os.path.exists(src):
        print(f"no existe {src}")
        sys.exit(1)
    print(f"origen: {src}\ndestino: {DATABASE_URL}")
    init_db()
    con = sqlite3.connect(src)
    con.row_factory = sqlite3.Row
    db = SessionLocal()
    total = 0
    try:
        for t in TABLES:
            try:
                rows = con.execute(f"SELECT * FROM {t}").fetchall()
            except Exception as e:
                print(f"  {t}: omitida ({e})")
                continue
            cols = [c for c in rows[0].keys()] if rows else []
            model_cols = {c.name for c in MODEL[t].__table__.columns}
            n = 0
            for r in rows:
                data = {k: r[k] for k in cols if k in model_cols}
                if "id" in data and db.query(MODEL[t]).filter(
                        MODEL[t].id == data["id"]).first():
                    continue
                db.add(MODEL[t](**data))
                n += 1
            db.commit()
            total += n
            print(f"  {t}: {n} importadas")
    finally:
        db.close()
        con.close()
    print(f"listo: {total} filas importadas (las existentes no se pisan)")


if __name__ == "__main__":
    main()
