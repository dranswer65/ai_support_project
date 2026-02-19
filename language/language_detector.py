# -----------------------------------
# Day 44A — Language Detection (Enterprise Safe)
# -----------------------------------

import re

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

def detect_language(text: str) -> str:
    """
    Detect language from user message.

    Returns:
        'ar' → Arabic detected
        'en' → default fallback

    Enterprise behavior:
    - Detect Arabic even if mixed with English
    - Fast (no heavy NLP)
    - Safe for real-time WhatsApp usage
    """

    if not text:
        return "en"

    # If ANY Arabic character exists → treat as Arabic
    if _ARABIC_RE.search(text):
        return "ar"

    return "en"
