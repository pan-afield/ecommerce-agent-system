"""Static guards for execution identity and money; not PostgreSQL runtime tests."""

import re
from pathlib import Path

import pytest

PRISMA_DIRECTORY = Path(__file__).resolve().parents[3] / "packages/database/prisma"
MIGRATION_PATH = (
    PRISMA_DIRECTORY
    / "migrations/20260916090000_create_refund_executions/migration.sql"
)


@pytest.mark.parametrize(
    ("column", "field", "length"),
    [
        ("refund_application_id", "refundApplicationId", 64),
        ("idempotency_key", "idempotencyKey", 128),
    ],
)
def test_execution_identity_is_required_and_unique_in_sql_and_prisma(
    column: str, field: str, length: int
) -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    schema = (PRISMA_DIRECTORY / "schema.prisma").read_text(encoding="utf-8")
    model = re.search(r"model\s+RefundExecution\s*\{([^}]+)\}", schema)
    assert model is not None, "Prisma must own the refund execution table."
    assert '@@map("refund_executions")' in model.group(1)
    assert re.search(
        rf'"{column}"\s+VARCHAR\({length}\)\s+NOT\s+NULL\s+UNIQUE\b',
        sql,
        re.IGNORECASE,
    ), f"Each {column} must identify at most one execution, including concurrent inserts."
    declaration = re.search(rf"^\s*{field}\s+String\s+([^\n]+)", model.group(1), re.M)
    assert declaration is not None, "Execution identity must be a required string."
    assert "@unique" in declaration.group(1)
    assert f'@map("{column}")' in declaration.group(1)
    assert f"@db.VarChar({length})" in declaration.group(1)


def test_execution_preserves_approved_application_money_precision() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    schema = (PRISMA_DIRECTORY / "schema.prisma").read_text(encoding="utf-8")
    approved_amount = re.search(
        r"requestedAmount\s+Decimal\s+[^\n]*@db\.Decimal\((\d+),\s*(\d+)\)", schema
    )
    execution_amount = re.search(
        r'"amount"\s+(?:DECIMAL|NUMERIC)\((\d+),\s*(\d+)\)\s+NOT\s+NULL',
        sql,
        re.IGNORECASE,
    )
    model_amount = re.search(
        r"model\s+RefundExecution\s*\{[^}]*\bamount\s+Decimal\s+"
        r"@db\.Decimal\((\d+),\s*(\d+)\)",
        schema,
    )
    assert approved_amount is not None
    assert execution_amount is not None
    assert model_amount is not None
    assert execution_amount.groups() == model_amount.groups() == approved_amount.groups()
