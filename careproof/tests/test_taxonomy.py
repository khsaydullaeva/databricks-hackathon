"""Exercise the taxonomy SELECT and patterns locally using synthetic text."""

import re
import sqlite3
from pathlib import Path

import pytest

from careproof_agent.runner import guard_statement, split_sql


@pytest.fixture(scope="module")
def taxonomy():
    sql = Path(__file__).resolve().parents[1] / "sql" / "06_taxonomy.sql"
    (statement,) = split_sql(sql.read_text())
    guard_statement(statement, {"target_schema": "workspace.careproof"})
    heading, query = statement.split("\nAS\n", 1)
    assert heading.startswith("CREATE OR REPLACE TABLE workspace.careproof.service_taxonomy\n")
    # Run the shared SQL query with local equivalents for Spark string functions.
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.create_function("regexp_replace", 3, lambda value, pattern, replacement:
                           re.sub(pattern, replacement, value))
        db.create_function("concat", -1, lambda *parts: "".join(parts))
        return db.execute(query).fetchall()


def test_unique_normalized_aliases(taxonomy):
    aliases = [row["alias"] for row in taxonomy]
    assert all(re.fullmatch(r"[a-z0-9]+(?: [a-z0-9]+)*", alias) for alias in aliases)
    assert len(aliases) == len(set(aliases))
    assert len({row["service_key"] for row in taxonomy}) >= 6


@pytest.mark.parametrize(("alias", "text", "expected"), [
    ("haemodialysis", "We provide haemodialysis.", "candidate_claim"),
    ("mri", "Offers on-site MRI.", "candidate_claim"),
    ("nicu", "Provides NICU care.", "candidate_claim"),
    ("emergency c section", "Performs emergency C-section.", "candidate_claim"),
    ("cardiac surgery", "Offers cardiac surgery.", "candidate_claim"),
    ("trauma care", "Provides trauma care.", "candidate_claim"),
    ("dialysis", "Does not currently offer dialysis.", "negated"),
    ("dialysis", "No longer offers dialysis.", "negated"),
    ("mri", "MRI scans are not available.", "negated"),
    ("nicu", "NICU", "mention_only"),
    ("dialysis", "Offers dialysis education.", "mention_only"),
    ("dialysis", "Referred elsewhere for dialysis.", "mention_only"),
    ("mri", "Plans to offer MRI.", "mention_only"),
    ("mri", "Offers fMRI and general imaging.", "no_match"),
    ("dialysis", "Offers nephrology.", "no_match"),
    ("emergency c section", "Offers maternity care and elective C-section.", "no_match"),
    ("cardiac surgery", "Offers cardiology.", "no_match"),
    ("nicu", "Offers adult ICU services.", "no_match"),
])
def test_taxonomy_context(taxonomy, alias, text, expected):
    row = next(row for row in taxonomy if row["alias"] == alias)
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

    if not re.search(row["alias_regex"], normalized):
        outcome = "no_match"
    elif re.search(row["negation_regex"], normalized):
        outcome = "negated"
    elif re.search(row["mention_only_regex"], normalized):
        outcome = "mention_only"
    elif re.search(row["offering_regex"], normalized):
        outcome = "candidate_claim"
    else:
        outcome = "mention_only"

    assert outcome == expected
