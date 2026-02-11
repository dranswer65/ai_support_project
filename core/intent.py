def detect_intent(text: str):
    text = text.lower()

    if text in {"hi", "hello", "hey"}:
        return "GREETING"

    if "refund" in text:
        return "REFUND"

    if "order" in text or "delivery" in text:
        return "ORDER_STATUS"

    if "thank" in text:
        return "THANKS"

    if len(text) < 4:
        return "UNCLEAR"

    return "GENERAL"
