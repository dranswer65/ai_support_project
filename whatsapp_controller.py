# whatsapp_controller.py
# GCC Hospital WhatsApp Controller
# FINAL STABLE VERSION

from __future__ import annotations

from typing import Dict, Any, Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from core.engine import run_engine
from core.session_store_pg import (
    get_session,
    upsert_session,
)

# =========================================================
# CONFIG
# =========================================================

DEFAULT_TENANT = "default"


# =========================================================
# HELPERS
# =========================================================

def _norm(text: str) -> str:
    return (text or "").strip()


def _is_return_to_menu(text: str) -> bool:
    t = text.strip().lower()
    return t in ["0", "menu", "القائمة"]


def _is_thanks(text: str) -> bool:
    t = text.lower()

    thanks_words = [
        "thanks",
        "thank you",
        "thx",
        "appreciate",
        "شكرا",
        "شكراً",
        "مشكور",
        "يعطيك العافية",
        "جزاك الله خير",
    ]

    return any(w in t for w in thanks_words)


# =========================================================
# MAIN HANDLER
# =========================================================

async def handle_message(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    user_id: str,
    message_text: str,
) -> Tuple[str, Dict[str, Any]]:

    tenant = tenant_id or DEFAULT_TENANT
    incoming = _norm(message_text)

    # -----------------------------------------------------
    # Load Session
    # -----------------------------------------------------
    session = await get_session(
        db,
        user_id=user_id,
        tenant_id=tenant,
    ) or {}

    # -----------------------------------------------------
    # HUMAN HANDOFF MODE
    # -----------------------------------------------------
    if session.get("handoff_active"):

        # allow return to menu
        if _is_return_to_menu(incoming):
            session["handoff_active"] = False

        else:
            # silence while human handles
            if _is_thanks(incoming):
                lang = session.get("language", "en")

                reply = (
                    "You're welcome ✅ Reception will assist you shortly."
                    if lang == "en"
                    else "العفو ✅ سيقوم موظف الاستقبال بمساعدتك قريباً."
                )

                await upsert_session(
                    db,
                    user_id=user_id,
                    tenant_id=tenant,
                    session=session,
                )

                return reply, {"handoff": True}

            return "", {"handoff": True}

    # -----------------------------------------------------
    # RUN AI ENGINE
    # -----------------------------------------------------
    result = run_engine(
        message_text=incoming,
        session=session,
    )

    reply_text = result.reply_text
    session = result.session
    actions = result.actions or {}

    # -----------------------------------------------------
    # ESCALATION ACTION
    # -----------------------------------------------------
    if actions.get("handoff"):

        session["handoff_active"] = True

        ref = actions.get("ref", "RX" + user_id[-4:])

        lang = session.get("language", "en")

        reply_text = (
            f"Connecting you to Reception ✅ Ref: #{ref}\n"
            "Reply 0 for menu"
            if lang == "en"
            else
            f"تم تحويلك لموظف الاستقبال ✅ رقم الطلب #{ref}\n"
            "اكتب 0 للعودة للقائمة"
        )

    # -----------------------------------------------------
    # SAVE SESSION
    # -----------------------------------------------------
    await upsert_session(
        db,
        user_id=user_id,
        tenant_id=tenant,
        session=session,
    )

    return reply_text, actions