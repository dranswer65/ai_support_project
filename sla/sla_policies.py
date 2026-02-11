# sla/sla_policies.py
# -----------------------------------
# Day 43A — SLA Policies
# -----------------------------------

SLA_POLICIES = {
    "low": {
        "first_response_sec": 300,     # 5 min
        "resolution_sec": 86400,        # 24 hours
    },
    "normal": {
        "first_response_sec": 120,     # 2 min
        "resolution_sec": 14400,        # 4 hours
    },
    "high": {
        "first_response_sec": 60,      # 1 min
        "resolution_sec": 3600,         # 1 hour
    },
    "critical": {
        "first_response_sec": 30,      # 30 sec
        "resolution_sec": 900,          # 15 min
    },
}
