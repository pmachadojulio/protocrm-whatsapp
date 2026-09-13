#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backup del CRM (Fase A).
- Mock/SQLite: copia mock.db a backups/ con timestamp, conserva las últimas 10.
- Postgres (compose): imprime el comando pg_dump correspondiente.
Uso: python3 backup.py
"""
import os, shutil, glob
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE, "backups")
KEEP = 10

def backup_sqlite():
    src = os.path.join(BASE, "mock.db")
    if not os.path.exists(src):
        print("[backup] no hay mock.db (nada que respaldar en modo mock)")
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"mock-{stamp}.db")
    # copia consistente de sqlite (checkpoint simple: el mock no usa WAL, basta copy)
    shutil.copy2(src, dst)
    print(f"[backup] {dst} ({os.path.getsize(dst)//1024} KB)")
    olds = sorted(glob.glob(os.path.join(BACKUP_DIR, "mock-*.db")))
    for f in olds[:-KEEP]:
        os.remove(f)
        print(f"[backup] rotado (borrado): {os.path.basename(f)}")

def print_pg_hint():
    print("[backup] Postgres (compose):")
    print('  docker compose exec postgres pg_dump -U postgres -d protocrm > backups/pg-$(date +%Y%m%d-%H%M%S).sql')

if __name__ == "__main__":
    backup_sqlite()
    print_pg_hint()
