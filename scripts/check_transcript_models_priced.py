"""scripts/check_transcript_models_priced.py — fail when a model id that appears in real Claude
Code transcripts cannot be priced by the bundled price table.

Why this exists: claude-opus-5-5 and claude-sonnet-5-5 were in the author's own transcripts for
weeks (84,140 assistant turns) while the price table, and the CI that is meant to police it,
knew nothing of them -- the vendor-page check can only see what the vendor lists, and CI has no
real transcripts. This script closes the other side of that gap: it scans a directory of
transcripts, collects every ``message.model`` of an assistant record (main and subagent files
alike) and exits non-zero listing each id the table does not resolve.

Usage (local, and as a documented pre-release step, see RELEASING.md)::

    python scripts/check_transcript_models_priced.py            # scans ~/.claude/projects
    python scripts/check_transcript_models_priced.py DIR [DIR ...]

Exit codes: 0 every id resolves; 1 at least one id is unpriced; 2 nothing could be scanned (no
directory / no transcripts) -- "could not check" is never reported as a pass. Non-model ids such
as ``<synthetic>`` (tes.cost.NON_MODEL_IDS) are excluded by design. Output contains model ids,
counts and file paths only -- never transcript content. Always checks the BUNDLED table, not a
~/.tes/prices.json override. CI cannot run this against real data; the always-on guard there is
tests/test_transcript_models_priced.py, which runs it over a committed fixture of model ids.
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
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tes.cost import NON_MODEL_IDS, _resolve_model, load_price_table  # noqa: E402

_BUNDLED_PRICES = _ROOT / "tes" / "data" / "prices.json"


@dataclass
class ScanResult:
    """Model ids found in a transcript tree."""

    files_scanned: int = 0
    turns: Counter[str] = field(default_factory=Counter)  # model id -> assistant records
    example_file: dict[str, Path] = field(default_factory=dict)  # model id -> first file seen


def scan_transcript_models(roots: Iterable[Path]) -> ScanResult:
    """Collect ``message.model`` of every assistant record under ``roots`` (``**/*.jsonl``)."""
    result = ScanResult()
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.jsonl"))
        for path in files:
            result.files_scanned += 1
            try:
                with path.open(encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if '"assistant"' not in line or '"model"' not in line:
                            continue  # cheap pre-filter; transcripts run to gigabytes
                        try:
                            rec: Any = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(rec, dict) or rec.get("type") != "assistant":
                            continue
                        message = rec.get("message")
                        model = message.get("model") if isinstance(message, dict) else None
                        if isinstance(model, str):
                            result.turns[model] += 1
                            result.example_file.setdefault(model, path)
            except OSError as e:
                print(f"warning: could not read {path}: {e}", file=sys.stderr)
    return result


def unresolvable_models(models: Iterable[str], prices: dict[str, Any]) -> list[str]:
    """Model ids that are neither a non-model id nor resolvable against ``prices``."""
    bad: list[str] = []
    for model in sorted(set(models)):
        if model.strip() in NON_MODEL_IDS:
            continue
        key, _, _ = _resolve_model(model, prices)
        if key is None:
            bad.append(model)
    return bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on transcript model ids with no price.")
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="transcript directories or files (default: ~/.claude/projects)",
    )
    args = parser.parse_args(argv)
    roots: list[Path] = args.paths or [Path.home() / ".claude" / "projects"]

    missing_roots = [r for r in roots if not r.exists()]
    if missing_roots:
        print(
            f"COULD NOT CHECK: no such path: {', '.join(map(str, missing_roots))}", file=sys.stderr
        )
        return 2
    result = scan_transcript_models(roots)
    if result.files_scanned == 0 or not result.turns:
        print(
            f"COULD NOT CHECK: {result.files_scanned} transcript file(s) scanned, "
            f"{sum(result.turns.values())} assistant model id(s) found",
            file=sys.stderr,
        )
        return 2

    bad = unresolvable_models(result.turns, load_price_table(_BUNDLED_PRICES))
    if not bad:
        priced = sorted(m for m in result.turns if m.strip() not in NON_MODEL_IDS)
        print(
            f"OK: {len(priced)} model id(s) across {result.files_scanned} file(s) all price: "
            + ", ".join(priced)
        )
        return 0
    print(
        "UNPRICED MODEL IDS in transcripts (add them to tes/data/prices.json from the official "
        "page, then re-run scripts/check_price_table_vs_vendor.py):",
        file=sys.stderr,
    )
    for model in bad:
        print(
            f"  - {model!r}: {result.turns[model]} assistant record(s), e.g. "
            f"{result.example_file[model]}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
