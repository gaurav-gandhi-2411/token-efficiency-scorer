from __future__ import annotations

"""Generates the README's example output blocks from SYNTHETIC sessions, with the real CLI.

Every `tes cost`, `tes cost --roi`, `tes budget` and `tes monitor` block in the README is the
verbatim stdout of this build on invented transcripts: no real session content, no real prices
beyond the bundled table. The clock is frozen at NOW (below) so the output is the same on every run;
tests/test_readme_examples.py regenerates the blocks and fails when the README drifts from them.

    python scripts/gen_readme_examples.py                 # print every block
    python scripts/gen_readme_examples.py --check README.md   # exit 1 if a block is not in the file
    python scripts/gen_readme_examples.py --ask "<question>"  # tes ask on a synthetic store (needs
                                                              # the [patterns] extra and a local Ollama)

Each scenario builds its own throw-away store and projects directory under a temp directory, so the
real ~/.tes store and ~/.claude/projects are never read or written.
"""

import argparse
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc  # datetime.UTC is 3.11+; this package supports 3.10

# The frozen clock. 2026-10-11T12:00:00Z; every session mtime below is relative to it.
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)
NOW_TS = NOW.timestamp()
DAY = 86400.0

PROMPT = "Investigate why the nightly export job times out and fix it."


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> _FrozenDatetime:  # type: ignore[override]
        return cls.fromtimestamp(NOW_TS, tz=tz)


def _records(model: str, turns: int, ctx0: int, step: int, out: int, tag: str) -> list[dict]:
    """A plausible agent loop: the cached context grows by `step` tokens a turn."""
    recs: list[dict] = []
    for i in range(turns):
        recs.append(
            {
                "type": "user",
                "message": {"role": "user", "content": PROMPT if i == 0 else f"continue {i}"},
            }
        )
        recs.append(
            {
                "type": "assistant",
                "message": {
                    "id": f"{tag}-{i}",
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "text", "text": f"step {i} done"}],
                    "usage": {
                        "input_tokens": 150,
                        "cache_creation_input_tokens": step,
                        "cache_read_input_tokens": ctx0 + i * step,
                        "output_tokens": out,
                    },
                },
            }
        )
    return recs


def _write_session(
    root: Path,
    project: str,
    name: str,
    model: str,
    age_days: float,
    *,
    turns: int = 40,
    ctx0: int = 20_000,
    step: int = 4_000,
    out: int = 600,
    age_seconds: float | None = None,
) -> Path:
    path = root / "projects" / project / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    recs = _records(model, turns, ctx0, step, out, name)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    age = age_seconds if age_seconds is not None else age_days * DAY
    ts = NOW_TS - age
    os.utime(path, (ts, ts))
    return path


class _Sandbox:
    """One scenario: its own projects tree, store, plan file, and a way to run the CLI in it."""

    def __init__(self, base: Path, name: str) -> None:
        self.root = base / name
        self.root.mkdir(parents=True)
        self.db = self.root / "tes.db"

    def run(self, *argv: str) -> tuple[int, str]:
        import tes.cli as cli

        old_argv, old_env = sys.argv, dict(os.environ)
        os.environ.update({"TES_DB_PATH": str(self.db), "TES_NO_NOTICE": "1"})
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["TES_PLAN_PATH"] = str(self.root / "no-plan-here.json")
        sys.argv = ["tes", *argv]
        buf, err, code = io.StringIO(), io.StringIO(), 0
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                try:
                    cli.main()
                except SystemExit as exc:
                    code = int(exc.code or 0)
        finally:
            sys.argv = old_argv
            os.environ.clear()
            os.environ.update(old_env)
        return code, buf.getvalue()

    def score_all(self) -> None:
        for proj in sorted((self.root / "projects").iterdir()):
            code, _ = self.run("score", str(proj), "--no-judge")
            if code != 0:
                raise RuntimeError(f"scoring {proj} exited {code}")

    def sql(self, statement: str, params: tuple = ()) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute(statement, params)
        conn.commit()
        conn.close()


def _trim(text: str) -> str:
    lines = [ln.rstrip() for ln in text.strip("\n").splitlines()]
    return "\n".join(lines)


# --------------------------------------------------------------------------- scenarios


