# handoff_builder.py

from datetime import datetime

def build_handoff_payload(
    user_id: str,
    current_state: str,
    last_user_message: str,
    last_intent: str,
    decision_rule: str,
    decision_reason: str,
    kpi_signals: list,
):
    return {
        "meta": {
            "source": "SupportPilot-AI",
            "generated_at": datetime.utcnow().isoformat() + "Z",
        },
        "user": {
            "user_id": user_id,
        },
        "conversation": {
            "current_state": current_state,
            "tries": None,
            "last_user_message": last_user_message,
            "last_intent": last_intent,
        },
        "decision_trace": {
            "rule": decision_rule,
            "reason": decision_reason,
        },
        "kpi_flags": kpi_signals,
    }
