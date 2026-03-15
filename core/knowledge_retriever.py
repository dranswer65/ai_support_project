from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal


_AR_RE = re.compile(r"[\u0600-\u06FF]")


def _looks_arabic(text_in: str) -> bool:
    return bool(_AR_RE.search(text_in or ""))


def _normalize(text_in: str) -> str:
    return " ".join((text_in or "").strip().lower().split())


def _tokenize(text_in: str) -> List[str]:
    s = _normalize(text_in)
    s = re.sub(r"[^\w\s\u0600-\u06FF]", " ", s)
    parts = [p for p in s.split() if len(p) >= 2]
    return parts[:12]


def _language_candidates(user_message: str, forced_language: Optional[str] = None) -> List[str]:
    lang = (forced_language or "").strip().lower()
    if lang in {"en", "ar"}:
        return [lang, "ar" if lang == "en" else "en"]

    if _looks_arabic(user_message):
        return ["ar", "en"]

    return ["en", "ar"]


def _category_hints(user_message: str) -> List[str]:
    m = _normalize(user_message)

    hints: List[str] = []

    insurance_words = [
        "insurance", "insured", "approved insurance", "accepted insurance",
        "bupa", "tawuniya", "medgulf", "axa",
        "تأمين", "التأمين", "التأمينات", "بوبا", "تكافل", "ميدغلف", "اكسا", "التعاونية",
    ]
    hours_words = [
        "hours", "opening", "open", "close", "timing", "timings", "working hours",
        "ramadan", "ramadhan",
        "ساعات", "الدوام", "مواعيد العمل", "اوقات العمل", "أوقات العمل", "رمضان", "يفتح", "يغلق",
    ]
    location_words = [
        "location", "address", "where", "map", "maps", "branch", "directions",
        "الموقع", "العنوان", "وين", "اين", "أين", "خرائط", "اتجاهات", "فرع",
    ]
    services_words = [
        "service", "services", "offer", "offers", "treat", "treatment", "specialty", "specialities", "specialties",
        "خدمات", "تقدمون", "توفرون", "علاج", "تخصص", "التخصصات",
    ]
    doctors_words = [
        "doctor", "doctors", "female doctor", "male doctor", "consultant", "consultants",
        "doctor available", "doctor list", "available doctors",
        "دكتور", "دكتورة", "طبيب", "طبيبة", "استشاري", "الأطباء", "الاطباء",
    ]
    faq_words = [
        "faq", "question", "questions", "policy", "policies",
        "سؤال", "أسئلة", "سياسة", "سياسات",
    ]

    if any(w in m for w in insurance_words):
        hints.append("insurance")
    if any(w in m for w in hours_words):
        hints.append("hours")
    if any(w in m for w in location_words):
        hints.append("location")
    if any(w in m for w in services_words):
        hints.append("services")
    if any(w in m for w in doctors_words):
        hints.append("doctors")
    if any(w in m for w in faq_words):
        hints.append("faq")

    return hints or ["faq", "services", "insurance", "location", "hours", "doctors", "other"]


def _phrase_candidates(user_message: str) -> List[str]:
    m = _normalize(user_message)

    phrases: List[str] = []

    known_phrases = [
        "working hours in ramadan",
        "ramadan working hours",
        "ramadan hours",
        "opening hours",
        "working hours",
        "accepted insurance",
        "approved insurance",
        "doctor list",
        "available doctors",
        "location",
        "address",
        "google maps",
        "مواعيد العمل في رمضان",
        "اوقات العمل في رمضان",
        "أوقات العمل في رمضان",
        "ساعات العمل في رمضان",
        "مواعيد العمل",
        "أوقات العمل",
        "اوقات العمل",
        "التأمينات المعتمدة",
        "التأمين المعتمد",
        "الأطباء المتوفرون",
        "الاطباء المتوفرون",
        "الموقع",
        "العنوان",
    ]

    for p in known_phrases:
        if p in m:
            phrases.append(p)

    if len(m.split()) >= 2:
        phrases.append(m)

    return phrases[:6]


def _score_row(
    *,
    row: Dict[str, Any],
    lang: str,
    cat: str,
    tokens: List[str],
    phrases: List[str],
) -> int:
    score = 0

    title = _normalize(str(row.get("title") or ""))
    content = _normalize(str(row.get("content") or ""))
    category = str(row.get("category") or "").strip().lower()
    row_lang = str(row.get("language") or "").strip().lower()

    full = f"{title} {content}".strip()

    if row_lang == lang:
        score += 6

    if category == cat:
        score += 8

    for phrase in phrases:
        p = _normalize(phrase)
        if not p:
            continue
        if p == title:
            score += 30
        elif p in title:
            score += 18
        elif p in content:
            score += 10
        elif p in full:
            score += 6

    for tok in tokens[:8]:
        tok = _normalize(tok)
        if not tok:
            continue
        if tok == title:
            score += 10
        elif tok in title:
            score += 6
        elif tok in content:
            score += 3
        elif tok in full:
            score += 2

    if "ramadan" in tokens or "ramadhan" in tokens or "رمضان" in tokens:
        if "ramadan" in title or "ramadhan" in title or "رمضان" in title:
            score += 15
        if "ramadan" in content or "ramadhan" in content or "رمضان" in content:
            score += 8

    return score


