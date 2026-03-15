from webhooks.zendesk_webhook_handler import handle_zendesk_reply_webhook


# Simulated Zendesk webhook payload
webhook_payload = {
    "ticket_id": "ZD-12345",
    "agent_reply": "Hello, your issue has been resolved.",
    "custom_fields": {
        "reply_language": "ar",       # Arabic required
        "language_lock": True,
        "rtl_required": True
    }
}


result = handle_zendesk_reply_webhook(webhook_payload)

print("WEBHOOK VALIDATION RESULT →")
print(result)
