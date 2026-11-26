#!/usr/bin/env python3
"""Build the public JSON for one project run (pipeline stage 6).

Inputs:  SonarQube Web API, the target checkout (test XML), review/run.sh output,
         the local state (what was published before: projects.json, history.json).
Output:  <out>/projects.json, <out>/projects/<id>/latest.json, <out>/projects/<id>/history.json
         Every number comes from the Sonar API or this script. Claude writes only the
         findings (stage 4) and the opinion text here, and the opinion may only quote
         numbers that are already in latest.json.
Checks:  report/allowlist.py and schema/validate.py must pass, otherwise nothing is
         written to the state and the build fails. The state is updated only after both pass.

Env:     SONAR_HOST_URL (default http://sonarqube:9000), SONAR_TOKEN
         CLAUDE_CODE_OAUTH_TOKEN (for the opinion; without it opinion is null)
         CNY_CLAUDE_BIN (default "claude"; tests point it at a stub)

--sonar-fixtures DIR reads recorded API responses from DIR instead of calling Sonar
(tests; file names in SonarFixtures.FILES).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "schema"))
import allowlist  # noqa: E402
import validate  # noqa: E402

CATEGORIES = ("transaction", "concurrency", "scale", "failure", "other")
RATING = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E"}
HISTORY_LIMIT = 100
ISSUE_SEARCH_CAP = 10000

# Our field -> Sonar metric keys, first one that exists on this server wins
# (MQR mode keys first, then standard mode).
QUALITIES = ("reliability", "security", "maintainability")
ISSUE_KEYS = {
    "reliability": ["software_quality_reliability_issues", "reliability_issues", "bugs"],
    "security": ["software_quality_security_issues", "security_issues", "vulnerabilities"],
    "maintainability": ["software_quality_maintainability_issues", "maintainability_issues", "code_smells"],
}
RATING_KEYS = {
    "reliability": ["software_quality_reliability_rating", "reliability_rating"],
    "security": ["software_quality_security_rating", "security_rating"],
    "maintainability": ["software_quality_maintainability_rating", "sqale_rating"],
}
NEW_RATING_KEYS = {
    "reliability": ["new_software_quality_reliability_rating", "new_reliability_rating"],
    "security": ["new_software_quality_security_rating", "new_security_rating"],
    "maintainability": ["new_software_quality_maintainability_rating", "new_maintainability_rating"],
}
SIMPLE_KEYS = {
    "coverage": "coverage", "duplication": "duplicated_lines_density", "lines": "ncloc",
    "linesToCover": "lines_to_cover", "uncoveredLines": "uncovered_lines",
    "hotspotsTotal": "security_hotspots", "hotspotsReviewed": "security_hotspots_reviewed",
    "acceptedIssues": "accepted_issues",
}

# Gate condition metric -> our name (schema/v1 latest gateCondition.metric).
GATE_NAMES = {
    "new_violations": "newIssues", "violations": "issues",
    "new_security_hotspots_reviewed": "newHotspotsReviewed", "security_hotspots_reviewed": "hotspotsReviewed",
    "new_coverage": "newCoverage", "coverage": "coverage",
    "new_duplicated_lines_density": "newDuplication", "duplicated_lines_density": "duplication",
}
for q in QUALITIES:
    for k in RATING_KEYS[q]:
        GATE_NAMES[k] = f"{q}Rating"
    for k in NEW_RATING_KEYS[q]:
        GATE_NAMES[k] = f"new{q.capitalize()}Rating"

NEW_CODE_TYPES = {
    "PREVIOUS_VERSION": "previous_version", "NUMBER_OF_DAYS": "days", "DAYS": "days",
    "REFERENCE_BRANCH": "reference_branch", "SPECIFIC_ANALYSIS": "specific_analysis",
}


class ReportError(Exception):
    pass


# ---------------------------------------------------------------- Sonar access

class SonarAPI:
    def __init__(self, base, token):
        self.base = base.rstrip("/")
        self.token = token

    def get(self, path, **params):
        url = f"{self.base}/{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Do not echo the URL with parameters or headers; the path is enough.
            raise ReportError(f"Sonar API {path} returned HTTP {e.code}") from None


class SonarFixtures:
    FILES = {
        "api/qualitygates/project_status": "qualitygates_project_status.json",
        "api/metrics/search": "metrics_search.json",
        "api/measures/component": "measures_component.json",
        "api/issues/search": "issues_search.json",
        "api/new_code_periods/show": "new_code_periods_show.json",
        "api/project_analyses/search": "project_analyses_search.json",
    }

    def __init__(self, folder):
        self.folder = Path(folder)

    def get(self, path, **params):
        f = self.folder / self.FILES[path]
        if not f.exists():
            raise ReportError(f"fixture missing for {path}")
        return json.loads(f.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- value helpers

def camel(key):
    head, *rest = key.split("_")
    return head + "".join(p.capitalize() for p in rest)


def convert(value, metric_type):
    """Sonar measure string -> contract value. None stays None."""
    if value is None or value == "":
        return None
    if metric_type == "RATING":
        return RATING.get(int(round(float(value))))
    if metric_type == "DATA" and str(value).startswith("{"):
        total = json.loads(value).get("total")
        return None if total is None else int(total)
    if metric_type in ("INT", "WORK_DUR", "MILLISEC"):
        return int(round(float(value)))
    if metric_type in ("PERCENT", "FLOAT"):
        return round(float(value), 1)
    try:
        return int(value)
    except ValueError:
        return round(float(value), 1)


def measure_value(m):
    if "value" in m:
        return m["value"]
    if "period" in m:
        return m["period"].get("value")
    if m.get("periods"):
        return m["periods"][0].get("value")
    return None


# ---------------------------------------------------------------- collectors

def sonar_metrics(api, project_key):
    types = {}
    page = 1
    while True:
        data = api.get("api/metrics/search", ps=500, p=page)
        for m in data.get("metrics", []):
            types[m["key"]] = m.get("type")
        if page * 500 >= data.get("total", 0):
            break
        page += 1

    def pick(candidates):
        return next((k for k in candidates if k in types), None)

    wanted = {}
    for scope, prefix in (("overall", ""), ("newCode", "new_")):
        for q in QUALITIES:
            wanted[(scope, "issues", q)] = pick([prefix + k if not k.startswith(prefix) else k for k in ISSUE_KEYS[q]])
            wanted[(scope, "ratings", q)] = pick((RATING_KEYS if scope == "overall" else NEW_RATING_KEYS)[q])
        for field, key in SIMPLE_KEYS.items():
            if scope == "newCode" and field == "lines":
                continue
            wanted[(scope, field, None)] = pick([prefix + key])

    keys = sorted({k for k in wanted.values() if k})
    data = api.get("api/measures/component", component=project_key, metricKeys=",".join(keys))
    values = {m["metric"]: measure_value(m) for m in data.get("component", {}).get("measures", [])}

    def val(slot):
        key = wanted.get(slot)
        return convert(values.get(key), types.get(key)) if key else None

    out = {}
    for scope in ("overall", "newCode"):
        block = {
            "coverage": val((scope, "coverage", None)),
            "duplication": val((scope, "duplication", None)),
        }
        if scope == "overall":
            block["lines"] = val((scope, "lines", None))
        block["issues"] = {q: val((scope, "issues", q)) for q in QUALITIES}
        ratings = {q: val((scope, "ratings", q)) for q in QUALITIES}
        if any(v is not None for v in ratings.values()):
            block["ratings"] = ratings
        for field in ("linesToCover", "uncoveredLines", "acceptedIssues"):
            if wanted.get((scope, field, None)):
                block[field] = val((scope, field, None))
        if wanted.get((scope, "hotspotsTotal", None)):
            block["hotspots"] = {
                "total": val((scope, "hotspotsTotal", None)),
                "reviewedPercent": val((scope, "hotspotsReviewed", None)),
            }
        out[scope] = block
    return out, types


def sonar_gate(api, project_key, types):
    data = api.get("api/qualitygates/project_status", projectKey=project_key).get("projectStatus", {})
    status = {"OK": "passed", "ERROR": "failed"}.get(data.get("status"))
    conditions = []
    if status is not None:
        for c in data.get("conditions", []):
            key = c.get("metricKey", "")
            mtype = types.get(key)
            conditions.append({
                "metric": GATE_NAMES.get(key, camel(key)),
                "comparator": str(c.get("comparator", "")).lower(),
                "threshold": convert(c.get("errorThreshold"), mtype),
                "actual": convert(c.get("actualValue"), mtype),
                "status": "passed" if c.get("status") == "OK" else "failed",
            })
    return {"status": status, "conditions": conditions}


def sonar_issues(api, project_key):
    issues, page = [], 1
    while True:
        data = api.get("api/issues/search", componentKeys=project_key, resolved="false", ps=500, p=page)
        total = data.get("total", data.get("paging", {}).get("total", 0))
        if total > ISSUE_SEARCH_CAP:
            raise ReportError(f"{total} open issues is more than the Sonar search cap {ISSUE_SEARCH_CAP}")
        issues += data.get("issues", [])
        if page * 500 >= total:
            break
        page += 1

    out = []
    prefix = project_key + ":"
    for i in issues:
        rule = i.get("rule", "")
        if rule.startswith("secrets:"):
            continue  # never published (public data rules)
        impacts = [{"quality": x["softwareQuality"].lower(), "severity": x["severity"].lower()}
                   for x in i.get("impacts", []) if x.get("softwareQuality") and x.get("severity")]
        if not impacts:
            continue  # security hotspots and legacy issues without impacts are not in the contract
        component = i.get("component", "")
        out.append({
            "rule": rule,
            "impacts": impacts,
            "file": component[len(prefix):] if component.startswith(prefix) else None,
            "line": i.get("line"),
            "message": i.get("message", ""),
        })
    out.sort(key=lambda x: (x["file"] or "", x["line"] or 0, x["rule"]))
    return out


def sonar_new_code(api, project_key):
    try:
        data = api.get("api/new_code_periods/show", project=project_key)
    except ReportError:
        return None
    kind = NEW_CODE_TYPES.get(data.get("type"))
    if kind is None:
        return None
    value = data.get("value")
    return {"type": kind, "value": None if value in (None, "") else str(value)}


def check_analysis(api, project_key, commit):
    data = api.get("api/project_analyses/search", project=project_key, ps=1)
    analyses = data.get("analyses", [])
    if not analyses or analyses[0].get("revision") != commit:
        raise ReportError(f"Sonar has no analysis for commit {commit[:12]}; the sonar stage did not finish")


def test_counts(target):
    total = failed = skipped = 0
    files = list((target / "build" / "test-results" / "test").glob("*.xml"))
    if not files:
        raise ReportError("no test result XML under build/test-results/test")
    for f in files:
        r = ET.parse(f).getroot()
        suites = [r] if r.tag == "testsuite" else r.findall("testsuite")
        for s in suites:
            total += int(s.get("tests", 0))
            failed += int(s.get("failures", 0)) + int(s.get("errors", 0))
            skipped += int(s.get("skipped", 0))
    return {"total": total, "failed": failed, "skipped": skipped}


def claude_block(findings_path):
    if not findings_path or not Path(findings_path).is_file():
        return None
    data = json.loads(Path(findings_path).read_text(encoding="utf-8"))
    keys = ("category", "severity", "file", "line", "message", "firstSeen")
    findings = [{k: f[k] for k in keys} for f in data.get("findings", [])]
    counted = Counter(f["category"] for f in findings)
    return {"counts": {c: counted.get(c, 0) for c in CATEGORIES}, "findings": findings}


# ---------------------------------------------------------------- opinion

OPINION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["paragraphs"],
    "properties": {"paragraphs": {"type": "array", "maxItems": 4, "items": {"type": "string", "minLength": 1}}},
}
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


def allowed_numbers(latest):
    nums = set()

    def add(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            nums.add(f"{v:g}")
            if isinstance(v, float):
                nums.add(f"{v:.1f}")
        elif isinstance(v, str):
            nums.update(NUMBER.findall(v))

    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                # Messages count: the opinion may quote them (e.g. "HTTP 500").
                # Hashes and timestamps do not, or almost any number would pass.
                if k not in ("commit", "at", "firstSeen"):
                    walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
        else:
            add(n)

    walk(latest)
    # Counts a reader would naturally state, derived only by counting entries.
    claude = latest.get("claude") or {}
    for c in Counter(f["severity"] for f in claude.get("findings", [])).values():
        add(c)
    add(len(claude.get("findings", [])))
    add(len(latest.get("sonarIssues", [])))
    add(sum(1 for c in latest["qualityGate"]["conditions"] if c["status"] == "failed"))
    return nums


def opinion(latest):
    if latest.get("claude") is None or not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return None
    payload = {k: v for k, v in latest.items() if k != "opinion"}
    prompt = (HERE / "opinion_prompt.md").read_text(encoding="utf-8") + "\n```json\n" + \
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n```\n"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    cmd = [os.environ.get("CNY_CLAUDE_BIN", "claude"), "-p", prompt,
           "--output-format", "json", "--json-schema", json.dumps(OPINION_SCHEMA),
           "--tools", "Read", "--permission-mode", "dontAsk",
           "--setting-sources", "user", "--settings", '{"disableAllHooks": true}',
           "--no-session-persistence", "--max-turns", "3"]
    with tempfile.TemporaryDirectory() as empty:  # nothing for it to read
        try:
            r = subprocess.run(cmd, cwd=empty, env=env, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"opinion: claude -p did not run ({e.__class__.__name__}); opinion = null")
            return None
    if r.returncode != 0:
        print(f"opinion: claude -p exit {r.returncode}; opinion = null")
        return None
    try:
        paragraphs = json.loads(r.stdout)["structured_output"]["paragraphs"]
    except (ValueError, KeyError, TypeError):
        print("opinion: no structured_output; opinion = null")
        return None
    allowed = allowed_numbers(latest)
    unknown = sorted({n for p in paragraphs for n in NUMBER.findall(p)} - allowed)
    if unknown:
        print(f"opinion: numbers not in the data {unknown}; opinion = null")
        return None
    return {"paragraphs": [p.strip() for p in paragraphs if p.strip()]}


# ---------------------------------------------------------------- assembly

def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def history_entry(latest):
    return {
        "at": latest["run"]["at"],
        "commit": latest["run"]["commit"],
        "qualityGate": latest["qualityGate"]["status"],
        "coverage": latest["metrics"]["overall"]["coverage"],
        "newCoverage": latest["metrics"]["newCode"]["coverage"],
        "issues": dict(latest["metrics"]["overall"]["issues"]),
        "claude": None if latest["claude"] is None else dict(latest["claude"]["counts"]),
    }


def project_row(latest):
    return {
        "id": latest["project"]["id"],
        "name": latest["project"]["name"],
        "repo": latest["project"]["repo"],
        "lastRunAt": latest["run"]["at"],
        "qualityGate": latest["qualityGate"]["status"],
        "coverage": latest["metrics"]["overall"]["coverage"],
        "claudeFindings": None if latest["claude"] is None else len(latest["claude"]["findings"]),
    }


def build(args, api):
    projects = yaml.safe_load(Path(args.projects_yml).read_text(encoding="utf-8"))["projects"]
    project = next((p for p in projects if p["id"] == args.project_id), None)
    if project is None:
        raise ReportError(f"{args.project_id!r} is not in {args.projects_yml}")

    target = Path(args.target_dir)
    commit = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    key = project["id"]

    check_analysis(api, key, commit)
    metrics, types = sonar_metrics(api, key)
    latest = {
        "schemaVersion": 1,
        "project": {"id": key, "name": project["name"], "repo": project["repo"]},
        "run": {"at": args.run_at, "commit": commit, "branch": args.branch, "build": int(args.build)},
        "qualityGate": sonar_gate(api, key, types),
        "metrics": metrics,
    }
    ncd = sonar_new_code(api, key)
    if ncd is not None:
        latest["newCodeDefinition"] = ncd
    latest["tests"] = test_counts(target)
    latest["claude"] = claude_block(args.findings)
    latest["sonarIssues"] = sonar_issues(api, key)
    latest["opinion"] = opinion(latest)

    state = Path(args.state_dir)
    history = load_json(state / "projects" / key / "history.json", {"schemaVersion": 1, "runs": []})
    runs = list(history.get("runs", []))
    if runs and runs[-1].get("commit") == commit:
        runs = runs[:-1]  # same commit inspected again (e.g. code-nyang-e changed): replace it
    runs.append(history_entry(latest))
    runs = runs[-HISTORY_LIMIT:]

    index = load_json(state / "projects.json", {"schemaVersion": 1, "projects": []})
    rows = [r for r in index.get("projects", []) if r.get("id") != key] + [project_row(latest)]
    rows.sort(key=lambda r: r["id"])

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "projects" / key).mkdir(parents=True)

    def write(rel, data):
        (out / rel).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write("projects.json", {"schemaVersion": 1, "updatedAt": args.run_at, "projects": rows})
    write(f"projects/{key}/latest.json", latest)
    write(f"projects/{key}/history.json", {"schemaVersion": 1, "runs": runs})
    # Other projects' files are needed for validate.py's cross-checks of projects.json.
    for row in rows:
        if row["id"] != key:
            src = state / "projects" / row["id"]
            if src.is_dir():
                shutil.copytree(src, out / "projects" / row["id"])
    return out, state, key


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--project-id", required=True)
    p.add_argument("--projects-yml", required=True)
    p.add_argument("--target-dir", required=True)
    p.add_argument("--findings", help="review/run.sh findings.json; missing file = review failed")
    p.add_argument("--run-at", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--build", required=True)
    p.add_argument("--state-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--sonar-fixtures")
    args = p.parse_args(argv)

    if args.sonar_fixtures:
        api = SonarFixtures(args.sonar_fixtures)
    else:
        token = os.environ.get("SONAR_TOKEN")
        if not token:
            print("SONAR_TOKEN is not set", file=sys.stderr)
            return 1
        api = SonarAPI(os.environ.get("SONAR_HOST_URL", "http://sonarqube:9000"), token)

    try:
        out, state, key = build(args, api)
    except ReportError as e:
        print(f"report: {e}", file=sys.stderr)
        return 1

    if allowlist.main(["allowlist", str(out)]) != 0 or validate.main(["validate", str(out)]) != 0:
        print("report: checks failed; state not updated, nothing to publish", file=sys.stderr)
        return 1

    (state / "projects" / key).mkdir(parents=True, exist_ok=True)
    shutil.copy2(out / "projects.json", state / "projects.json")
    for name in ("latest.json", "history.json"):
        shutil.copy2(out / "projects" / key / name, state / "projects" / key / name)
    print(f"report: wrote {out} and updated state {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
