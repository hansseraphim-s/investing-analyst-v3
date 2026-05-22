"""Neon Postgres journal store.

Replaces v2's local SQLite journal. The agent writes here; the Next.js
dashboard reads here. Schema source of truth: shared/schema.sql.
"""

from .neon import NeonJournal

__all__ = ["NeonJournal"]
