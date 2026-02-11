import json
import csv
import os
from datetime import datetime

LOG_DIR = "logs"
JSON_LOG_FILE = os.path.join(LOG_DIR, "conversations.jsonl")
CSV_LOG_FILE = os.path.join(LOG_DIR, "conversations.csv")

os.makedirs(LOG_DIR, exist_ok=True)

CSV_HEADERS = [
    "timestamp",
    "user_id",
    "state_before",
    "intent",
    "emotion",
    "action_taken",
    "state_after",
    "decision_rule",
    "decision_reason",
    "kpi_signals",
]

def log_event(
    user_id,
    state_before,
    intent,
    emotion,
    action_taken,
    state_after,
    decision_rule,
    decision_reason,
    kpi_signals,
):
    timestamp = datetime.utcnow().isoformat()

    record = {
        "timestamp": timestamp,
        "user_id": user_id,
        "state_before": state_before,
        "intent": intent,
        "emotion": emotion,
        "action_taken": action_taken,
        "state_after": state_after,
        "decision_rule": decision_rule,
        "decision_reason": decision_reason,
        "kpi_signals": kpi_signals,
    }

    # -------------------------------
    # JSONL (audit-friendly)
    # -------------------------------

    with open(JSON_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    # -------------------------------
    # CSV (business-friendly)
    # -------------------------------

    file_exists = os.path.isfile(CSV_LOG_FILE)

    with open(CSV_LOG_FILE, "a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_HEADERS)

        if not file_exists:
            writer.writeheader()

        writer.writerow({
            **record,
            "kpi_signals": ",".join(kpi_signals),
        })
