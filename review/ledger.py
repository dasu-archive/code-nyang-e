#!/usr/bin/env python3
"""Findings ledger for the Claude review (pipeline stage 4).

Claude can word the same problem differently from run to run. The ledger keeps one
entry per problem so that a finding keeps its firstSeen and does not flap open/closed:

  fingerprint  file + category + normalized message (line numbers left out, digits masked)
  firstSeen    set when a fingerprint first appears, kept while it stays open
  closing      an open entry closes only when its file was reviewed again in this run
               and the fingerprint did not come back, or when the file no longer exists
  scope        files outside this run's review scope are not re-reviewed, so their
               entries stay open as they are

The ledger lives on the mini PC only (Jenkins volume) and is never published.

Commands:
  ledger.py last-commit <ledger.json>
      Print the commit of the last review, or nothing if there is none.
  ledger.py merge <ledger.json> --repo-dir DIR --scope FILE --scope-kind full|changed|none
                  --commit SHA --run-at TIME --out FILE [--structured FILE]
      Merge one run's structured_output into the ledger and write the open findings
      (the claude.findings[] of latest.json, before allowlist) to --out.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

LEDGER_VERSION = 1
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
FINDINGS_SCHEMA = Path(__file__).resolve().parent / "findings.schema.json"


def normalize(message):
    text = message.lower()
    text = re.sub(r"\d+", "#", text)          # line numbers, counts
    text = re.sub(r"[^\w#]+", " ", text)      # punctuation and quotes (\w keeps Korean)
    return " ".join(text.split())


def fingerprint(finding):
    key = "\n".join([finding["file"], finding["category"], normalize(finding["message"])])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def load_ledger(path):
    if not path.exists():
        return {"version": LEDGER_VERSION, "lastReviewedCommit": None, "entries": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != LEDGER_VERSION:
        raise SystemExit(f"{path}: unsupported ledger version {data.get('version')!r}")
    return data


def write_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def read_scope(path):
    if not path or not Path(path).exists():
        return set()
    return {line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()}


def read_structured(path):
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    schema = json.loads(FINDINGS_SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        e = errors[0]
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        raise SystemExit(f"structured_output does not match findings.schema.json: {where}: {e.message}")
    return data["findings"]


def merge(ledger, findings, scope, scope_kind, repo_dir, commit, run_at):
    entries = ledger["entries"]
    stats = {"new": 0, "kept": 0, "reopened": 0, "closed": 0, "dropped": 0}

    seen = {}
    for f in findings:
        if not (repo_dir / f["file"]).is_file():
            stats["dropped"] += 1          # path that does not exist in the checkout
            continue
        seen.setdefault(fingerprint(f), f)  # duplicates in one run collapse

    for fp, f in seen.items():
        current = {k: f[k] for k in ("category", "severity", "file", "line", "message")}
        entry = entries.get(fp)
        if entry is None:
            entries[fp] = {**current, "status": "open", "firstSeen": run_at, "lastSeen": run_at,
                           "firstCommit": commit, "closedAt": None}
            stats["new"] += 1
        else:
            if entry["status"] == "closed":
                stats["reopened"] += 1
            else:
                stats["kept"] += 1
            entry.update(current, status="open", lastSeen=run_at, closedAt=None)

    for fp, entry in entries.items():
        if entry["status"] != "open" or fp in seen:
            continue
        gone = not (repo_dir / entry["file"]).is_file()
        reviewed_again = scope_kind == "full" or entry["file"] in scope
        if gone or (scope_kind != "none" and reviewed_again):
            entry["status"] = "closed"
            entry["closedAt"] = run_at
            stats["closed"] += 1

    ledger["lastReviewedCommit"] = commit
    return stats


def open_findings(ledger):
    rows = [e for e in ledger["entries"].values() if e["status"] == "open"]
    rows.sort(key=lambda e: (SEVERITY_ORDER[e["severity"]], e["file"], e["line"] or 0, e["category"]))
    keys = ("category", "severity", "file", "line", "message", "firstSeen")
    return [{k: e[k] for k in keys} for e in rows]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude review findings ledger")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_last = sub.add_parser("last-commit")
    p_last.add_argument("ledger", type=Path)

    p_merge = sub.add_parser("merge")
    p_merge.add_argument("ledger", type=Path)
    p_merge.add_argument("--repo-dir", type=Path, required=True)
    p_merge.add_argument("--scope", required=True)
    p_merge.add_argument("--scope-kind", choices=("full", "changed", "none"), required=True)
    p_merge.add_argument("--commit", required=True)
    p_merge.add_argument("--run-at", required=True)
    p_merge.add_argument("--out", type=Path, required=True)
    p_merge.add_argument("--structured")

    args = parser.parse_args(argv)

    if args.cmd == "last-commit":
        commit = load_ledger(args.ledger).get("lastReviewedCommit")
        if commit:
            print(commit)
        return 0

    ledger = load_ledger(args.ledger)
    findings = read_structured(args.structured)
    scope = read_scope(args.scope)
    stats = merge(ledger, findings, scope, args.scope_kind, args.repo_dir, args.commit, args.run_at)
    result = open_findings(ledger)
    write_json_atomic(args.ledger, ledger)
    write_json_atomic(args.out, {
        "reviewedCommit": args.commit,
        "scope": args.scope_kind,
        "reviewedFiles": len(scope),
        "findings": result,
    })
    print("ledger: " + ", ".join(f"{k} {v}" for k, v in stats.items()) + f"; open {len(result)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
