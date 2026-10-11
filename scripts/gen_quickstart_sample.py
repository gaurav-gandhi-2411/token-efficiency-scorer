from __future__ import annotations

"""Regenerates tes/data/quickstart_sample_session.jsonl (the SYNTHETIC `quickstart` demo).

Why this exists: the sample must clear the research-recon turn gate of the shipped baseline
(tes/data/cc_baselines.json scope_gates) so `quickstart` prints a real token verdict, and must
contain one deterministic waste event (a REPEATED-FAILED-RETRY with proof turns) so the first run
shows a finding. It is fully synthetic: invented module names, placeholder file bodies, no real
session content. Run `python scripts/gen_quickstart_sample.py` to rewrite the file; output is
deterministic (no randomness).
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tes" / "data" / "quickstart_sample_session.jsonl"
MODEL = "claude-sonnet-5"
BODY = "    pass  # synthetic body\n" * 16

BILLING = [
    "charge_dispatcher",
    "retry_policy",
    "webhook_handler",
    "idempotency_store",
    "ledger_writer",
    "invoice_builder",
    "proration",
    "tax_calculator",
    "coupon_engine",
    "subscription_state",
    "dunning_scheduler",
    "usage_meter",
    "plan_catalog",
    "credit_notes",
    "payment_methods",
    "billing_events",
    "export_jobs",
    "quota_enforcer",
    "trial_manager",
    "receipt_mailer",
    "seat_counter",
    "overage_rules",
    "audit_trail",
]
PAYMENTS = [
    "gateway_client",
    "card_tokenizer",
    "refund_processor",
    "dispute_handler",
    "settlement_batch",
    "currency_convert",
    "fraud_signals",
    "payout_scheduler",
    "reconciliation",
    "audit_log",
    "notification_bridge",
    "three_ds_flow",
    "vault_client",
    "chargeback_feed",
    "risk_scoring",
    "ach_adapter",
    "sepa_adapter",
    "wallet_bridge",
    "fee_schedule",
    "limits_guard",
    "bank_directory",
    "statement_import",
    "sandbox_switch",
]
NOTES = [
    "retries on 5xx with exponential backoff, capped at 3 attempts.",
    "catches the gateway timeout but does not distinguish idempotent from non-idempotent calls.",
    "signature check happens after the payload is parsed.",
    "key TTL is shorter than a plausible retry window.",
    "writes are not wrapped in the same transaction as the charge call.",
    "no circuit breaker; an outage is retried at full rate.",
    "refresh silently swallows expired-token errors.",
    "re-uses the charge retry policy, which is not safe for refunds.",
    "logs more card metadata than needed.",
    "batching has no dedup guard across restarts.",
    "rate is fetched per request with no cache.",
    "thresholds are hardcoded rather than configured.",
    "assumes UTC everywhere.",
    "compares totals only, not line items.",
    "write is fire-and-forget; failures are dropped.",
]
FAIL = (
    "Exit code 1\nFAILED tests/payments/test_gateway_client.py::test_timeout_retry - "
    "ConnectionError: sandbox gateway unreachable (synthetic)\n1 failed in 0.41s\n"
)
RETRY_AFTER = 20  # review cycles completed before the retry episode


def _line(kind: str, content: object, usage: dict[str, int] | None = None) -> str:
    msg: dict[str, object] = {"content": content}
    if kind == "user":
        msg = {"role": "user", "content": content}
    else:
        msg = {"model": MODEL, "content": content, "usage": usage}
    return json.dumps({"type": kind, "isSidechain": False, "message": msg})


def build() -> list[str]:
    files = [f"billing/{n}.py" for n in BILLING] + [f"payments/{n}.py" for n in PAYMENTS]
    lines = [
        _line(
            "user",
            "[Synthetic demo session -- not a real audit] Review and summarize how retry and "
            "failure-recovery logic is implemented across the billing and payments modules. Flag "
            "anything that looks like a real correctness or security concern -- don't just "
            "describe the code, assess it critically.",
        )
    ]
    ctx = 0  # tokens already in the (cached) context
    call = 0

    def usage(new_in: int, created: int, out: int) -> dict[str, int]:
        return {
            "input_tokens": new_in,
            "cache_creation_input_tokens": created,
            "cache_read_input_tokens": ctx,
            "output_tokens": out,
        }

    def tool_cycle(name: str, tool_input: dict[str, str], result: str, note: str) -> None:
        nonlocal ctx, call
        call += 1
        out = 600 + (call % 5) * 110
        lines.append(
            _line(
                "assistant",
                [{"type": "tool_use", "id": f"call_{call}", "name": name, "input": tool_input}],
                usage(4200 + call * 20, 2400 + call * 15, out),
            )
        )
        ctx += 2400 + call * 15 + 600
        lines.append(
            _line(
                "user",
                [{"type": "tool_result", "tool_use_id": f"call_{call}", "content": result}],
            )
        )
        lines.append(
            _line("assistant", [{"type": "text", "text": note}], usage(1500 + call * 10, 0, out))
        )
        ctx += out

    for i, path in enumerate(files[:46]):
        note = f"{path}: {NOTES[i % len(NOTES)]}"
        tool_cycle("Read", {"file_path": path}, f"# {path}\n{BODY}", note)
        if i + 1 == RETRY_AFTER:
            cmd = {"command": "pytest tests/payments/test_gateway_client.py -x -q"}
            tool_cycle("Bash", cmd, FAIL, "Test run failed; retrying the same command.")
            tool_cycle("Bash", cmd, FAIL, "Same failure again; moving on with the file review.")
    lines.append(
        _line(
            "assistant",
            [
                {
                    "type": "text",
                    "text": "Summary: reviewed 46 files across billing/ and payments/. Three "
                    "findings worth prioritizing: (1) the idempotency key TTL is shorter than a "
                    "plausible retry window and can double-charge, (2) ledger writes aren't "
                    "transactional with the charge call, and (3) the dispute handler logs full "
                    "card metadata. The rest is a lower-severity punch list, not fixed here. "
                    "The gateway test failed twice with the same error and was not investigated.",
                }
            ],
            usage(9000, 0, 2400),
        )
    )
    return lines


if __name__ == "__main__":
    OUT.write_text("\n".join(build()) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(build())} lines)")
