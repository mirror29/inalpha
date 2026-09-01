"""Merge event-evolution and waitlist migration heads.

Revision ID: 0044
Revises: 0043, 0043_waitlist
Create Date: 2026-08-28
"""

from __future__ import annotations

revision: str = "0044"
down_revision: tuple[str, str] = ("0043", "0043_waitlist")
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
