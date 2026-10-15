#!/usr/bin/env python3
"""Bounded path lookup from a committed Git-metadata catalog; stdlib + Git only.

No source file contents, credentials, network access, or application imports.
Queries work in a source ZIP; check/build require a Git checkout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CATALOG = "docs/_index/catalog.json"
SCOPE = "docs/index-scope.json"
HISTORY = re.compile(
    r"(^|/)(history/|superpowers/|AI_SERVER_PROJECT_(?:CONTEXT|HANDOFF)_|"
    r"RUN_V\d|UX_UI_CONTEXT_V|RELEASE_v|VERIFICATION_v|V\d|"
    r"CLAUDE_CODE_REVIEW_HANDOFF_)|_BATCH_\d", re.IGNORECASE)


def emit(value):
    print(json.dumps(value, ensure_ascii=True, indent=2))


def git(*args):
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)
    if result.returncode:
        raise ValueError("Git command failed; use a checkout with Git installed: " + " ".join(args))
    return result.stdout


def fingerprint(entries):
    raw = json.dumps(entries, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def make_catalog(entries, source_commit):
    """Rows are [path, blob-or-gitlink SHA, mode]; generated catalog is excluded."""
    rows = sorted([list(row) for row in entries if row[0] != CATALOG])
    return {"schema": 1, "source_commit": source_commit, "fingerprint": fingerprint(rows),
            "entry_count": len(rows), "entries": rows}


def committed_entries():
    rows = []
    for item in git("ls-tree", "-r", "-z", "--full-tree", "HEAD").split(b"\0"):
        if item:
            meta, path = item.split(b"\t", 1)
            mode, _, sha = meta.decode("ascii").split()
            rows.append([path.decode("utf-8", "surrogateescape"), sha, mode])
    return make_catalog(rows, git("rev-parse", "HEAD").decode().strip())


def dirty_paths():
    # NUL records preserve spaces/newlines; a rename's second path is separate.
    records = git("status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0")
    paths, pos = set(), 0
    while pos < len(records):
        record = records[pos]
        pos += 1
        if not record:
            continue
        paths.add(record[3:].decode("utf-8", "surrogateescape"))
        if b"R" in record[:2] or b"C" in record[:2]:
            if pos < len(records) and records[pos]:
                paths.add(records[pos].decode("utf-8", "surrogateescape"))
            pos += 1
    return sorted(paths - {CATALOG})


def load_catalog():
    data = json.loads((ROOT / CATALOG).read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("entries"), list):
        raise ValueError("Unsupported catalog schema; rebuild from committed Git HEAD.")
    rows = data["entries"]
    valid = all(isinstance(row, list) and len(row) == 3 and
                all(isinstance(value, str) for value in row) and
                not row[0].startswith("/") and ".." not in row[0].split("/") and
                re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", row[1]) and
                row[2] in {"100644", "100755", "120000", "160000"} for row in rows)
    if (not valid or rows != sorted(rows) or len({row[0] for row in rows}) != len(rows)
            or data.get("entry_count") != len(rows) or data.get("fingerprint") != fingerprint(rows)):
        raise ValueError("Catalog entries/fingerprint invalid; rebuild from committed Git HEAD.")
    return data


def bounded(values, limit):
    return {"total": len(values), "truncated": len(values) > limit, "results": values[:limit]}


def load_focus():
    path = ROOT / SCOPE
    return json.loads(path.read_text(encoding="utf-8")).get("focus", []) if path.exists() else []


def under(path, prefix):
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("find", "ls", "check"):
        part = sub.add_parser(command)
        part.add_argument("--limit", type=int, default=20)
        if command == "find":
            part.add_argument("query", nargs="?", default="")
            part.add_argument("--prefix", default="")
            part.add_argument("--docs", action="store_true")
            part.add_argument("--history", action="store_true", help="include historical/design docs")
            part.add_argument("--all", action="store_true", help="include upstream paths and history")
        elif command == "ls":
            part.add_argument("prefix", nargs="?", default="")
            part.add_argument("--all", action="store_true", help="include upstream paths and history")
    sub.add_parser("build", help="catalog committed HEAD; refuses uncommitted source changes")
    args = parser.parse_args()
    if hasattr(args, "limit") and not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")

    if args.command == "build":
        dirty = dirty_paths()
        if dirty:
            raise ValueError("Build requires committed source changes. Commit intended files first; "
                             "do not add credentials or local data. Dirty paths: " + repr(dirty[:20]))
        data = committed_entries()
        path = ROOT / CATALOG
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8")
        temporary.replace(path)
        emit({key: value for key, value in data.items() if key != "entries"})
        return 0

    data = load_catalog()
    rows = data["entries"]
    if args.command == "check":
        current = committed_entries()
        before = {row[0]: row[1:] for row in rows}
        after = {row[0]: row[1:] for row in current["entries"]}
        dirty = dirty_paths()
        fresh = before == after
        emit({"fresh": fresh, "source_commit": data["source_commit"],
              "head": current["source_commit"], "dirty": bounded(dirty, args.limit),
              "added": bounded(sorted(after.keys() - before.keys()), args.limit),
              "removed": bounded(sorted(before.keys() - after.keys()), args.limit),
              "modified": bounded(sorted(path for path in before.keys() & after.keys()
                                         if before[path] != after[path]), args.limit)})
        return 0 if fresh and not dirty else 1

    if args.command == "find":
        focus = load_focus()
        terms = args.query.casefold().split()
        result = []
        prefix = args.prefix.strip("/")
        for path, sha, mode in rows:
            if prefix and not under(path, prefix):
                continue
            if not args.all and not prefix and focus and not any(path.startswith(p) for p in focus):
                continue
            if not args.all and not args.history and HISTORY.search(path):
                continue
            if args.docs and Path(path).suffix.casefold() not in {".md", ".rst"}:
                continue
            if all(term in path.casefold() for term in terms):
                result.append({"path": path, "sha": sha, "mode": mode})
        emit({"source_commit": data["source_commit"], **bounded(result, args.limit)})
        return 0

    prefix = args.prefix.strip("/")
    base = prefix + "/" if prefix else ""
    children = {}
    focus = load_focus()
    for path, sha, mode in rows:
        if not path.startswith(base):
            continue
        if not args.all and HISTORY.search(path):
            continue
        if not args.all and not prefix and focus and not any(path.startswith(p) for p in focus):
            continue
        rest = path[len(base):]
        name, separator, _ = rest.partition("/")
        child = base + name + ("/" if separator else "")
        if separator:
            children.setdefault(child, {"path": child, "kind": "directory", "files": 0})["files"] += 1
        else:
            children[child] = {"path": child, "kind": "file", "sha": sha, "mode": mode}
    emit({"source_commit": data["source_commit"], "prefix": prefix,
          **bounded(sorted(children.values(), key=lambda row: (row["kind"] != "directory", row["path"])),
                    args.limit)})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError) as error:
        emit({"error": str(error)})
        sys.exit(2)
