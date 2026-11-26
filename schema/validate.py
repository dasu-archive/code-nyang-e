#!/usr/bin/env python3
"""Validate a dashboard data tree against the v1 data contract.

Usage: python3 schema/validate.py <root>

<root> is laid out like the dashboard repo:
  <root>/projects.json
  <root>/projects/<id>/latest.json
  <root>/projects/<id>/history.json   (optional)

Checks every file against schema/v1/*.schema.json, then checks that the files
agree with each other (the contract repeats some values across files).
Exit code 0 when everything passes, 1 otherwise. Used for samples/v1 and,
later, by the report step before anything is published.
"""

import json
import sys
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_DIR = Path(__file__).resolve().parent / "v1"
CATEGORIES = ("transaction", "concurrency", "scale", "failure", "other")


def load_json(path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def validator(name):
    schema = load_json(SCHEMA_DIR / f"{name}.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def deepest(error):
    """For oneOf/anyOf failures, report the sub-error that got furthest into the data."""
    while error.context:
        error = max(error.context, key=lambda c: len(list(c.absolute_path)))
    return error


def schema_errors(v, data, label):
    out = []
    for e in sorted(v.iter_errors(data), key=lambda e: list(e.absolute_path)):
        e = deepest(e)
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        message = e.message if len(e.message) <= 300 else e.message[:300] + "..."
        out.append(f"{label}: {where}: {message}")
    return out


def cross_checks(pid, row, latest, history):
    errs = []
    label = f"projects/{pid}"

    if latest["project"]["id"] != pid:
        errs.append(f"{label}/latest.json: project.id {latest['project']['id']!r} != folder name {pid!r}")

    if row is None:
        errs.append(f"projects.json: no row for {pid!r}")
    else:
        expect = {
            "name": latest["project"]["name"],
            "repo": latest["project"]["repo"],
            "lastRunAt": latest["run"]["at"],
            "qualityGate": latest["qualityGate"]["status"],
            "coverage": latest["metrics"]["overall"]["coverage"],
            "claudeFindings": None if latest["claude"] is None else len(latest["claude"]["findings"]),
        }
        for key, want in expect.items():
            if row.get(key) != want:
                errs.append(f"projects.json[{pid}].{key} = {row.get(key)!r}, latest.json says {want!r}")

    claude = latest["claude"]
    if claude is None:
        if latest["opinion"] is not None:
            errs.append(f"{label}/latest.json: claude is null but opinion is not (a failed review nulls both)")
    else:
        counted = Counter(f["category"] for f in claude["findings"])
        want = {c: counted.get(c, 0) for c in CATEGORIES}
        if claude["counts"] != want:
            errs.append(f"{label}/latest.json: claude.counts {claude['counts']} != counted from findings {want}")

    if history is not None:
        runs = history["runs"]
        times = [r["at"] for r in runs]
        if times != sorted(times):
            errs.append(f"{label}/history.json: runs are not oldest-first")
        if runs:
            last = runs[-1]
            checks = {
                "at": latest["run"]["at"],
                "commit": latest["run"]["commit"],
                "qualityGate": latest["qualityGate"]["status"],
                "coverage": latest["metrics"]["overall"]["coverage"],
                "newCoverage": latest["metrics"]["newCode"]["coverage"],
                "issues": latest["metrics"]["overall"]["issues"],
                "claude": None if claude is None else claude["counts"],
            }
            for key, want in checks.items():
                if last.get(key) != want:
                    errs.append(f"{label}/history.json: last run {key} = {last.get(key)!r}, latest.json says {want!r}")
    return errs


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    root = Path(argv[1])
    v_projects, v_latest, v_history = validator("projects"), validator("latest"), validator("history")
    errors, checked = [], []

    projects_path = root / "projects.json"
    projects = load_json(projects_path)
    errors += schema_errors(v_projects, projects, "projects.json")
    checked.append("projects.json")
    rows = {r.get("id"): r for r in projects.get("projects", []) if isinstance(r, dict)}

    folders = sorted(p for p in (root / "projects").iterdir() if p.is_dir()) if (root / "projects").is_dir() else []
    for folder in folders:
        pid = folder.name
        latest_path = folder / "latest.json"
        if not latest_path.is_file():
            errors.append(f"projects/{pid}: latest.json is missing")
            continue
        latest = load_json(latest_path)
        latest_errs = schema_errors(v_latest, latest, f"projects/{pid}/latest.json")
        errors += latest_errs
        checked.append(f"projects/{pid}/latest.json")

        history = None
        history_path = folder / "history.json"
        if history_path.is_file():
            history = load_json(history_path)
            hist_errs = schema_errors(v_history, history, f"projects/{pid}/history.json")
            errors += hist_errs
            checked.append(f"projects/{pid}/history.json")
            if hist_errs:
                history = None
        if not latest_errs:
            errors += cross_checks(pid, rows.get(pid), latest, history)

    for pid in rows:
        if not (root / "projects" / str(pid) / "latest.json").is_file():
            errors.append(f"projects.json: row {pid!r} has no projects/{pid}/latest.json")

    for name in checked:
        print(f"checked {name}")
    if errors:
        print(f"\n{len(errors)} problem(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\nall files pass schema/v1 and agree with each other")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
