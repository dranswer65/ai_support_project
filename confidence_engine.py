# confidence_engine.py
from core.state import ConversationState

def calculate_confidence(session: dict, emotion: str | None = None) -> float:
    score = 1.0

    if emotion == "angry":
        score -= 0.25
    elif emotion == "abusive":
        score -= 0.60

    tries = int(session.get("tries", 0))
    if tries >= 1:
        score -= 0.20
    if tries >= 3:
        score -= 0.30

    intent = session.get("intent")
    if not intent:
        score -= 0.25

    state = session.get("state")
    if state in {ConversationState.RESOLVED.value, ConversationState.INFO_REQUIRED.value}:
        score += 0.15

    abuse = int(session.get("abuse_strikes", 0))
    if abuse >= 1:
        score -= 0.15
    if abuse >= 2:
        score -= 0.30

    return max(0.0, round(score, 2))


def should_escalate(confidence: float, threshold: float = 0.35) -> bool:
    return confidence < threshold