#!/usr/bin/env python3
"""Validate a workflow-run association before using its artifacts in a PR report."""
import argparse
import json
import re
import sys


def candidate(event):
    try:
        run = event["workflow_run"]
        pulls = run["pull_requests"]
        if run["event"] != "pull_request" or len(pulls) != 1:
            raise ValueError("workflow run has no unique pull request association")
        number = pulls[0]["number"]
        if type(number) is not int or number < 1:
            raise ValueError("invalid pull request number")
        return number
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("invalid workflow run association") from error


def validate(event, pr, repository):
    try:
        number = candidate(event)
        run = event["workflow_run"]
        association = run["pull_requests"][0]
        head = run["head_sha"]
        base = association["base"]["sha"]
        return bool(re.fullmatch(r"[0-9a-f]{40}", head) and re.fullmatch(r"[0-9a-f]{40}", base)
                    and association["head"]["sha"] == head == pr["head"]["sha"]
                    and base == pr["base"]["sha"] and pr["number"] == number
                    and pr["state"] == "open" and pr["base"]["repo"]["full_name"] == repository)
    except (ValueError, KeyError, TypeError, IndexError):
        return False


def validate_manifest_identity(event, manifest, profile):
    try:
        candidate(event)
        run = event["workflow_run"]
        return (manifest["profile"] == profile and manifest["head"] == run["head_sha"] and
                manifest["base"] == run["pull_requests"][0]["base"]["sha"])
    except (ValueError, KeyError, TypeError, IndexError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--pr")
    parser.add_argument("--repository")
    parser.add_argument("--manifest")
    parser.add_argument("--profile")
    args = parser.parse_args()
    try:
        with open(args.event) as handle:
            event = json.load(handle)
        number = candidate(event)
        if args.pr:
            with open(args.pr) as handle:
                pr = json.load(handle)
            if not args.repository or not validate(event, pr, args.repository):
                raise ValueError("pull request no longer matches the workflow run")
        if args.manifest:
            with open(args.manifest) as handle:
                manifest = json.load(handle)
            if not args.profile or not validate_manifest_identity(event, manifest, args.profile):
                raise ValueError("result manifest does not match the workflow run")
        print(number)
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({"type": "error", "message": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
