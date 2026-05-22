"""Optional Claude commentary layer.

This is deliberately OUT of the decision hot path. v1's core flaw was putting
an LLM in charge of every trade: slow, expensive, nondeterministic, and
impossible to backtest. Here the deterministic strategy makes every decision;
Claude only writes a short plain-English review of what the rules did, if the
operator opts in (ENABLE_ADVISOR=true). It cannot place, block, or alter a
trade. Disabled by default; never imported unless enabled.
"""

from __future__ import annotations

_SYSTEM = (
    "You are a concise risk reviewer for a RULES-BASED equity trading system. "
    "You do NOT make trading decisions — the deterministic strategy already "
    "did. Given the cycle summary, write 3-5 sentences: what the rules did, "
    "the main risk currently carried, and one thing the operator should watch. "
    "No hype, no predictions, no price targets. If the data looks thin, say so."
)


class ClaudeAdvisor:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        if not api_key:
            raise ValueError("ClaudeAdvisor requires ANTHROPIC_API_KEY")
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def review(self, cycle_summary: str) -> str:
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=400,
                system=_SYSTEM,
                messages=[{"role": "user", "content": cycle_summary}],
            )
            return "".join(
                b.text for b in resp.content if getattr(b, "text", "")
            ).strip()
        except Exception as e:  # advisory only — never break the cycle
            return f"(advisor unavailable: {e})"
