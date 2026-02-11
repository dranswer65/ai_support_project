import json
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
FILE = BASE / "usage" / "usage_log.json"


def log(client, tokens, cost):

    FILE.parent.mkdir(exist_ok=True)

    if FILE.exists():
        data = json.load(open(FILE))
    else:
        data = []

    data.append({
        "client": client,
        "tokens": tokens,
        "cost": cost,
        "time": datetime.utcnow().isoformat()
    })

    json.dump(data, open(FILE, "w"), indent=2)
