#!/usr/bin/env python3
"""Tests for review/ledger.py. Run: python3 -m unittest review/test_ledger.py"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger as L  # noqa: E402

A = "src/main/java/a/A.java"
B = "src/main/java/b/B.java"


def finding(file=A, category="concurrency", severity="high", line=10, message="정원 확인 후 저장해서 경합이 생겨요."):
    return {"category": category, "severity": severity, "file": file, "line": line, "message": message}


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        for f in (A, B):
            (self.repo / f).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / f).write_text("class X {}\n")
        self.ledger = L.load_ledger(Path(self.tmp.name) / "none.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_merge(self, findings, scope, kind, at, commit="c1"):
        return L.merge(self.ledger, findings, set(scope), kind, self.repo, commit, at)

    def test_new_finding_gets_first_seen(self):
        self.run_merge([finding()], [A, B], "full", "2026-11-26T21:00:00+09:00")
        [f] = L.open_findings(self.ledger)
        self.assertEqual(f["firstSeen"], "2026-11-26T21:00:00+09:00")
        self.assertEqual(self.ledger["lastReviewedCommit"], "c1")

    def test_first_seen_kept_when_line_and_numbers_change(self):
        self.run_merge([finding(line=10, message="정원 100명 확인 후 저장해서 경합이 생겨요.")], [A], "full", "T1")
        stats = self.run_merge([finding(line=14, message="정원 200명 확인 후 저장해서 경합이 생겨요!")], [A], "changed", "T2")
        [f] = L.open_findings(self.ledger)
        self.assertEqual(stats["kept"], 1)
        self.assertEqual(f["firstSeen"], "T1")
        self.assertEqual(f["line"], 14)

    def test_closes_when_file_reviewed_again_and_not_found(self):
        self.run_merge([finding()], [A], "full", "T1")
        stats = self.run_merge([], [A], "changed", "T2")
        self.assertEqual(stats["closed"], 1)
        self.assertEqual(L.open_findings(self.ledger), [])

    def test_stays_open_when_file_not_in_scope(self):
        self.run_merge([finding(file=A)], [A, B], "full", "T1")
        stats = self.run_merge([], [B], "changed", "T2")
        self.assertEqual(stats["closed"], 0)
        self.assertEqual(len(L.open_findings(self.ledger)), 1)

    def test_closes_when_file_deleted_even_without_review(self):
        self.run_merge([finding(file=B)], [A, B], "full", "T1")
        (self.repo / B).unlink()
        stats = self.run_merge([], [], "none", "T2")
        self.assertEqual(stats["closed"], 1)

    def test_reopen_keeps_first_seen(self):
        self.run_merge([finding()], [A], "full", "T1")
        self.run_merge([], [A], "changed", "T2")
        stats = self.run_merge([finding()], [A], "changed", "T3")
        [f] = L.open_findings(self.ledger)
        self.assertEqual(stats["reopened"], 1)
        self.assertEqual(f["firstSeen"], "T1")

    def test_drops_paths_that_do_not_exist(self):
        stats = self.run_merge([finding(file="src/main/java/nope/Nope.java")], [A], "full", "T1")
        self.assertEqual(stats["dropped"], 1)
        self.assertEqual(L.open_findings(self.ledger), [])

    def test_duplicates_in_one_run_collapse(self):
        self.run_merge([finding(line=10), finding(line=30)], [A], "full", "T1")
        self.assertEqual(len(L.open_findings(self.ledger)), 1)

    def test_different_category_is_a_different_finding(self):
        self.run_merge([finding(category="concurrency"), finding(category="transaction")], [A], "full", "T1")
        self.assertEqual(len(L.open_findings(self.ledger)), 2)

    def test_open_findings_sorted_by_severity(self):
        self.run_merge([finding(severity="low", message="낮음"), finding(file=B, severity="high", message="높음")], [A, B], "full", "T1")
        self.assertEqual([f["severity"] for f in L.open_findings(self.ledger)], ["high", "low"])

    def test_structured_output_is_schema_checked(self):
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text(json.dumps({"findings": [dict(finding(), severity="HIGH")]}), encoding="utf-8")
        with self.assertRaises(SystemExit):
            L.read_structured(str(bad))

    def test_null_line_allowed(self):
        good = Path(self.tmp.name) / "good.json"
        good.write_text(json.dumps({"findings": [finding(line=None)]}), encoding="utf-8")
        self.assertEqual(L.read_structured(str(good))[0]["line"], None)


if __name__ == "__main__":
    unittest.main()
