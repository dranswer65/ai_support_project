# --------------------------------------------------
# Audit Metrics Aggregator
# Day 49B — Compliance Dashboard Engine
# --------------------------------------------------

from datetime import datetime
from typing import List, Dict, Optional
from collections import Counter


# =========================================================
# Utility — Safe Timestamp Parsing
# =========================================================

def _parse_timestamp(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts.replace("Z", ""))
    except Exception:
        return None


# =========================================================
# Core Aggregator Class
# =========================================================

class AuditMetricsAggregator:
    """
    Aggregates structured audit log events into
    dashboard-ready compliance metrics.

    NEVER processes raw message content.
    ONLY structured audit metadata.
    """

    def __init__(self, events: List[Dict]):
        self.events = events or []

    # -------------------------------------------------
    # Public Entry
    # -------------------------------------------------

    def generate_summary(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        client_id: Optional[str] = None
    ) -> Dict:
        """
        Generate compliance metrics summary.

        Filters:
        - start_time (ISO string)
        - end_time   (ISO string)
        - client_id
        """

        filtered_events = self._filter_events(start_time, end_time, client_id)

        return self._aggregate_metrics(filtered_events)

    # -------------------------------------------------
    # Filtering Layer
    # -------------------------------------------------

    def _filter_events(
        self,
        start_time: Optional[str],
        end_time: Optional[str],
        client_id: Optional[str]
    ) -> List[Dict]:

        start_dt = _parse_timestamp(start_time) if start_time else None
        end_dt = _parse_timestamp(end_time) if end_time else None

        results = []

        for event in self.events:

            # --- Time Filter ---
            ts = _parse_timestamp(event.get("timestamp", ""))
            if ts:
                if start_dt and ts < start_dt:
                    continue
                if end_dt and ts > end_dt:
                    continue

            # --- Client Filter ---
            if client_id:
                if event.get("client_id") != client_id:
                    continue

            results.append(event)

        return results

    # -------------------------------------------------
    # Aggregation Layer
    # -------------------------------------------------

    def _aggregate_metrics(self, events: List[Dict]) -> Dict:

        counter = Counter()
        priority_counter = Counter()

        for event in events:

            event_type = event.get("event_type")

            # Count all event types
            counter[event_type] += 1

            # Track escalation priority distribution
            if event_type == "ticket_escalated":
                priority = event.get("metadata", {}).get("priority")
                if priority:
                    priority_counter[priority] += 1

        summary = {
            "total_events": len(events),
            "conversation_restarts": counter.get("conversation_restart", 0),
            "conversations_closed": counter.get("conversation_closed", 0),
            "sla_breaches": counter.get("sla_breach", 0),
            "escalations": counter.get("ticket_escalated", 0),
            "incident_triggers": counter.get("incident_mode_triggered", 0),
            "language_violations": counter.get("agent_language_violation", 0),
            "auto_corrections": counter.get("agent_reply_auto_corrected", 0),
            "blocked_replies": counter.get("agent_reply_blocked", 0),
            "priority_distribution": dict(priority_counter),
        }

        return summary
    # -------------------------------------------------
    # Advanced KPI Layer
    # -------------------------------------------------

    def generate_kpis(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        client_id: Optional[str] = None
    ) -> Dict:
        """
        Returns percentage-based KPIs + health score.
        """

        filtered_events = self._filter_events(start_time, end_time, client_id)

        summary = self._aggregate_metrics(filtered_events)

        total_events = summary.get("total_events", 0)
        total_escalations = summary.get("escalations", 0)
        total_sla = summary.get("sla_breaches", 0)
        total_violations = summary.get("language_violations", 0)
        total_autocorrect = summary.get("auto_corrections", 0)
        total_blocked = summary.get("blocked_replies", 0)
        total_restarts = summary.get("conversation_restarts", 0)

        # -------------------------------------------------
        # Safe Division Helper
        # -------------------------------------------------

        def pct(value, base):
            if base == 0:
                return 0.0
            return round((value / base) * 100, 2)

        # -------------------------------------------------
        # Core Rates
        # -------------------------------------------------

        escalation_rate = pct(total_escalations, total_events)
        sla_breach_rate = pct(total_sla, total_escalations)
        violation_rate = pct(total_violations, total_events)
        autocorrect_rate = pct(total_autocorrect, total_violations)
        block_rate = pct(total_blocked, total_violations)
        restart_rate = pct(total_restarts, total_events)

        # -------------------------------------------------
        # Compliance Health Score (0–100)
        # Weighted enterprise model
        # -------------------------------------------------

        health_score = self._calculate_health_score(
            escalation_rate,
            sla_breach_rate,
            violation_rate,
            block_rate,
            restart_rate
        )

        return {
            "total_events": total_events,
            "rates": {
                "escalation_rate_pct": escalation_rate,
                "sla_breach_rate_pct": sla_breach_rate,
                "language_violation_rate_pct": violation_rate,
                "auto_correction_rate_pct": autocorrect_rate,
                "block_rate_pct": block_rate,
                "restart_rate_pct": restart_rate,
            },
            "health_score": health_score
        }

    # -------------------------------------------------
    # Health Score Calculation
    # -------------------------------------------------

    def _calculate_health_score(
        self,
        escalation_rate,
        sla_breach_rate,
        violation_rate,
        block_rate,
        restart_rate
    ) -> int:
        """
        Enterprise weighted compliance health score.
        """

        score = 100

        # Escalations reduce health
        score -= escalation_rate * 0.2

        # SLA breaches heavily penalized
        score -= sla_breach_rate * 0.4

        # Language violations moderate penalty
        score -= violation_rate * 0.2

        # Blocked replies small penalty (shows guardrails working)
        score -= block_rate * 0.1

        # Restart anomalies small penalty
        score -= restart_rate * 0.1

        # Clamp score
        score = max(0, min(100, score))

        return int(round(score))
