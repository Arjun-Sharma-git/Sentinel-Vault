"""authz freshness tracking and sign-in throttling

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09

- authz_state: one row; a statement-level trigger on grants, group_members and folders bumps
  `version` inside the writing transaction, so "OpenFGA is behind" is never missed, whichever
  code path (API, CLI, scanner, manual SQL) made the change.
- login_throttle: failed sign-in counters and lockouts.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCESS_TABLES = ("grants", "group_members", "folders")


def upgrade() -> None:
    op.create_table(
        "authz_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("synced_version", sa.BigInteger(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name="authz_state_single_row"),
    )
    # Starts "behind" (1 > 0): until the first successful sync, the SQL rules answer.
    op.execute("INSERT INTO authz_state (id, version, synced_version) VALUES (1, 1, 0)")
    op.execute(
        """
        CREATE FUNCTION bump_authz_version() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE authz_state SET version = version + 1 WHERE id = 1;
            RETURN NULL;
        END;
        $$
        """
    )
    for table in ACCESS_TABLES:
        # Folder renames and description edits don't change access; only rows and parents do.
        events = (
            "INSERT OR DELETE OR UPDATE OF parent_id" if table == "folders" else "INSERT OR UPDATE OR DELETE"
        )
        op.execute(
            f"CREATE TRIGGER {table}_bump_authz_version AFTER {events} OR TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION bump_authz_version()"
        )

    op.create_table(
        "login_throttle",
        sa.Column("key", sa.String(length=300), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("login_throttle")
    for table in ACCESS_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_bump_authz_version ON {table}")
    op.execute("DROP FUNCTION IF EXISTS bump_authz_version()")
    op.drop_table("authz_state")
