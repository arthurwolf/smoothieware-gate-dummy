#!/usr/bin/env python3
"""Reject unsafe or excessive files in an untrusted gate result artifact."""
import argparse
import json
import os
import re
import stat
import sys



def allowed_result_name(name):
    return bool(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', name) and
                (name.endswith('.result.json') or name in ('pr.json', 'manifest.json', 'trace-samples.json')))


def inspect(root, max_file_bytes=5 * 1024 * 1024, max_total_bytes=25 * 1024 * 1024, max_files=256):
    if os.path.islink(root) or not os.path.isdir(root):
        return ["artifact root is missing or is not a directory"]
    problems, pending, count, total, entries_seen = [], [(root, "", 0)], 0, 0, 0
    while pending:
        directory, prefix, depth = pending.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            problems.append(f"cannot read artifact directory: {prefix or '.'}")
            continue
        for entry in entries:
            entries_seen += 1
            if entries_seen > max_files * 4:
                entries.close()
                return problems + ["artifact exceeds entry count limit"]
            name = f"{prefix}/{entry.name}" if prefix else entry.name
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", entry.name):
                problems.append(f"invalid artifact path: {name}")
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                problems.append(f"cannot inspect artifact entry: {name}")
                continue
            if stat.S_ISDIR(info.st_mode) and depth < 8:
                pending.append((entry.path, name, depth + 1))
            elif stat.S_ISREG(info.st_mode):
                count += 1
                total += info.st_size
                if not allowed_result_name(entry.name):
                    problems.append(f"unexpected artifact file: {name}")
                if info.st_size > max_file_bytes:
                    problems.append(f"artifact file is too large: {name}")
            else:
                problems.append(f"unsupported artifact entry: {name}")
            if count > max_files or total > max_total_bytes:
                entries.close()
                return problems + ["artifact exceeds file count or total size limit"]
        entries.close()
    if not count:
        problems.append("artifact has no result files")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    problems = inspect(args.root)
    print(json.dumps({"type": "error" if problems else "result", "problems": problems}))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
