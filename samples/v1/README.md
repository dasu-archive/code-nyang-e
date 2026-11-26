# samples/v1

Fake data that follows the v1 data contract (`schema/v1/`), so the dashboard can be built
before real data exists. The layout matches the dashboard repo:

```
projects.json
projects/rehab/latest.json        gate passed, 5 Claude findings (all categories), 7 Sonar issues,
                                  all optional Sonar fields (ratings, linesToCover/uncoveredLines,
                                  hotspots, acceptedIssues, newCodeDefinition)
projects/rehab/history.json       6 runs, oldest first; includes failed gates and a run without review
projects/demo-failed/latest.json  gate failed (2 failed conditions), 1 failing test, nulls (newCode.coverage, a gate
                                  actual, an issue count), 0 findings, empty opinion, optional fields
                                  left out, a file-level and a project-level Sonar issue
```

`demo-failed` has no `history.json` on purpose (a project with no history yet).

Every value is made up, except that `rehab` uses the real `dasu-archive/rehab` commit
`3751f3498849906ad5d0734cf7648987b58469f0` and real file paths, so GitHub links from the
dashboard open real files. Line numbers and messages are invented.

Check the samples:

```
python3 schema/validate.py samples/v1
```
