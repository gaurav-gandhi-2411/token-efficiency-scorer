from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Resolve paths relative to repo root
# ---------------------------------------------------------------------------
ROOT: Path = Path(__file__).parent.parent
TAXONOMY_PATH: Path = ROOT / "data" / "validation-corpus" / "taxonomy" / "task_taxonomy.json"
ANNOTATIONS_DIR: Path = ROOT / "data" / "validation-corpus" / "annotations" / "gpt_oss"
TRACES_DIR: Path = ROOT / "data" / "validation-corpus" / "traces_normalized"
OUTPUT_PATH: Path = ROOT / "data" / "layer1_outputs.jsonl"

# Import after path constants so that any import errors surface immediately.
from token_efficiency.layer1_features import (  # noqa: E402
    LayerOneFeatures,
    compute_domain_p25_baselines,
    load_corpus,
)
from token_efficiency.trace_digest import (  # noqa: E402
    SessionDigest,
    build_digest,
    digest_to_dict,
)


def _load_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON file and return its contents, or None if the file is absent."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[return-value]


def main() -> None:
    """Process all corpus sessions and write a JSONL of features + digests.

    Each line in the output file contains all LayerOneFeatures fields plus a
    'digest' key holding the SessionDigest as a plain dict.

    Summary counts (total, missing annotations, missing traces) are printed to
    stdout after processing completes.
    """
    print(f"[run_layer1] Loading corpus from {TAXONOMY_PATH}", file=sys.stderr)

    # load_corpus handles missing annotation/trace files gracefully.
    features_list: list[LayerOneFeatures] = load_corpus(TAXONOMY_PATH, ANNOTATIONS_DIR, TRACES_DIR)

    # Re-read taxonomy to get session_ids in order for trace/annotation loading.
    taxonomy: list[dict[str, Any]] = json.loads(
        TAXONOMY_PATH.read_text(encoding="utf-8")
    )
    sid_to_row: dict[str, dict[str, Any]] = {row["session_id"]: row for row in taxonomy}

    # Ensure output directory exists.
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    missing_annotations: int = 0
    missing_traces: int = 0
    total_sessions: int = len(features_list)

    with OUTPUT_PATH.open("w", encoding="utf-8") as out_fh:
        for features in features_list:
            sid: str = features.session_id

            annotation_path: Path = ANNOTATIONS_DIR / f"{sid}.json"
            trace_path: Path = TRACES_DIR / f"{sid}.json"

            annotation: dict[str, Any] | None = _load_json(annotation_path)
            trace: dict[str, Any] | None = _load_json(trace_path)

            if annotation is None:
                missing_annotations += 1
            if trace is None:
                missing_traces += 1

            digest: SessionDigest = build_digest(
                session_id=sid,
                features=features,
                trace=trace,
                annotation=annotation,
            )

            record: dict[str, Any] = dataclasses.asdict(features)
            record["digest"] = digest_to_dict(digest)

            out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(
        f"[run_layer1] Done.\n"
        f"  Total sessions  : {total_sessions}\n"
        f"  Missing annotations: {missing_annotations}\n"
        f"  Missing traces     : {missing_traces}\n"
        f"  Output written to  : {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
