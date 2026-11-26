#!/usr/bin/env python3
"""Tests for report/build_report.py with recorded-style Sonar responses (no Sonar, no Claude).

Run: python3 -m unittest report/test_build_report.py

The fixtures follow the SonarQube Web API response shapes; once a real analysis exists,
compare them with real responses (T10 completion check).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_report as B  # noqa: E402

KEY = "rehab"
FILE = "src/main/java/com/example/demo/domain/event/service/EventService.java"


def sonar_fixtures(folder, commit):
    metrics = [
        ("coverage", "PERCENT"), ("new_coverage", "PERCENT"),
        ("duplicated_lines_density", "PERCENT"), ("new_duplicated_lines_density", "PERCENT"),
        ("ncloc", "INT"), ("lines_to_cover", "INT"), ("uncovered_lines", "INT"),
        ("new_lines_to_cover", "INT"), ("new_uncovered_lines", "INT"),
        ("software_quality_reliability_issues", "INT"), ("software_quality_security_issues", "INT"),
        ("software_quality_maintainability_issues", "INT"),
        ("new_software_quality_reliability_issues", "INT"), ("new_software_quality_security_issues", "INT"),
        ("new_software_quality_maintainability_issues", "INT"),
        ("software_quality_reliability_rating", "RATING"), ("software_quality_security_rating", "RATING"),
        ("software_quality_maintainability_rating", "RATING"),
        ("new_software_quality_reliability_rating", "RATING"), ("new_software_quality_security_rating", "RATING"),
        ("new_software_quality_maintainability_rating", "RATING"),
        ("security_hotspots", "INT"), ("security_hotspots_reviewed", "PERCENT"),
        ("new_security_hotspots", "INT"), ("new_security_hotspots_reviewed", "PERCENT"),
        ("accepted_issues", "INT"), ("new_accepted_issues", "INT"), ("new_violations", "INT"),
        ("bugs", "INT"), ("code_smells", "INT"), ("vulnerabilities", "INT"),
    ]
    values = {
        "coverage": "72.43", "duplicated_lines_density": "2.1", "ncloc": "1483",
        "lines_to_cover": "612", "uncovered_lines": "169",
        "software_quality_reliability_issues": "2", "software_quality_security_issues": "0",
        "software_quality_maintainability_issues": "6",
        "software_quality_reliability_rating": "3.0", "software_quality_security_rating": "1.0",
        "software_quality_maintainability_rating": "1.0",
        "security_hotspots": "2", "security_hotspots_reviewed": "100.0", "accepted_issues": "1",
    }
    new_values = {
        "new_coverage": "84.2", "new_duplicated_lines_density": "1.2", "new_lines_to_cover": "95",
        "new_uncovered_lines": "15", "new_software_quality_reliability_issues": "0",
        "new_software_quality_security_issues": "0", "new_software_quality_maintainability_issues": "0",
        "new_software_quality_reliability_rating": "1.0", "new_software_quality_security_rating": "1.0",
        "new_software_quality_maintainability_rating": "1.0", "new_security_hotspots": "1",
        "new_security_hotspots_reviewed": "100.0", "new_accepted_issues": "0",
    }
    files = {
        "metrics_search.json": {"metrics": [{"key": k, "type": t} for k, t in metrics], "total": len(metrics)},
        "measures_component.json": {"component": {"key": KEY, "measures":
            [{"metric": k, "value": v} for k, v in values.items()] +
            [{"metric": k, "period": {"index": 1, "value": v}} for k, v in new_values.items()]}},
        "qualitygates_project_status.json": {"projectStatus": {"status": "OK", "conditions": [
            {"status": "OK", "metricKey": "new_violations", "comparator": "GT", "errorThreshold": "0", "actualValue": "0"},
            {"status": "OK", "metricKey": "new_security_hotspots_reviewed", "comparator": "LT", "errorThreshold": "100", "actualValue": "100.0"},
            {"status": "OK", "metricKey": "new_coverage", "comparator": "LT", "errorThreshold": "80", "actualValue": "84.2"},
            {"status": "OK", "metricKey": "new_duplicated_lines_density", "comparator": "GT", "errorThreshold": "3", "actualValue": "1.2"},
            {"status": "OK", "metricKey": "new_software_quality_reliability_rating", "comparator": "GT", "errorThreshold": "1", "actualValue": "1"},
        ]}},
        "issues_search.json": {"total": 4, "p": 1, "ps": 500, "issues": [
            {"rule": "java:S2259", "component": f"{KEY}:{FILE}", "line": 71, "message": "NPE possible",
             "impacts": [{"softwareQuality": "RELIABILITY", "severity": "MEDIUM"}]},
            {"rule": "java:S2142", "component": f"{KEY}:{FILE}", "line": 88, "message": "Re-interrupt",
             "impacts": [{"softwareQuality": "RELIABILITY", "severity": "MEDIUM"},
                         {"softwareQuality": "MAINTAINABILITY", "severity": "MEDIUM"}]},
            {"rule": "secrets:S6290", "component": f"{KEY}:{FILE}", "line": 3, "message": "secret here",
             "impacts": [{"softwareQuality": "SECURITY", "severity": "BLOCKER"}]},
            {"rule": "java:S1451", "component": KEY, "message": "Project-level issue",
             "impacts": [{"softwareQuality": "MAINTAINABILITY", "severity": "LOW"}]},
        ]},
        "new_code_periods_show.json": {"projectKey": KEY, "type": "PREVIOUS_VERSION", "inherited": True},
        "project_analyses_search.json": {"analyses": [{"key": "A1", "revision": commit}]},
    }
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (folder / name).write_text(json.dumps(data), encoding="utf-8")


STUB = """#!/usr/bin/env python3
import json, os
mode = os.environ.get("STUB_OPINION", "good")
paragraphs = {
    "good": ["품질 게이트를 통과했어요. 새 코드 커버리지는 84.2%예요.", "Claude 리뷰는 high 1개를 짚었어요."],
    "invented": ["커버리지가 지난번보다 12.5% 올랐어요."],
    "quote": ["조회 실패가 HTTP 500으로 나가는 지적이 있어요."],
}[mode]
print(json.dumps({"type": "result", "subtype": "success", "structured_output": {"paragraphs": paragraphs}}))
"""


class BuildReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.target = t / "target"
        (self.target / "build/test-results/test").mkdir(parents=True)
        (self.target / FILE).parent.mkdir(parents=True)
        (self.target / FILE).write_text("class EventService {}\n")
        (self.target / "build/test-results/test/TEST-a.xml").write_text(
            '<testsuite name="a" tests="4" failures="0" errors="0" skipped="1"/>')
        (self.target / "build/test-results/test/TEST-b.xml").write_text(
            '<testsuite name="b" tests="2" failures="0" errors="0" skipped="0"/>')
        git = ["git", "-C", str(self.target), "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", "x"], check=True)
        self.commit = subprocess.run(git[:3] + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

        self.fixtures = t / "sonar"
        sonar_fixtures(self.fixtures, self.commit)
        self.findings = t / "findings.json"
        self.findings.write_text(json.dumps({"findings": [
            {"category": "concurrency", "severity": "high", "file": FILE, "line": 58,
             "message": "정원 확인 후 저장해서 경합이 생겨요.", "firstSeen": "2026-11-26T21:00:00+09:00"},
            {"category": "scale", "severity": "medium", "file": FILE, "line": None,
             "message": "페이징 없이 전체를 가져와요.", "firstSeen": "2026-11-26T21:00:00+09:00"},
            {"category": "failure", "severity": "medium", "file": FILE, "line": 62,
             "message": "orElseThrow()가 NoSuchElementException을 던져서 500으로 나가요.",
             "firstSeen": "2026-11-26T21:00:00+09:00"},
        ]}, ensure_ascii=False), encoding="utf-8")
        self.yml = t / "projects.yml"
        self.yml.write_text("projects:\n  - id: rehab\n    name: rehab\n    repo: dasu-archive/rehab\n    branch: main\n")
        self.state = t / "state"
        self.out = t / "out"
        stub = t / "claude-stub"
        stub.write_text(STUB)
        stub.chmod(0o755)
        os.environ["CNY_CLAUDE_BIN"] = str(stub)
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "dummy-token-for-tests"
        os.environ.pop("STUB_OPINION", None)

    def tearDown(self):
        self.tmp.cleanup()
        for k in ("CNY_CLAUDE_BIN", "CLAUDE_CODE_OAUTH_TOKEN", "STUB_OPINION"):
            os.environ.pop(k, None)

    def run_report(self, at="2026-11-26T21:00:00+09:00", findings=True):
        args = ["--project-id", "rehab", "--projects-yml", str(self.yml), "--target-dir", str(self.target),
                "--run-at", at, "--branch", "main", "--build", "7", "--state-dir", str(self.state),
                "--out", str(self.out), "--sonar-fixtures", str(self.fixtures)]
        if findings:
            args += ["--findings", str(self.findings)]
        return B.main(args)

    def latest(self, where=None):
        return json.loads(((where or self.out) / "projects/rehab/latest.json").read_text(encoding="utf-8"))

    def test_full_report_passes_checks_and_updates_state(self):
        self.assertEqual(self.run_report(), 0)
        latest = self.latest()
        m = latest["metrics"]
        self.assertEqual(m["overall"]["coverage"], 72.4)                 # rounded to 1 decimal
        self.assertEqual(m["overall"]["issues"], {"reliability": 2, "security": 0, "maintainability": 6})
        self.assertEqual(m["overall"]["ratings"]["reliability"], "C")
        self.assertEqual(m["newCode"]["coverage"], 84.2)                 # from period.value
        self.assertEqual(m["newCode"]["hotspots"], {"total": 1, "reviewedPercent": 100.0})
        gate = latest["qualityGate"]
        self.assertEqual(gate["status"], "passed")
        self.assertEqual([c["metric"] for c in gate["conditions"]],
                         ["newIssues", "newHotspotsReviewed", "newCoverage", "newDuplication", "newReliabilityRating"])
        self.assertEqual(gate["conditions"][4]["threshold"], "A")
        self.assertEqual(latest["tests"], {"total": 6, "failed": 0, "skipped": 1})
        rules = [i["rule"] for i in latest["sonarIssues"]]
        self.assertNotIn("secrets:S6290", rules)                         # never published
        project_level = [i for i in latest["sonarIssues"] if i["rule"] == "java:S1451"][0]
        self.assertIsNone(project_level["file"])
        self.assertEqual(latest["claude"]["counts"]["concurrency"], 1)
        self.assertEqual(latest["newCodeDefinition"], {"type": "previous_version", "value": None})
        self.assertIsNotNone(latest["opinion"])
        self.assertTrue((self.state / "projects/rehab/history.json").is_file())

    def test_invented_number_drops_opinion(self):
        os.environ["STUB_OPINION"] = "invented"
        self.assertEqual(self.run_report(), 0)
        self.assertIsNone(self.latest()["opinion"])

    def test_number_quoted_from_a_finding_message_is_kept(self):
        os.environ["STUB_OPINION"] = "quote"
        self.assertEqual(self.run_report(), 0)
        self.assertIsNotNone(self.latest()["opinion"])

    def test_failed_review_nulls_claude_and_opinion(self):
        self.assertEqual(self.run_report(findings=False), 0)
        latest = self.latest()
        self.assertIsNone(latest["claude"])
        self.assertIsNone(latest["opinion"])
        index = json.loads((self.out / "projects.json").read_text(encoding="utf-8"))
        self.assertIsNone(index["projects"][0]["claudeFindings"])

    def test_history_appends_and_same_commit_replaces(self):
        self.assertEqual(self.run_report(at="2026-11-26T21:00:00+09:00"), 0)
        self.assertEqual(self.run_report(at="2026-11-26T22:00:00+09:00"), 0)
        runs = json.loads((self.out / "projects/rehab/history.json").read_text(encoding="utf-8"))["runs"]
        self.assertEqual([r["at"] for r in runs], ["2026-11-26T22:00:00+09:00"])

    def test_missing_analysis_for_commit_fails(self):
        (self.fixtures / "project_analyses_search.json").write_text(json.dumps({"analyses": [{"revision": "0" * 40}]}))
        self.assertEqual(self.run_report(), 1)
        self.assertFalse((self.state / "projects.json").exists())

    def test_leak_blocks_and_state_untouched(self):
        data = json.loads((self.fixtures / "issues_search.json").read_text())
        data["issues"][0]["message"] = "connect to 192.168.77.23 failed"
        (self.fixtures / "issues_search.json").write_text(json.dumps(data))
        self.assertEqual(self.run_report(), 1)
        self.assertFalse((self.state / "projects.json").exists())


if __name__ == "__main__":
    unittest.main()
