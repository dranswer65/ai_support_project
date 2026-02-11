# -----------------------------------
# Day 48D — Zendesk Webhook Simulation
# -----------------------------------

from qa.agent_reply_validator import validate_agent_reply


def handle_zendesk_reply_webhook(webhook_payload: dict) -> dict:
    """
    Simulates Zendesk sending agent reply to our system.

    NEVER throws.
    ALWAYS returns structured validation response.
    """

    try:
        # -------------------------------------------------
        # 1️⃣ Extract required data from webhook
        # -------------------------------------------------

        agent_reply = webhook_payload.get("agent_reply", "")
        ticket_id = webhook_payload.get("ticket_id")
        custom_fields = webhook_payload.get("custom_fields", {})

        # Language constraints come from custom fields
        agent_constraints = {
            "reply_language": custom_fields.get("reply_language", "en"),
            "language_lock": custom_fields.get("language_lock", False),
            "rtl_required": custom_fields.get("rtl_required", False),
        }

        # -------------------------------------------------
        # 2️⃣ Validate agent reply
        # -------------------------------------------------

        validation_result = validate_agent_reply(
            reply_text=agent_reply,
            agent_constraints=agent_constraints,
            enforcement_level="warn"  # change to block / autocorrect if needed
        )

        # -------------------------------------------------
        # 3️⃣ Simulated Decision Handling
        # -------------------------------------------------

        return {
            "ticket_id": ticket_id,
            "validation_status": validation_result["status"],
            "issues": validation_result.get("issues", []),
            "corrected_text": validation_result.get("corrected_text"),
            "action": validation_result.get("action"),
        }

    except Exception as e:
        return {
            "status": "webhook_error",
            "details": str(e),
            "action": "allow_with_warning"
        }
