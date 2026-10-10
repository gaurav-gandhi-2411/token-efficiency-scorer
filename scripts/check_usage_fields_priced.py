"""scripts/check_usage_fields_priced.py -- fail when a usage key path (or value) that appears in
Claude Code transcripts has no entry in the committed pricing-rule registry.

Why this exists: 1-hour cache writes were recorded in every transcript
(``usage.cache_creation.ephemeral_1h_input_tokens``) for weeks while tracegauge priced all writes
at the 5-minute rate, understating set-B spend by ~3%. Nothing noticed, because nothing compared
"what the transcripts carry" with "what the pricing code knows". This gate does: every key path
under ``message.usage`` must be listed in ``config/usage_field_rules.json`` with a rule (priced /
free / ignored-with-reason / unmodelled), so a NEW billable field fails loudly the first time it
shows up, and a value that changes the bill (``speed: fast``, ``inference_geo: us``, a non-
standard ``service_tier``) is rejected by the registry's value guards instead of passing as an
ordinary string.

Usage::

    python scripts/check_usage_fields_priced.py                  # scans ~/.claude/projects
    python scripts/check_usage_fields_priced.py DIR_OR_FILE ...  # e.g. tests/fixtures/usage_fields

Exit codes: 0 every key path and value is covered; 1 at least one is not; 2 nothing could be
scanned (a missing path, no usage records) -- "could not check" is never a pass. Output is key
paths, counts and file paths only -- never transcript content. CI cannot scan real transcripts;
its always-on guard is tests/test_usage_fields_priced.py over a committed content-free fixture of
real-shaped usage records (and over the registry itself).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = _ROOT / "config" / "usage_field_rules.json"
RULES = ("priced", "free", "ignored", "unmodelled")


@dataclass
class UsageScan:
    """Key paths (and guarded values) found under ``message.usage`` in a transcript tree."""

    files_scanned: int = 0
    records: int = 0
    paths: Counter[str] = field(default_factory=Counter)  # key path -> records carrying it
    example_file: dict[str, Path] = field(default_factory=dict)  # key path -> first file seen
    values: dict[str, Counter[str]] = field(default_factory=dict)  # guarded path -> value counts


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    """Load the registry and check it is well formed (every entry has a rule and a reason)."""
    reg: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    for key_path, entry in reg["fields"].items():
        if entry.get("rule") not in RULES or not str(entry.get("reason", "")).strip():
            raise ValueError(f"registry entry {key_path!r} needs rule in {RULES} and a reason")
    return reg


def flatten(prefix: str, obj: Any, out: list[tuple[str, Any]]) -> None:
    """Append ``(key_path, leaf_value)`` for every leaf; list elements use ``[]``. Containers have no
    path of their own: an empty one carries nothing to price, and its children are checked."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(obj, list):
        for item in obj:
            flatten(f"{prefix}[]", item, out)
    else:
        out.append((prefix, obj))


def scan_usage_fields(roots: Iterable[Path], guarded: Iterable[str] = ()) -> UsageScan:
    """Collect every key path under ``message.usage`` of an assistant record (``**/*.jsonl``)."""
    guard_set = set(guarded)
    result = UsageScan()
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.jsonl"))
        for path in files:
            result.files_scanned += 1
            try:
                with path.open(encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if '"usage"' not in line:
                            continue  # cheap pre-filter; transcripts run to gigabytes
                        try:
                            rec: Any = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(rec, dict) or rec.get("type") != "assistant":
                            continue
                        message = rec.get("message")
                        usage = message.get("usage") if isinstance(message, dict) else None
                        if not isinstance(usage, dict):
                            continue
                        result.records += 1
                        flat: list[tuple[str, Any]] = []
                        flatten("usage", usage, flat)
                        for key_path, value in flat:
                            result.paths[key_path] += 1
                            result.example_file.setdefault(key_path, path)
                            if key_path in guard_set and value is not None:
                                result.values.setdefault(key_path, Counter())[repr(value)] += 1
            except OSError as e:
                print(f"warning: could not read {path}: {e}", file=sys.stderr)
    return result


def violations(scan: UsageScan, registry: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return ``(unregistered_key_paths, guarded_values_outside_the_allowed_set)``."""
    fields: dict[str, Any] = registry["fields"]
    unknown = sorted(p for p in scan.paths if p not in fields)
    guards: dict[str, list[Any]] = registry.get("value_guards", {})
    bad: list[str] = []
    for key_path, allowed in sorted(guards.items()):
        allowed_reprs = {repr(a) for a in allowed}
        for value_repr, n in sorted(scan.values.get(key_path, {}).items()):
            if value_repr not in allowed_reprs:
                bad.append(f"{key_path} = {value_repr} ({n} record(s))")
    return unknown, bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on usage fields with no pricing rule.")
    parser.add_argument("paths", nargs="*", type=Path, help="transcript dirs/files")
    args = parser.parse_args(argv)
    roots: list[Path] = args.paths or [Path.home() / ".claude" / "projects"]

    missing = [r for r in roots if not r.exists()]
    if missing:
        print(f"COULD NOT CHECK: no such path: {', '.join(map(str, missing))}", file=sys.stderr)
        return 2
    registry = load_registry()
    scan = scan_usage_fields(roots, registry.get("value_guards", {}))
    if scan.records == 0:
        print(
            f"COULD NOT CHECK: {scan.files_scanned} file(s) scanned, 0 assistant usage records",
            file=sys.stderr,
        )
        return 2
    unknown, bad_values = violations(scan, registry)
    if not unknown and not bad_values:
        print(
            f"OK: {len(scan.paths)} usage key path(s) across {scan.records} record(s) in "
            f"{scan.files_scanned} file(s) all have a pricing rule"
        )
        return 0
    if unknown:
        print(
            "USAGE KEY PATHS with no pricing rule (decide: price it in tes/cost.py, or add it to "
            "config/usage_field_rules.json as free/ignored with a reason, and document it in "
            "docs/PRICING_FIELDS.md):",
            file=sys.stderr,
        )
        for p in unknown:
            print(
                f"  - {p}: {scan.paths[p]} record(s), e.g. {scan.example_file[p]}", file=sys.stderr
            )
    if bad_values:
        print("USAGE VALUES that change the bill and are not modelled:", file=sys.stderr)
        for v in bad_values:
            print(f"  - {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