async def search_knowledge_base(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_message: str,
    language: Optional[str] = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """
    Improved keyword retrieval for clinic KB.

    Returns top matching rows from tenant_knowledge_base.
    """
    tenant_id = (tenant_id or "").strip()
    user_message = (user_message or "").strip()

    if not tenant_id or not user_message:
        return []

    langs = _language_candidates(user_message, language)
    cats = _category_hints(user_message)
    tokens = _tokenize(user_message)
    phrases = _phrase_candidates(user_message)

    if not tokens:
        tokens = [user_message[:40].lower()]

    t1 = tokens[0] if len(tokens) > 0 else ""
    t2 = tokens[1] if len(tokens) > 1 else t1
    t3 = tokens[2] if len(tokens) > 2 else t1

    scored_rows: List[Dict[str, Any]] = []

    for lang in langs:
        for cat in cats[:4]:
            query = text(
                """
                SELECT
                    id,
                    tenant_id,
                    category,
                    title,
                    content,
                    language,
                    is_active,
                    sort_order,
                    source,
                    created_at,
                    updated_at
                FROM tenant_knowledge_base
                WHERE tenant_id = :tenant_id
                  AND is_active = TRUE
                  AND language = :language
                  AND (:category = '' OR category = :category)
                  AND (
                        LOWER(title) LIKE :q1
                        OR LOWER(content) LIKE :q1
                        OR LOWER(title) LIKE :q2
                        OR LOWER(content) LIKE :q2
                        OR LOWER(title) LIKE :q3
                        OR LOWER(content) LIKE :q3
                      )
                ORDER BY
                    sort_order ASC,
                    updated_at DESC,
                    id DESC
                LIMIT 20;
                """
            )

            res = await db.execute(
                query,
                {
                    "tenant_id": tenant_id,
                    "language": lang,
                    "category": cat,
                    "q1": f"%{t1.lower()}%",
                    "q2": f"%{t2.lower()}%",
                    "q3": f"%{t3.lower()}%",
                },
            )

            rows = [dict(r) for r in res.mappings().all()]
            for row in rows:
                row["_score"] = _score_row(
                    row=row,
                    lang=lang,
                    cat=cat,
                    tokens=tokens,
                    phrases=phrases,
                )
                scored_rows.append(row)

    best_by_id: Dict[Any, Dict[str, Any]] = {}
    for row in scored_rows:
        rid = row.get("id")
        prev = best_by_id.get(rid)
        if prev is None or int(row.get("_score", 0)) > int(prev.get("_score", 0)):
            best_by_id[rid] = row

    final_rows = list(best_by_id.values())
    final_rows.sort(
        key=lambda x: (
            -int(x.get("_score", 0)),
            int(x.get("sort_order", 0)),
            str(x.get("updated_at", "")),
        )
    )

    return final_rows[: max(1, min(limit, 5))]


async def build_knowledge_answer(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_message: str,
    language: Optional[str] = None,
    clinic_name: Optional[str] = None,
) -> Optional[str]:
    """
    Returns a direct answer from KB if a good enough match exists.
    Otherwise returns None.
    """
    matches = await search_knowledge_base(
        db,
        tenant_id=tenant_id,
        user_message=user_message,
        language=language,
        limit=3,
    )

    if not matches:
        return None

    top = matches[0]
    top_score = int(top.get("_score", 0))

    if top_score < 8:
        return None

    lang = str(top.get("language") or language or "en").strip().lower()
    title = str(top.get("title") or "").strip()
    content = str(top.get("content") or "").strip()
    clinic_name = (clinic_name or "").strip()

    if not content:
        return None

    if clinic_name:
        content = content.replace("{clinic_name}", clinic_name)

    if lang == "ar":
        if title:
            return f"{title}\n{content}"
        return content

    if title:
        return f"{title}\n{content}"
    return content


async def get_knowledge_answer(
    *,
    tenant_id: str,
    query: str,
    language: Optional[str] = None,
    clinic_name: Optional[str] = None,
) -> Optional[str]:
    """
    Safe public wrapper used by controller/engine.
    Opens its own DB session and returns a direct KB answer if found.
    """
    tenant_id = (tenant_id or "").strip()
    query = (query or "").strip()

    if not tenant_id or not query:
        return None

    async with AsyncSessionLocal() as db:
        return await build_knowledge_answer(
            db,
            tenant_id=tenant_id,
            user_message=query,
            language=language,
            clinic_name=clinic_name,
        )