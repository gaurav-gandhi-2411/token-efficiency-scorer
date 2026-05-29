# Post-A.1 Cleanup Backlog

Items deferred from Phase A.1 to avoid mid-batch disruption. Execute after
all annotation batches are complete and before Phase A.2 work begins.

---

## CB-001 — Directory renaming: `gpt_oss/` → labeler-agnostic layout

**Why deferred:** mid-batch restructuring risked losing the 158 in-flight
Anthropic batch results (msgbatch_01DsfCb6tfAVU3TXTC497F8e).

**Tasks:**
1. Rename `data/validation-corpus/annotations/gpt_oss/` → `annotations/bulk/`
   (or `annotations/` flat — decide at execution time)
2. Update `_poll_anthropic_batch` and any `_save_response` helpers to write
   to the new path
3. Migrate existing 42 files (35 GPT-OSS + 7 Haiku) to the new path
4. Update all downstream readers that hardcode `gpt_oss`:
   - `scripts/01_annotate_corpus.py` (`GPT_OSS_DIR` constant)
   - `scripts/10_remeasure_heuristics.py` (any hardcoded path)
   - Any other script referencing the old path
5. Backport: add `labeler_model` field to the 35 GPT-OSS annotation files
   (currently absent — Haiku files carry it but the original GPT-OSS files
   do not). Value to write: `"openai/gpt-oss-120b"`.

**Verification:** all downstream scripts run end-to-end; annotation count
unchanged; `labeler_model` present in all 200 annotation files.