def scenario_cost(base: Path) -> dict[str, str]:
    """`tes cost --week` / `--month` on priced sessions, one of them older than the week."""
    sb = _Sandbox(base, "cost")
    _write_session(sb.root, "demo-api", "api-1", "claude-sonnet-5-5", 1.0, turns=60, step=6_000)
    _write_session(sb.root, "demo-api", "api-2", "claude-sonnet-5-5", 3.0, turns=45, step=5_000)
    _write_session(sb.root, "demo-web", "web-1", "claude-opus-5-5", 2.0, turns=50, step=7_000)
    _write_session(sb.root, "demo-api", "api-old", "claude-sonnet-5-5", 12.0, turns=70, step=6_000)
    sb.score_all()
    return {
        "cost_week": _trim(sb.run("cost", "--week")[1]),
        "cost_month": _trim(sb.run("cost", "--month")[1]),
    }


def scenario_cost_unpriced(base: Path) -> dict[str, str]:
    """Priced coverage: one session on a model that is not in the price table."""
    sb = _Sandbox(base, "unpriced")
    _write_session(sb.root, "demo-api", "api-1", "claude-sonnet-5-5", 1.0, turns=30, step=3_000)
    _write_session(sb.root, "demo-web", "web-1", "claude-future-9", 1.0, turns=30, step=3_000)
    sb.score_all()
    return {"cost_unpriced": _trim(sb.run("cost", "--week")[1])}


def scenario_cost_missing(base: Path) -> dict[str, str]:
    """A session stored without a cost (stands in for a row written before costs were stored)."""
    sb = _Sandbox(base, "missing")
    _write_session(sb.root, "demo-api", "api-1", "claude-sonnet-5-5", 1.0, turns=30, step=3_000)
    _write_session(sb.root, "demo-api", "api-2", "claude-sonnet-5-5", 2.0, turns=30, step=3_000)
    sb.score_all()
    sb.sql(
        "UPDATE sessions SET session_cost_usd = NULL WHERE session_id = "
        "(SELECT session_id FROM sessions ORDER BY session_id LIMIT 1)"
    )
    return {"cost_missing": _trim(sb.run("cost", "--week")[1])}


def scenario_cost_unattributable(base: Path) -> dict[str, str]:
    """An unpriced model plus a stored cost blanked and its model name lost."""
    sb = _Sandbox(base, "unattributable")
    _write_session(sb.root, "demo-api", "api-1", "claude-sonnet-5-5", 1.0, turns=30, step=3_000)
    _write_session(sb.root, "demo-web", "web-1", "gpt-6-preview", 1.0, turns=30, step=3_000)
    _write_session(sb.root, "demo-web", "web-2", "claude-future-9", 2.0, turns=30, step=3_000)
    sb.score_all()
    sb.sql(
        "UPDATE sessions SET session_cost_usd = NULL, cost_unpriced_models = NULL "
        "WHERE source_path LIKE '%web-2.jsonl'"
    )
    return {"cost_unattributable": _trim(sb.run("cost", "--week")[1])}


def scenario_roi(base: Path) -> dict[str, str]:
    """`tes cost --week --roi` against a plan history that starts before the window."""
    sb = _Sandbox(base, "roi")
    for i in range(12):
        _write_session(
            sb.root,
            "demo-api" if i % 2 == 0 else "demo-web",
            f"s{i}",
            "claude-opus-5-5",
            0.4 + 0.5 * i,
            turns=100,
            ctx0=40_000,
            step=7_000,
            out=2_500,
        )
    sb.score_all()
    plan = sb.root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "plans": [
                    {"name": "Claude Pro", "monthly_cost_usd": 20, "effective_from": "2026-01-01"},
                    {"name": "Claude Max", "monthly_cost_usd": 200, "effective_from": "2026-07-01"},
                ]
            }
        ),
        encoding="utf-8",
    )
    out = sb.run("cost", "--week", "--roi", "--plan-config", str(plan))[1]
    lines = _trim(out).splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("Plan:"))
    return {"roi": "\n".join(lines[start:]).split("\n" + "─" * 70)[0]}


def scenario_budget(base: Path) -> dict[str, str]:
    """`tes budget` over a week of work, and on a store with nothing recent."""
    sb = _Sandbox(base, "budget")
    for i in range(10):
        _write_session(
            sb.root,
            "demo-api",
            f"s{i}",
            "claude-sonnet-5-5" if i % 3 else "claude-opus-5-5",
            0.3 + 0.55 * i,
            turns=60,
            ctx0=30_000,
            step=6_000,
            out=1_200,
        )
    sb.score_all()
    quiet = _Sandbox(base, "budget-quiet")
    _write_session(quiet.root, "demo-api", "old", "claude-sonnet-5-5", 40.0)
    quiet.score_all()
    return {
        "budget": _trim(sb.run("budget")[1]),
        "budget_quiet": _trim(quiet.run("budget")[1]),
    }


