from pydantic import BaseModel
from typing import Optional
import time
import json
import os

# Expected reset windows per agent. Claude Pro resets daily usage every ~8h in practice.
RESET_INTERVALS = {
    "claude": {"daily": 8 * 3600,  "weekly": 7 * 86400},
    "gemini": {"daily": 24 * 3600, "weekly": 7 * 86400},
    "codex":  {"daily": 24 * 3600, "weekly": 7 * 86400},
    "grok":   {"daily": 24 * 3600, "weekly": 7 * 86400},
}


class AgentQuota(BaseModel):
    exhausted: bool = False
    exhausted_at: Optional[float] = None   # Unix ts when we detected exhaustion
    reset_at: Optional[float] = None       # Unix ts of expected reset (may be exact from CLI output)
    limit_type: Optional[str] = None       # "daily" or "weekly"
    real_daily_pct: Optional[float] = None
    real_weekly_pct: Optional[float] = None


class QuotaManager:
    def __init__(self, state_file: str = "leadagent-data/quota.json"):
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            with open(self.state_file, "r") as f:
                raw = json.load(f)
            result = {}
            for agent in RESET_INTERVALS:
                entry = raw.get(agent, {})
                # Migrate legacy format (had daily_tokens, weekly_tokens, etc.)
                if "daily_tokens" in entry or "weekly_tokens" in entry:
                    result[agent] = AgentQuota(
                        exhausted=entry.get("exhausted", False),
                        real_daily_pct=entry.get("real_daily_pct"),
                        real_weekly_pct=entry.get("real_weekly_pct"),
                    )
                else:
                    known = {k: v for k, v in entry.items() if k in AgentQuota.model_fields}
                    result[agent] = AgentQuota(**known)
            return result
        return {agent: AgentQuota() for agent in RESET_INTERVALS}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump({k: v.model_dump() for k, v in self.state.items()}, f, indent=2)

    # ── reset tracking ────────────────────────────────────────────────────────

    def reset_seconds_remaining(self, agent: str) -> Optional[int]:
        state = self.state.get(agent)
        if not state or not state.exhausted:
            return None
        if state.reset_at:
            return max(0, int(state.reset_at - time.time()))
        if state.exhausted_at:
            interval = RESET_INTERVALS.get(agent, {}).get(state.limit_type or "daily", 86400)
            return max(0, int((state.exhausted_at + interval) - time.time()))
        return None

    def _auto_clear_resets(self):
        changed = False
        for agent, state in self.state.items():
            if not state.exhausted:
                continue
            remaining = self.reset_seconds_remaining(agent)
            if remaining == 0:
                state.exhausted = False
                state.exhausted_at = None
                state.reset_at = None
                state.limit_type = None
                changed = True
        if changed:
            self._save_state()

    # ── write methods ─────────────────────────────────────────────────────────

    def set_exhausted(self, agent: str, exhausted: bool = True, limit_type: str = "daily"):
        if agent not in self.state:
            return
        state = self.state[agent]
        state.exhausted = exhausted
        if exhausted:
            state.exhausted_at = state.exhausted_at or time.time()
            state.limit_type = limit_type
            if not state.reset_at:
                interval = RESET_INTERVALS.get(agent, {}).get(limit_type, 86400)
                state.reset_at = time.time() + interval
        else:
            state.exhausted_at = None
            state.reset_at = None
            state.limit_type = None
        self._save_state()

    def set_reset_from_cli(self, agent: str, reset_in_seconds: int, limit_type: str = "daily"):
        """Use exact reset time parsed directly from CLI output."""
        if agent not in self.state:
            return
        state = self.state[agent]
        state.exhausted = True
        state.exhausted_at = state.exhausted_at or time.time()
        state.reset_at = time.time() + reset_in_seconds
        state.limit_type = limit_type
        self._save_state()

    def sync_real_usage(self, agent: str, daily_pct: Optional[float] = None, weekly_pct: Optional[float] = None) -> list[str]:
        alerts = []
        if agent not in self.state:
            return alerts
        state = self.state[agent]

        # Check thresholds (80% cliff as per debate consensus)
        old_daily = state.real_daily_pct or 0
        old_weekly = state.real_weekly_pct or 0

        is_currently_exhausted = False
        if daily_pct is not None:
            state.real_daily_pct = daily_pct
            if daily_pct >= 80 and old_daily < 80:
                alerts.append(f"⚠️  {agent} daily quota reached {daily_pct:.0f}% (80% threshold)")
            if daily_pct >= 100:
                is_currently_exhausted = True

        if weekly_pct is not None:
            state.real_weekly_pct = weekly_pct
            if weekly_pct >= 80 and old_weekly < 80:
                alerts.append(f"⚠️  {agent} weekly quota reached {weekly_pct:.0f}% (80% threshold)")
            if weekly_pct >= 100:
                is_currently_exhausted = True

        # If we are currently exhausted but the real usage is low, clear it
        if state.exhausted and not is_currently_exhausted:
            if daily_pct is not None or weekly_pct is not None:
                # Only clear if we actually got a fresh reading
                state.exhausted = False
                state.exhausted_at = None
                state.reset_at = None
                state.limit_type = None
                print(f"[QuotaManager] {agent} exhaustion cleared via real usage sync (Daily: {daily_pct}%, Weekly: {weekly_pct}%)")
        elif is_currently_exhausted and not state.exhausted:
            self.set_exhausted(agent, True, "daily" if (daily_pct or 0) >= 100 else "weekly")

        self._save_state()
        return alerts

    # ── read methods ──────────────────────────────────────────────────────────

    def get_usage_percentage(self, agent: str) -> Optional[float]:
        state = self.state.get(agent)
        if not state:
            return None
        # Use daily as primary indicator for solo dev flow
        return state.real_daily_pct

    def get_available_agents(self) -> list:
        self._auto_clear_resets()
        return [a for a, s in self.state.items() if not s.exhausted]

    def get_wait_status(self) -> str:
        lines = []
        for agent, state in self.state.items():
            if not state.exhausted:
                continue
            remaining = self.reset_seconds_remaining(agent)
            if remaining is None or remaining == 0:
                lines.append(f"  {agent}: reset imminent")
            else:
                h, rem = divmod(remaining, 3600)
                m = rem // 60
                lines.append(f"  {agent} ({state.limit_type or 'daily'} limit): resets in {h}h {m:02d}m")
        return "\n".join(lines) if lines else "All agents available."


quota_manager = QuotaManager()
