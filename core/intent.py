# core/intent.py

def detect_intent(text: str, language: str = "en") -> str:
    t = (text or "").strip().lower()
    if not t:
        return "UNCLEAR"

    if language == "ar":
        if any(k in t for k in ["السلام", "مرحبا", "اهلا", "أهلاً", "هلا"]):
            return "GREETING"
    else:
        if t in {"hi", "hello", "hey"}:
            return "GREETING"

    if language == "ar":
        if any(k in t for k in ["شكرا", "شكرًا", "جزاك"]):
            return "THANKS"
    else:
        if "thank" in t or t in {"thx", "thanks"}:
            return "THANKS"

    if language == "ar":
        if any(k in t for k in ["استرجاع", "ارجاع", "إرجاع", "تعويض", "استرداد", "refund", "return"]):
            return "REFUND"
    else:
        if any(k in t for k in ["refund", "return", "replacement", "exchange"]):
            return "REFUND"

    if language == "ar":
        if any(k in t for k in ["طلب", "طلبي", "توصيل", "الشحنة", "تتبع", "متأخر", "تأخير"]):
            return "ORDER_STATUS"
    else:
        if any(k in t for k in ["order", "delivery", "shipment", "tracking", "delayed", "late"]):
            return "ORDER_STATUS"

    if len(t) < 4:
        return "UNCLEAR"

    return "GENERAL"