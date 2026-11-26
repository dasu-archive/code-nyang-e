#!/usr/bin/env python3
"""Last check before anything is published (CNY-ISS-8).

The dashboard repo and site are public, and a push stays in git history. This script
scans every string in the output tree and blocks publishing when it finds:

  - token shapes          ghp_ gho_ ghu_ ghs_ ghr_ github_pat_ sk-ant- squ_ sqp_ sqa_, private keys
  - private addresses     10.x.x.x, 172.16-31.x.x, 192.168.x.x, 127.x.x.x, 169.254.x.x,
                          internal service names (sonarqube:9000, localhost:<port>)
  - absolute paths        /home/ /Users/ /root/ /var/jenkins_home, drive letters like C:\\
  - email addresses
  - secret values         the actual values of this process's *TOKEN*/*SECRET*/*PASSWORD*/*KEY*
                          environment variables, plus CNY_FORBIDDEN_STRINGS (comma separated,
                          e.g. the mini PC host name)
  - Sonar secret-detection issues (rule starting with "secrets:") left in sonarIssues

Structure (only contract fields) is enforced by schema/v1, whose objects all have
additionalProperties: false; run schema/validate.py as well.

Findings are reported by file, JSON path and pattern name. Matched text is never
printed, so a leaked secret does not end up in the Jenkins log either.

Usage: python3 report/allowlist.py <site-dir>    exit 0 = clean, 1 = blocked
"""

import json
import os
import re
import sys
from pathlib import Path

PATTERNS = [
    ("github token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("anthropic token", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("sonar token", re.compile(r"\bsq[upa]_[A-Za-z0-9]{16,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("private ip", re.compile(
        r"(?<![\d.])(10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|169\.254\.\d{1,3}\.\d{1,3})(?![\d.]*\d)")),
    ("internal host", re.compile(r"\b(sonarqube|jenkins|db):\d{2,5}\b|\blocalhost:\d{2,5}\b", re.IGNORECASE)),
    ("absolute path", re.compile(r"(/home/|/Users/|/root/|/var/jenkins_home|(?<![A-Za-z])[A-Za-z]:\\)")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
]

SECRET_ENV = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|KEY)", re.IGNORECASE)


def secret_values(environ):
    values = []
    for name, value in environ.items():
        if SECRET_ENV.search(name) and value and len(value) >= 8:
            values.append((f"value of ${name}", value))
    extra = environ.get("CNY_FORBIDDEN_STRINGS", "")
    for i, s in enumerate(x.strip() for x in extra.split(",")):
        if len(s) >= 3:
            values.append((f"CNY_FORBIDDEN_STRINGS[{i}]", s))
    return values


def walk(node, path=""):
    """Yield (json_path, string) for every key and string value."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}/{k}", str(k)
            yield from walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}/{i}")
    elif isinstance(node, str):
        yield path or "/", node


def scan(data, label, secrets):
    problems = []
    for path, text in walk(data):
        for name, rx in PATTERNS:
            if rx.search(text):
                problems.append(f"{label}:{path}: {name}")
        for name, value in secrets:
            if value in text:
                problems.append(f"{label}:{path}: {name}")
    for i, issue in enumerate(data.get("sonarIssues") or []) if isinstance(data, dict) else []:
        if isinstance(issue, dict) and str(issue.get("rule", "")).startswith("secrets:"):
            problems.append(f"{label}:/sonarIssues/{i}: secret-detection issue must not be published")
    return problems


def scan_tree(root, environ=None):
    secrets = secret_values(os.environ if environ is None else environ)
    problems = []
    files = sorted(p for p in Path(root).rglob("*.json"))
    for p in files:
        label = p.relative_to(root).as_posix()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            problems.append(f"{label}: cannot read as JSON ({e.__class__.__name__})")
            continue
        problems += scan(data, label, secrets)
    return files, problems


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    files, problems = scan_tree(Path(argv[1]))
    if not files:
        print("allowlist: no JSON files found", file=sys.stderr)
        return 1
    if problems:
        print(f"allowlist: BLOCKED, {len(problems)} problem(s) in {len(files)} file(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"allowlist: {len(files)} file(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
