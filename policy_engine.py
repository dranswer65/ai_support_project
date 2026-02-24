# policy_engine.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class PolicyDecision:
    action: str
    reply: str = ""
    rule: str = ""
    reason: str = ""
    rag_input: Optional[str] = None
    prefix: str = ""


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _is_greeting(t: str) -> bool:
    t = _norm(t)
    return t in {"hi", "hello", "hey", "السلام عليكم", "مرحبا", "أهلاً", "اهلا"}


def _is_thanks(t: str) -> bool:
    t = _norm(t)
    return t in {"thanks", "thank you", "thx", "شكرا", "شكرًا", "جزاك الله خير"}


def _is_goodbye(t: str) -> bool:
    t = _norm(t)
    return t in {"bye", "goodbye", "see you", "مع السلامة", "سلام", "الى اللقاء", "إلى اللقاء"}


def _is_no(t: str) -> bool:
    t = _norm(t)
    return t in {"no", "nope", "nah", "لا", "لا شكرا", "لا شكرًا", "ليس الآن", "مو", "مش"}


def _is_ack(t: str) -> bool:
    t = _norm(t)
    return t in {"ok", "okay", "k", "sure", "alright", "تمام", "تم", "اوكي", "حسنًا", "حسنا"}


def _needs_order_id(intent: str) -> bool:
    # Your current intent set in core/intent.py:
    # GREETING, REFUND, ORDER_STATUS, THANKS, UNCLEAR, GENERAL
    return intent in {"ORDER_STATUS", "REFUND"}


def _prefix_greeting_once(session: Dict[str, Any], language: str, body: str) -> str:
    if session.get("has_greeted"):
        return body
    session["has_greeted"] = True
    if language == "ar":
        return f"مرحبًا! شكرًا لتواصلك معنا.\n\n{body}"
    return f"Hello! Thank you for contacting us.\n\n{body}"


def decide_next_action(session: Dict[str, Any], language: str, text: str) -> PolicyDecision:
    intent = session.get("intent") or "GENERAL"
    t = _norm(text)

    # Hard close if user says goodbye
    if _is_goodbye(t):
        if language == "ar":
            return PolicyDecision(action="CLOSE", reply="مع السلامة! إذا احتجت أي شيء، أنا موجود. ✅")
        return PolicyDecision(action="CLOSE", reply="Goodbye! If you need anything else, I’m here. ✅")

    # If user says thanks and we are resolved: close politely or ask if anything else
    if _is_thanks(t):
        if language == "ar":
            return PolicyDecision(action="GREET_ONLY", reply=_prefix_greeting_once(session, language, "على الرحب والسعة ✅ هل هناك أي شيء آخر يمكنني مساعدتك به؟"))
        return PolicyDecision(action="GREET_ONLY", reply=_prefix_greeting_once(session, language, "You’re welcome ✅ Is there anything else I can help you with?"))

    # If user says "no" after a response → close (avoid loops)
    if _is_no(t):
        if language == "ar":
            return PolicyDecision(action="CLOSE", reply=_prefix_greeting_once(session, language, "شكرًا لك. إذا احتجت أي مساعدة لاحقًا أنا موجود. 🌟"))
        return PolicyDecision(action="CLOSE", reply=_prefix_greeting_once(session, language, "Thank you. If you need any help later, I’m here. 🌟"))

    # Greeting only (but do NOT block the real intent if text includes refund/order)
    if _is_greeting(t) and intent == "GREETING":
        if language == "ar":
            return PolicyDecision(action="GREET_ONLY", reply="مرحبًا! كيف يمكنني مساعدتك اليوم؟")
        return PolicyDecision(action="GREET_ONLY", reply="Hello! How may I assist you today?")

    # Require order id for ORDER_STATUS / REFUND (Amazon: greet once + ask for required verification)
    if _needs_order_id(intent) and not session.get("order_id"):
        if language == "ar":
            msg = "لأتمكن من المساعدة بدقة، هل يمكنك تزويدي برقم الطلب (Order ID)؟\nإذا لم يكن متوفرًا، شارك رقم الجوال أو البريد المسجل."
        else:
            msg = "To help you accurately, could you please share your Order ID?\nIf you don’t have it, share your registered phone number or email."
        msg = _prefix_greeting_once(session, language, msg)
        return PolicyDecision(action="ASK_ORDER_ID", reply=msg, rule="missing_order_id", reason="Order/refund intent requires Order ID")

    # If user is unclear
    if intent == "UNCLEAR":
        if language == "ar":
            msg = "شكرًا لرسالتك. هل يمكنك توضيح طلبك أكثر؟ هل الموضوع يتعلق بطلب/توصيل/استرجاع؟"
        else:
            msg = "Thank you for your message. Could you please share a bit more detail — is this about an order, delivery, or refund/return?"
        msg = _prefix_greeting_once(session, language, msg)
        return PolicyDecision(action="CLARIFY", reply=msg)

    # Normal: call RAG with a HOLD prefix for order/refund flows
    prefix = ""
    if intent in {"ORDER_STATUS", "REFUND"} and session.get("order_id"):
        if language == "ar":
            prefix = f"شكرًا لمشاركة رقم الطلب ({session['order_id']}). يرجى الانتظار لحظة — أنا أراجع التفاصيل الآن.\n\n"
        else:
            prefix = f"Thanks for sharing the Order ID ({session['order_id']}). Please allow me a moment — I’m checking the details now.\n\n"

    # RAG input structured
    rag_input = text
    if session.get("order_id") and intent in {"ORDER_STATUS", "REFUND"}:
        rag_input = f"Order ID: {session.get('order_id')}\nCustomer message: {text}"

    return PolicyDecision(action="CALL_RAG", rag_input=rag_input, prefix=_prefix_greeting_once(session, language, prefix) if prefix else "")