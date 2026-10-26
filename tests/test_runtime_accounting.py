"""Constant-size exact budget projection across transactions and recovery."""

import json
import sqlite3
from decimal import Decimal, localcontext

import pytest

from ecoalign_forge.runtime.accounting import BUDGET_KEY
from ecoalign_forge.runtime.control import budget_snapshot
from ecoalign_forge.runtime.journal import Journal


def attempt(journal, request_id="request", reservation=".3"):
    journal.request(request_id, "judge", [], {})
    return journal.start_attempt(request_id, reservation)


def test_exact_decimal_totals_do_not_use_float_or_context_rounding(tmp_path):
    j = Journal(tmp_path)
    for index, cost in enumerate(
        ["12345678901234567890.12345678901234567890", ".00000000000000000001"]
    ):
        aid = attempt(j, str(index))
        with localcontext() as ctx:
            ctx.prec = 6
            j.finish_attempt(aid, "succeeded", cost=cost)
    assert budget_snapshot(j)["estimated_usd"] == "12345678901234567890.12345678901234567891"
    assert budget_snapshot(j)["total_committed_usd"] == "12345678901234567890.12345678901234567891"
    assert budget_snapshot(j)["unresolved_reserved_usd"] == "0"
    j.close()


@pytest.mark.parametrize("choice", ["retry", "skip"])
def test_unknown_reservation_retained_after_recovery_and_explicit_resolution(tmp_path, choice):
    j = Journal(tmp_path)
    aid = attempt(j)
    before = budget_snapshot(j)
    j.close()
    j = Journal(tmp_path)
    j.recover_inflight()
    j.resolve_unknown({aid: choice})
    assert budget_snapshot(j) == before
    assert Decimal(before["total_committed_usd"]) == Decimal(".3")
    if choice == "retry":
        second = j.start_attempt("request", ".3")
        j.save_response(second, {"content": "a" * 100_000})
        assert Decimal(budget_snapshot(j)["total_committed_usd"]) == Decimal(".6")
        j.finish_attempt(second, "succeeded", cost=".1", usage={"total_tokens": 4})
        assert Decimal(budget_snapshot(j)["total_committed_usd"]) == Decimal(".4")
        assert budget_snapshot(j)["attempts_without_usage_or_settlement"] == 1
    j.close()


def test_repeated_settlement_replaces_contribution_without_double_counting(tmp_path):
    j = Journal(tmp_path)
    aid = attempt(j)
    j.finish_attempt(aid, "parse_failed", cost=".1", usage={"total_tokens": 7})
    before = budget_snapshot(j)
    j.finish_attempt(aid, "succeeded", cost=".1", usage={"total_tokens": 7})
    assert budget_snapshot(j) == before
    j.finish_attempt(aid, "unknown")
    after = budget_snapshot(j)
    assert after["attempts"] == 1 and after["attempts_with_usage"] == 0
    assert after["supplier_reported_usage"] is None
    assert Decimal(after["total_committed_usd"]) == Decimal(".3")
    assert after["estimated_usd"] == "0"
    j.close()


def test_failed_settlement_rolls_back_projection_with_attempt(tmp_path):
    j = Journal(tmp_path)
    aid = attempt(j)
    before = budget_snapshot(j)
    j.db.execute("""CREATE TRIGGER reject_settlement BEFORE UPDATE OF cost ON attempts
                    BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        j.finish_attempt(aid, "succeeded", cost=".1")
    assert budget_snapshot(j) == before
    assert j.attempts()[0]["cost"] is None
    j.close()


def test_legacy_projection_rebuilt_narrowly_and_readonly_does_not_write(tmp_path):
    j = Journal(tmp_path)
    aid = attempt(j)
    j.save_response(aid, {"content": "response" * 100_000})
    j.finish_attempt(aid, "succeeded", cost=".1", usage={"prompt_tokens": 3})
    expected = budget_snapshot(j)
    j.db.execute("DELETE FROM meta WHERE key=?", (BUDGET_KEY,))
    j.close()
    ro = Journal.readonly(tmp_path)
    statements = []
    ro.db.set_trace_callback(statements.append)
    assert budget_snapshot(ro) == expected
    assert ro.db.total_changes == 0 and ro.get(BUDGET_KEY) is None
    assert "SELECT cost,reservation,usage FROM attempts" in statements
    assert not any("SELECT *" in sql for sql in statements)
    ro.close()
    j = Journal(tmp_path)
    assert j.get(BUDGET_KEY) == expected
    j.close()


def test_old_writer_changes_invalidate_cache_and_rebuild(tmp_path):
    j = Journal(tmp_path)
    aid = attempt(j)
    j.db.execute(
        "UPDATE attempts SET cost='.15',usage=? WHERE id=?", (json.dumps({"total_tokens": 8}), aid)
    )
    assert j.get(BUDGET_KEY) is None
    assert Decimal(budget_snapshot(j)["estimated_usd"]) == Decimal(".15")
    j.db.execute("DELETE FROM attempts WHERE id=?", (aid,))
    assert budget_snapshot(j)["attempts"] == 0
    assert budget_snapshot(j)["supplier_reported_usage"] is None
    j.close()


@pytest.mark.parametrize("count", [100, 1000, 10000])
def test_budget_hot_path_reads_one_row_regardless_of_history(tmp_path, monkeypatch, count):
    j = Journal(tmp_path)
    # Build history efficiently in one transaction; triggers model older writers.
    j.request("q", "judge", [], {})
    with j.db:
        j.db.execute("BEGIN IMMEDIATE")
        j.db.executemany(
            "INSERT INTO attempts(id,request_id,number,state,reservation,cost,result,started) VALUES(?,'q',?,'succeeded','.2','.1',?,0)",
            ((str(i), i, "x" * 4096) for i in range(count)),
        )
    expected = budget_snapshot(j)
    statements, rows = [], []
    j.db.set_trace_callback(statements.append)

    def row_factory(cursor, row):
        rows.append(row)
        return sqlite3.Row(cursor, row)

    j.db.row_factory = row_factory
    monkeypatch.setattr(j, "attempts", lambda *_: pytest.fail("no full-history reads"))
    for _ in range(10):
        assert budget_snapshot(j) == expected
    assert len(rows) == len(statements) == 10
    assert all("FROM meta" in sql and "budget_totals_v1" in sql for sql in statements)
    assert expected["attempts"] == count
    assert Decimal(expected["estimated_usd"]) == Decimal(count) / 10
    j.close()