def scenario_monitor(base: Path) -> dict[str, str]:
    """`tes monitor` on a live session far above the maintainer's own recent sessions."""
    sb = _Sandbox(base, "monitor")
    for i in range(12):
        _write_session(
            sb.root,
            "demo-api",
            f"hist-{i}",
            "claude-sonnet-5-5",
            1.0 + 2 * i,
            turns=35 + (i % 5) * 4,
            ctx0=25_000,
            step=4_000 + 300 * (i % 4),
        )
    sb.score_all()
    live = _write_session(
        sb.root,
        "demo-api",
        "a1b2c3d4-0000-4000-8000-000000000001",
        "claude-sonnet-5-5",
        0,
        turns=110,
        ctx0=30_000,
        step=7_000,
        age_seconds=20,
    )
    live.touch()
    os.utime(live, (NOW_TS - 20, NOW_TS - 20))
    code, out = sb.run("monitor", "--cc-path", str(sb.root / "projects"))
    return {"monitor_alarm": _trim(out), "monitor_exit_code": str(code)}


SCENARIOS = (
    scenario_cost,
    scenario_cost_unpriced,
    scenario_cost_missing,
    scenario_cost_unattributable,
    scenario_roi,
    scenario_budget,
    scenario_monitor,
)


def generate() -> dict[str, str]:
    """Every README example block, keyed by name. Deterministic: clock frozen, data invented."""
    import tes.budget
    import tes.cost_period

    patched = [
        (tes.budget, "datetime"),
        (tes.cost_period, "datetime"),
    ]
    saved = [(mod, attr, getattr(mod, attr)) for mod, attr in patched]
    real_time = time.time
    blocks: dict[str, str] = {}
    try:
        for mod, attr in patched:
            setattr(mod, attr, _FrozenDatetime)
        time.time = lambda: NOW_TS  # type: ignore[assignment]
        with tempfile.TemporaryDirectory(prefix="tes-readme-examples-") as tmp:
            for scenario in SCENARIOS:
                blocks.update(scenario(Path(tmp)))
    finally:
        time.time = real_time  # type: ignore[assignment]
        for mod, attr, original in saved:
            setattr(mod, attr, original)
    return blocks


def run_ask(question: str) -> str:
    """`tes ask` on a synthetic 36-session store against a local Ollama (not deterministic)."""
    with tempfile.TemporaryDirectory(prefix="tes-ask-examples-") as tmp:
        sb = _Sandbox(Path(tmp), "ask")
        shapes = [
            (30 + (i * 7) % 60, 3_000 + (i * 900) % 6_000, 400 + (i * 130) % 1_600)
            for i in range(36)
        ]
        for i, (turns, step, out) in enumerate(shapes):
            _write_session(
                sb.root,
                f"demo-{'api' if i % 3 == 0 else 'web' if i % 3 == 1 else 'jobs'}",
                f"s{i}",
                "claude-sonnet-5-5" if i % 4 else "claude-opus-5-5",
                0.2 + 0.7 * i,
                turns=turns,
                step=step,
                out=out,
            )
        sb.score_all()
        code, out = sb.run("ask", question, "--db-path", str(sb.db))
        return f"[exit {code}]\n{out}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the README example blocks from synthetic sessions."
    )
    parser.add_argument("--check", metavar="README", help="fail unless every block is in this file")
    parser.add_argument("--ask", metavar="QUESTION", help="run `tes ask` once on a synthetic store")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # box-drawing characters
    if args.ask:
        print(run_ask(args.ask))
        return 0
    blocks = generate()
    if args.check:
        text = Path(args.check).read_text(encoding="utf-8").replace("\r\n", "\n")
        missing = [k for k, v in blocks.items() if k != "monitor_exit_code" and v not in text]
        for key in missing:
            print(f"README does not contain the generated block {key!r}", file=sys.stderr)
        return 1 if missing else 0
    for key, text in blocks.items():
        print(f"===== {key} =====\n{text}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
