#!/usr/bin/env python3
"""Tests for report/allowlist.py. Run: python3 -m unittest report/test_allowlist.py"""

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import allowlist as A  # noqa: E402

SAMPLES = HERE.parent / "samples" / "v1"
LATEST = "projects/rehab/latest.json"


class AllowlistTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "site"
        shutil.copytree(SAMPLES, self.root, ignore=shutil.ignore_patterns("*.md"))
        self.latest = json.loads((self.root / LATEST).read_text(encoding="utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, data):
        (self.root / LATEST).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def blocked(self, mutate, environ=None):
        data = copy.deepcopy(self.latest)
        mutate(data)
        self.put(data)
        _, problems = A.scan_tree(self.root, environ or {})
        return problems

    def set_message(self, text):
        return lambda d: d["claude"]["findings"][0].__setitem__("message", text)

    def test_samples_are_clean(self):
        files, problems = A.scan_tree(self.root, {})
        self.assertEqual(problems, [])
        self.assertEqual(len(files), 4)

    def test_token_shapes(self):
        for token in ["ghp_" + "a" * 36, "github_pat_" + "b" * 40, "sk-ant-oat01-" + "c" * 20,
                      "squ_" + "d" * 40, "sqa_" + "e" * 40, "gho_" + "f" * 36]:
            with self.subTest(token=token[:8]):
                self.assertTrue(self.blocked(self.set_message(f"토큰 {token} 노출")))

    def test_private_ips(self):
        for ip in ["192.168.77.23", "10.0.0.5", "172.20.1.1", "127.0.0.1"]:
            with self.subTest(ip=ip):
                self.assertTrue(self.blocked(self.set_message(f"서버 {ip} 에 접속")))

    def test_public_ip_and_versions_pass(self):
        self.assertEqual(self.blocked(self.set_message("Spring Boot 4.1.2, 정원 100명, 커버리지 72.4%")), [])

    def test_internal_hosts(self):
        for host in ["http://sonarqube:9000/api", "localhost:8080"]:
            with self.subTest(host=host):
                self.assertTrue(self.blocked(self.set_message(f"주소 {host}")))

    def test_absolute_paths(self):
        for path in ["/var/jenkins_home/workspace/x", "/home/moni/x", "/Users/a/b", "C:\\Users\\moni"]:
            with self.subTest(path=path):
                self.assertTrue(self.blocked(self.set_message(f"경로 {path}")))

    def test_email(self):
        self.assertTrue(self.blocked(self.set_message("연락 someone@example.com")))

    def test_annotation_is_not_email(self):
        self.assertEqual(self.blocked(self.set_message("@Transactional(readOnly = true)을 붙이세요")), [])

    def test_env_secret_value(self):
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "zzzz-very-secret-value-1234"}
        problems = self.blocked(self.set_message("값 zzzz-very-secret-value-1234 이 보여요"), env)
        self.assertTrue(problems)
        self.assertNotIn("zzzz-very-secret-value-1234", "\n".join(problems))

    def test_forbidden_strings(self):
        env = {"CNY_FORBIDDEN_STRINGS": "MINI-PC-01, other"}
        self.assertTrue(self.blocked(self.set_message("호스트 MINI-PC-01"), env))

    def test_secret_detection_issue(self):
        self.assertTrue(self.blocked(lambda d: d["sonarIssues"][0].__setitem__("rule", "secrets:S6290")))

    def test_problem_report_never_contains_matched_text(self):
        token = "ghp_" + "Q" * 36
        problems = self.blocked(self.set_message(f"x {token}"))
        self.assertNotIn(token, "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
