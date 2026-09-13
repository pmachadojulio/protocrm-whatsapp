"""RAG $0 sin dependencias: chunks de kb/*.md + TF-IDF + coseno (stdlib).

La base de conocimiento vive en kb/ (markdown). Reindexar junta también
empresa_contexto.json. Sin API keys: respuesta extractiva por overlap.
Con OpenRouter: los chunks top-k entran al prompt del LLM (ver core.py).
"""
import json
import math
import os
import re

from .config import KB_DIR, ROOT_DIR

STOP = set("""de la el en y a los del se las por un para con no una su al lo como mas pero sus entre
cuando donde quien que este esta estos estas ese esa esos esas esto eso aquel aquello ser hay fue son
nuestro nuestra nuestros nuestras tu tus usted ustedes mi mis me te nos le les lo la me mi-""".split())

_state = {"chunks": [], "df": {}, "n": 0}


def _tokens(s: str) -> list:
    return [w for w in re.findall(r"[a-záéíóúñü0-9]+", (s or "").lower())
            if len(w) > 2 and w not in STOP]


def _read_sources() -> list:
    docs = []
    if os.path.isdir(KB_DIR):
        for fn in sorted(os.listdir(KB_DIR)):
            if fn.endswith((".md", ".txt")):
                try:
                    with open(os.path.join(KB_DIR, fn), encoding="utf-8") as f:
                        docs.append((fn, f.read()))
                except Exception:
                    pass
    try:
        with open(os.path.join(ROOT_DIR, "empresa_contexto.json"), encoding="utf-8") as f:
            e = json.load(f)
        txt = " ".join(f"{k}: {v}" for k, v in e.items() if v)
        docs.append(("empresa_contexto.json", f"# Empresa\n\n{txt}"))
    except Exception:
        pass
    return docs


def reindex() -> int:
    chunks = []
    for source, text in _read_sources():
        for i, para in enumerate(re.split(r"\n\s*\n", text)):
            para = para.strip()
            if len(para) < 40:
                continue
            chunks.append({"source": source, "idx": i, "text": para[:1200], "tf": {}})
    df: dict = {}
    for ch in chunks:
        tf: dict = {}
        for w in _tokens(ch["text"]):
            tf[w] = tf.get(w, 0) + 1
        ch["tf"] = tf
        for w in tf:
            df[w] = df.get(w, 0) + 1
    _state.update(chunks=chunks, df=df, n=len(chunks))
    return len(chunks)


def _score(qtf: dict, ch: dict) -> float:
    n = _state["n"]
    if not n or not qtf:
        return 0.0
    s = 0.0
    qnorm = sum(v * v for v in qtf.values()) ** 0.5 or 1.0
    cnorm = sum(v * v for v in ch["tf"].values()) ** 0.5 or 1.0
    for w, qv in qtf.items():
        cv = ch["tf"].get(w)
        if not cv:
            continue
        idf = math.log(1 + n / (1 + _state["df"].get(w, 0)))
        s += (qv / qnorm) * (cv / cnorm) * idf * idf
    return s


def search(query: str, k: int = 3) -> list:
    if not _state["chunks"]:
        reindex()
    qtf: dict = {}
    for w in _tokens(query):
        qtf[w] = qtf.get(w, 0) + 1
    ranked = sorted(((_score(qtf, ch), ch) for ch in _state["chunks"]),
                    key=lambda x: x[0], reverse=True)
    return [{"source": ch["source"], "text": ch["text"], "score": round(s, 4)}
            for s, ch in ranked[:k] if s > 0]


def extractive_answer(query: str, threshold: float = 0.05) -> str | None:
    hits = search(query, k=1)
    if hits and hits[0]["score"] >= threshold:
        return hits[0]["text"]
    return None


def context_block(query: str, k: int = 3) -> str:
    hits = search(query, k=k)
    if not hits:
        return ""
    return "\n".join(f"- [{h['source']}] {h['text']}" for h in hits)
