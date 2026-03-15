# rag_engine.py
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from openai import OpenAI

TOKEN_PRICE_PER_1K = 0.002
client = OpenAI()


# ----------------------------
# Data Models
# ----------------------------

@dataclass
class RetrievedChunk:
    score: float
    text: str
    source: str | None = None


@dataclass
class RagResult:
    answer: str
    tokens: int
    cost: float
    confidence: float
    retrieved: List[RetrievedChunk]
    ok: bool
    reason: str


# ----------------------------
# Path Helpers (IMPORTANT)
# ----------------------------

def project_root() -> Path:
    # rag_engine.py is at project root in your tree. If you later move it into a package,
    # this still works because it climbs upward until it finds /clients.
    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        if (p / "clients").exists():
            return p
    return here.parent


def _client_dir(client_name: str) -> Path:
    root = project_root()
    return root / "clients" / client_name


# ----------------------------
# Loaders
# ----------------------------

def load_client_config(client_name: str) -> Dict[str, Any]:
    path = _client_dir(client_name) / "config" / "settings.json"
    if not path.exists():
        raise FileNotFoundError(f"No config for client: {client_name}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_client_key(client_name: str) -> str:
    path = _client_dir(client_name) / "config" / "api_key.json"
    if not path.exists():
        raise FileNotFoundError(f"No API key for client: {client_name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return (data.get("api_key") or "").strip()


def load_client_embeddings(client_name: str) -> List[Dict[str, Any]]:
    path = _client_dir(client_name) / "knowledge" / "embeddings.json"
    if not path.exists():
        raise FileNotFoundError(f"No data for client: {client_name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------
# Logging (keep your current behavior)
# ----------------------------

def log_usage(client_name: str, tokens: int, cost: float) -> None:
    usage_file = project_root() / "usage" / "usage_log.json"
    usage_file.parent.mkdir(exist_ok=True)

    if not usage_file.exists():
        usage_file.write_text("[]", encoding="utf-8")

    data = json.loads(usage_file.read_text(encoding="utf-8"))

    data.append(
        {
            "client": client_name,
            "tokens": tokens,
            "cost": round(cost, 6),
            "date": datetime.now().strftime("%Y-%m-%d"),
        }
    )

    usage_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def log_chat(question: str, answer: str, tone: str) -> None:
    logs_dir = project_root() / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / "chat_log.txt"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n[{ts}]\n")
        f.write(f"Tone: {tone}\n")
        f.write(f"Q: {question}\n")
        f.write(f"A: {answer}\n")
        f.write("-" * 50 + "\n")


# ----------------------------
# Similarity
# ----------------------------

def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def search_knowledge(query: str, client_data: List[Dict[str, Any]], top_k: int = 3) -> List[RetrievedChunk]:
    resp = client.embeddings.create(model="text-embedding-3-small", input=query)
    query_vec = resp.data[0].embedding

    scored: List[RetrievedChunk] = []
    for item in client_data:
        score = cosine_similarity(query_vec, item["embedding"])
        scored.append(
            RetrievedChunk(
                score=float(score),
                text=str(item.get("text") or ""),
                source=item.get("source"),
            )
        )

    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[:top_k]


# ----------------------------
# Answer generation + validation
# ----------------------------

def validate_answer(answer: str) -> bool:
    bad_phrases = ["i think", "maybe", "not sure", "probably", "guess"]
    a = (answer or "").lower()
    return not any(p in a for p in bad_phrases)


def build_system_prompt(context_chunks: List[RetrievedChunk], tone: str, client_config: Dict[str, Any], language: str) -> str:
    if tone == "friendly":
        style = "Use a warm, friendly, and supportive tone."
    elif tone == "premium":
        style = "Use a luxury, VIP-style, highly respectful tone."
    else:
        style = "Use a formal, professional corporate tone."

    context = ""
    for c in context_chunks:
        if c.text.strip():
            context += f"- {c.text}\n\n"

    if language == "ar":
        lang_line = "Reply in Arabic."
    else:
        lang_line = "Reply in English."

    return f"""
You are a professional AI customer support assistant for an e-commerce company in the GCC.

Style:
{style}

Rules:
1) Use ONLY the information in Company Policies (context).
2) If information is missing, ask ONE short clarifying question.
3) Do NOT guess. Do NOT invent.
4) Keep it clear and polite.
5) {lang_line}

Company Policies (context):
{context}

Legal Notice:
{client_config.get("legal_notice", "")}
""".strip()


def generate_answer(system_prompt: str, user_prompt: str) -> Tuple[str, int]:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    answer = resp.choices[0].message.content or ""
    tokens = int(resp.usage.total_tokens or 0)
    return answer.strip(), tokens


# ----------------------------
# MAIN ENTRY FOR YOUR API
# ----------------------------

def answer_with_rag(
    client_name: str,
    question: str,
    tone: str = "formal",
    language: str = "en",
    top_k: int = 3,
) -> RagResult:
    cfg = load_client_config(client_name)
    if not cfg.get("active", True):
        return RagResult(
            answer="Client suspended.",
            tokens=0,
            cost=0.0,
            confidence=0.0,
            retrieved=[],
            ok=False,
            reason="client_suspended",
        )

    client_data = load_client_embeddings(client_name)
    chunks = search_knowledge(question, client_data, top_k=top_k)

    # Confidence = best similarity score (simple, stable)
    confidence = float(chunks[0].score) if chunks else 0.0

    # IMPORTANT: do NOT decide escalation here — only return confidence
    system_prompt = build_system_prompt(chunks, tone=tone, client_config=cfg, language=language)
    answer, tokens = generate_answer(system_prompt, question)
    cost = (tokens / 1000.0) * TOKEN_PRICE_PER_1K

    log_usage(client_name, tokens, cost)
    log_chat(question, answer, tone)

    if not validate_answer(answer):
        return RagResult(
            answer=answer,
            tokens=tokens,
            cost=cost,
            confidence=confidence,
            retrieved=chunks,
            ok=False,
            reason="failed_quality_check",
        )

    return RagResult(
        answer=answer,
        tokens=tokens,
        cost=cost,
        confidence=confidence,
        retrieved=chunks,
        ok=True,
        reason="ok",
    )