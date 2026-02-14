from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from session_manager import SessionManager

app = FastAPI()
sessions = SessionManager()


def reply(message: str):
    """
    WhatsApp expects plain text (or TwiML if using Twilio).
    For now, we return plain text.
    """
    return PlainTextResponse(message)


@app.post("/whatsapp")
async def whatsapp_entry(request: Request):
    payload = await request.form()

    user_id = payload.get("From")  # WhatsApp number
    message = (payload.get("Body") or "").strip().lower()

    session = sessions.get(user_id)
    state = session["state"]

    # -------- STATE MACHINE --------

    if state == "START":
        sessions.set_state(user_id, "GREETING")
        return reply(
            "Hi 👋 Thanks for contacting SupportPilot.\n"
            "How can I assist you today?"
        )

    elif state == "GREETING":
        sessions.set_state(user_id, "INTENT_DETECTION")
        return reply(
            "Got it 👍 Could you please tell me more details so I can help you?"
        )

    elif state == "INTENT_DETECTION":
        sessions.update_data(user_id, "intent", message)
        sessions.set_state(user_id, "INFO_REQUIRED")
        return reply(
            "To assist you further, may I have your **order ID** please?"
        )

    elif state == "INFO_REQUIRED":
        sessions.update_data(user_id, "order_id", message)
        sessions.set_state(user_id, "ACTION_IN_PROGRESS")
        return reply(
            "Thank you. I’m checking this for you now. Please allow a moment ⏳"
        )

    elif state == "ACTION_IN_PROGRESS":
        sessions.set_state(user_id, "RESOLVED")
        return reply(
            "Your request has been processed ✅\n"
            "Is there anything else I can help you with?"
        )

    elif state == "RESOLVED":
        if message in {"no", "no thanks", "nothing", "nope"}:
            sessions.set_state(user_id, "CLOSED")
            return reply(
                "Thank you for contacting SupportPilot. Have a great day 😊"
            )
        else:
            sessions.set_state(user_id, "INTENT_DETECTION")
            return reply(
                "Sure — please let me know what else I can help you with."
            )

    elif state == "ESCALATION":
        return reply(
            "A human support agent will assist you shortly 👩‍💻👨‍💻\n"
            "Thank you for your patience."
        )

    elif state == "CLOSED":
        return reply(
            "This conversation is closed. You’re welcome to message again anytime."
        )

    # Fallback safety net
    return reply(
        "I’m here to help. Please tell me how I can assist you."
    )
