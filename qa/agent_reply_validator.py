# -----------------------------------
# Agent Reply Validator
# Day 48C — Agent Reply Auto-Correction & Warning System
# -----------------------------------

from typing import Dict
import re


# =========================================================
# Public Entry Point
# =========================================================

def validate_agent_reply(
    reply_text: str,
    agent_constraints: Dict,
    enforcement_level: str = "warn",  # warn | autocorrect | block
) -> Dict:
    """
    Validate agent reply against language constraints.

    NEVER throws.
    ALWAYS returns a structured decision object.

    enforcement_level:
      - warn        → allow + warning
      - autocorrect → auto-translate when safe
      - block       → block until fixed
    """

    try:
        required_language = agent_constraints.get("reply_language", "en")
        language_lock = agent_constraints.get("language_lock", False)
        rtl_required = agent_constraints.get("rtl_required", False)

        detected_language = detect_reply_language(reply_text)

        issues = []

        # -------------------------------------------------
        # 1️⃣ Language mismatch
        # -------------------------------------------------
        if language_lock and detected_language != required_language:
            issues.append("language_mismatch")

        # -------------------------------------------------
        # 2️⃣ RTL formatting check (Arabic expected)
        # -------------------------------------------------
        if rtl_required and required_language == "ar":
            if not contains_arabic_characters(reply_text):
                issues.append("rtl_expected_but_missing")

        # -------------------------------------------------
        # 3️⃣ Decision Logic
        # -------------------------------------------------
        if not issues:
            return {
                "status": "approved",
                "corrected_text": reply_text,
                "issues": [],
                "action": "allow"
            }

        # --- WARN MODE ---
        if enforcement_level == "warn":
            return {
                "status": "warning",
                "corrected_text": reply_text,
                "issues": issues,
                "action": "allow_with_warning"
            }

        # --- AUTOCORRECT MODE ---
        if enforcement_level == "autocorrect":
            if "language_mismatch" in issues:
                corrected = auto_translate_stub(reply_text, required_language)
                return {
                    "status": "autocorrected",
                    "corrected_text": corrected,
                    "issues": issues,
                    "action": "auto_translated"
                }

            return {
                "status": "warning",
                "corrected_text": reply_text,
                "issues": issues,
                "action": "allow_with_warning"
            }

        # --- BLOCK MODE ---
        if enforcement_level == "block":
            return {
                "status": "blocked",
                "corrected_text": None,
                "issues": issues,
                "action": "prevent_send"
            }

        # Fallback safety
        return {
            "status": "approved",
            "corrected_text": reply_text,
            "issues": [],
            "action": "allow"
        }

    except Exception as e:
        # NEVER break agent workflow
        return {
            "status": "validator_error",
            "corrected_text": reply_text,
            "issues": ["validator_internal_error"],
            "action": "allow_with_warning",
            "details": str(e)
        }


# =========================================================
# Language Detection (Lightweight Heuristic)
# =========================================================

def detect_reply_language(text: str) -> str:
    """
    Lightweight heuristic language detection.
    Replace with proper NLP detection in production.
    """

    if contains_arabic_characters(text):
        return "ar"

    if re.search(r"[a-zA-Z]", text):
        return "en"

    return "unknown"


def contains_arabic_characters(text: str) -> bool:
    """
    Detect Arabic Unicode block.
    """
    return re.search(r"[\u0600-\u06FF]", text) is not None


# =========================================================
# Auto Translation Stub (Simulation Only)
# =========================================================

def auto_translate_stub(text: str, target_language: str) -> str:
    """
    Placeholder for real translation service.

    In production:
      - Call internal translation API
      - Log translation event
      - Track QA override
      - Store original + translated version
      - Mark audit trail

    This is ONLY a simulation stub.
    """

    if target_language == "ar":
        return f"[AUTO-TRANSLATED TO AR] {text}"

    if target_language == "en":
        return f"[AUTO-TRANSLATED TO EN] {text}"

    return f"[AUTO-TRANSLATED] {text}"
