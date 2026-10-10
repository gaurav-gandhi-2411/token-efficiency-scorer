from __future__ import annotations

"""tes/exit_codes.py -- the CLI's process exit codes, documented in docs/EXIT_CODES.md.

Scripts, hooks and CI jobs branch on these, so they are a contract: add new codes, never
renumber. 2 is deliberately not used by us because argparse already exits 2 on a usage error.
"""

import argparse

EXIT_OK = 0
# Bad usage, a path that does not exist, no sessions found, an unopenable store: the pre-existing
# "nothing was done" code. Kept at 1 so every existing failure path is unchanged.
EXIT_USAGE = 1
# 2 = argparse usage error (unknown flag, missing argument). Not raised by our own code.
# `tes monitor`: the cost/context alarm fired for the active session. A hook or cron job can act
# on it (`tes monitor || notify`). Distinct from 1 so "alarm" is never confused with "error".
EXIT_ALARM = 3
# `tes score`: at least one session could not be read/parsed (the rest were still scored).
EXIT_ADAPT_ERROR = 4

EXIT_CODES_HEADING = "Exit codes:"

_DESCRIPTIONS: dict[int, str] = {
    EXIT_OK: "ok (including: scored with no findings, or monitor with no active session)",
    EXIT_USAGE: "bad usage, path not found, no sessions found, or the store cannot be opened",
    2: "command-line usage error (reported by argparse)",
    EXIT_ALARM: "monitor: the cost/context alarm fired",
    EXIT_ADAPT_ERROR: "score: at least one session could not be parsed (others still scored)",
}


def epilog(*codes: int) -> str:
    """Render an `--help` epilog listing the given exit codes (always starts with the heading)."""
    lines = [EXIT_CODES_HEADING]
    lines += [f"  {code}  {_DESCRIPTIONS[code]}" for code in codes]
    lines.append("See docs/EXIT_CODES.md for the full contract.")
    return "\n".join(lines)


class ExitCodeHelpFormatter(argparse.HelpFormatter):
    """Default help wrapping, except an epilog that starts with the exit-code heading is verbatim.

    RawDescriptionHelpFormatter would also stop wrapping every command description; this keeps
    those exactly as before and only protects the one-code-per-line table.
    """

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        if text.startswith(EXIT_CODES_HEADING):
            return "\n".join(indent + line for line in text.splitlines())
        return super()._fill_text(text, width, indent)
