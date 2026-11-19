"""Result record shared by every smoothie-gate stage, and its validator.

A stage writes <stage>.result.json; the reporter reads only these files.
Schema documentation: documentation/result-schema.md. Standard library only.
"""
import json
import sys

STATUSES = ("pass", "warn", "fail", "skipped", "error")
REQUIRED = {"stage": str, "status": str, "title": str, "summary": str, "details": str, "metrics": dict}
STATUS_RANK = {"pass": 0, "skipped": 1, "warn": 2, "error": 3, "fail": 4}


def validate_result(data):
    """Return a list of problems; an empty list means the record is valid."""
    if not isinstance(data, dict):
        return ["result must be a JSON object"]
    problems = []
    for key, kind in REQUIRED.items():
        if key not in data:
            problems.append(f"missing field '{key}'")
        elif not isinstance(data[key], kind):
            problems.append(f"field '{key}' must be {kind.__name__}")
    for key in data:
        if key not in REQUIRED:
            problems.append(f"unknown field '{key}'")
    if isinstance(data.get("status"), str) and data["status"] not in STATUSES:
        problems.append(f"status '{data['status']}' not one of {', '.join(STATUSES)}")
    for key in ("stage", "title", "summary"):
        if isinstance(data.get(key), str) and not data[key].strip():
            problems.append(f"field '{key}' must not be empty")
    metrics = data.get("metrics")
    if isinstance(metrics, dict):
        for name, value in metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                problems.append(f"metric '{name}' must be a number or string")
    return problems


def make_result(stage, status, title, summary, details="", metrics=None):
    result = {"stage": stage, "status": status, "title": title, "summary": summary,
              "details": details, "metrics": metrics or {}}
    problems = validate_result(result)
    if problems:
        raise ValueError("; ".join(problems))
    return result


def worst_status(statuses):
    return max(statuses, key=lambda s: STATUS_RANK[s], default="pass")


def main(argv):
    if len(argv) < 2:
        print("usage: gate_result.py RESULT.json [...]", file=sys.stderr)
        return 2
    code = 0
    for path in argv[1:]:
        try:
            with open(path) as handle:
                problems = validate_result(json.load(handle))
        except (OSError, json.JSONDecodeError) as error:
            problems = [f"cannot read: {error}"]
        for problem in problems:
            print(json.dumps({"type": "error", "path": path, "message": problem}))
        print(json.dumps({"type": "result", "path": path, "valid": not problems}))
        code = code or (1 if problems else 0)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
