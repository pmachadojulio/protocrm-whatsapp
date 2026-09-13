"""Config central. Lee el .env de la raíz del proyecto (mismos secretos del mock)."""
import os
import secrets as pysecrets

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(APP_DIR)
ROOT_DIR = os.path.dirname(BACKEND_DIR)


def _load_env():
    try:
        with open(os.path.join(ROOT_DIR, ".env"), encoding="utf-8") as f:
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


_load_env()


def cfg(name, default=""):
    return (os.environ.get(name) or default).strip()


DATABASE_URL = cfg("DATABASE_URL", f"sqlite:///{os.path.join(BACKEND_DIR, 'crm.db')}")
JWT_SECRET = cfg("JWT_SECRET")
if not JWT_SECRET:
    JWT_SECRET = pysecrets.token_hex(32)
    print("[aviso] JWT_SECRET sin configurar: sesiones inválidas al reiniciar (solo desarrollo)")

WA_VERIFY_TOKEN = cfg("WA_VERIFY_TOKEN")
WA_SEND_KEY = cfg("WA_SEND_KEY")
WA_APP_SECRET = cfg("WA_APP_SECRET")
OPENROUTER_API_KEY = cfg("OPENROUTER_API_KEY")
LLM_MODEL = cfg("LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
try:
    SESSION_DAYS = max(1, int(cfg("SESSION_DAYS", "7")))
except Exception:
    SESSION_DAYS = 7

HANDOFF_TIMEOUT_MIN = 30
PBKDF2_ITER = 200_000

MAX_BODY = 4000
MAX_NAME = 120
MAX_PHONE_DIGITS = 20
MIN_PHONE_DIGITS = 7
MAX_TAGS = 20
MAX_TAG_LEN = 40
IMPORT_MAX = 2000

STAGES = ["nuevo", "contactado", "cotizado", "ganado", "perdido"]
OPEN_STAGES = ["nuevo", "contactado", "cotizado"]
PRIORITIES = ["baja", "normal", "alta", "critica"]
TICKET_STATUS = ["abierto", "en_progreso", "resuelto", "cerrado"]
SLA_HOURS = {"critica": 2, "alta": 8, "normal": 24, "baja": 72}
NOTE_KINDS = ["note", "task", "call", "visit"]

EMPRESA_CTX = {}
try:
    import json
    with open(os.path.join(ROOT_DIR, "empresa_contexto.json"), encoding="utf-8") as f:
        EMPRESA_CTX = json.load(f)
except Exception:
    EMPRESA_CTX = {"nombre": "Tu Empresa", "horarios": "Lunes a viernes 9 a 18 hs",
                   "turnos": "Sin turno, por orden de llegada"}

KB_DIR = os.path.join(ROOT_DIR, "kb")
