"""Full corpus composition audit: scaffolds, models, tasks, diversity gaps."""
from __future__ import annotations
import json, pathlib, collections

traces_dir = pathlib.Path("data/validation-corpus/traces_normalized")
taxonomy_path = pathlib.Path("data/validation-corpus/taxonomy/task_taxonomy.json")

sessions = [json.loads(f.read_text()) for f in sorted(traces_dir.glob("*.json"))]
taxonomy = {r["session_id"]: r for r in json.loads(taxonomy_path.read_text())}

# 1. Scaffold breakdown
print("=== SCAFFOLD BREAKDOWN ===")
scaffold_counts: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"total": 0, "resolved": 0})
for s in sessions:
    sc = s["scaffold"]
    resolved = s["outcome"]["result"] in ("pass", "resolved", True, "true", "1")
    scaffold_counts[sc]["total"] += 1
    scaffold_counts[sc]["resolved"] += int(resolved)
for sc, c in sorted(scaffold_counts.items()):
    print(f"  {sc:<30} total={c['total']:>3}  resolved={c['resolved']:>3} ({100*c['resolved']//c['total']}%)")

# 2. Model breakdown
print("\n=== MODEL BREAKDOWN ===")
model_counts: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"total": 0, "resolved": 0})
for s in sessions:
    m = s.get("model") or "unknown"
    resolved = s["outcome"]["result"] in ("pass", "resolved", True, "true", "1")
    model_counts[m]["total"] += 1
    model_counts[m]["resolved"] += int(resolved)
for m, c in sorted(model_counts.items(), key=lambda x: -x[1]["total"]):
    print(f"  {m:<40} total={c['total']:>3}  resolved={c['resolved']:>3} ({100*c['resolved']//max(c['total'],1)}%)")

# 3. Scaffold × model matrix
print("\n=== SCAFFOLD × MODEL MATRIX ===")
sc_m: dict[tuple, int] = collections.Counter()
for s in sessions:
    sc_m[(s["scaffold"], s.get("model") or "unknown")] += 1
for (sc, m), cnt in sorted(sc_m.items()):
    print(f"  {sc:<30} × {m:<35} = {cnt}")

# 4. Source dataset breakdown
print("\n=== SOURCE DATASET ===")
src_counts = collections.Counter(s.get("source_dataset", "unknown") for s in sessions)
for src, cnt in src_counts.most_common():
    print(f"  {src:<50} {cnt}")

# 5. Repo diversity
print("\n=== TOP REPOS (by instance count) ===")
repo_counts: collections.Counter = collections.Counter()
for s in sessions:
    iid = s["instance_id"]
    repo = iid.split("__")[-1].rsplit("-", 1)[0] if "__" in iid else iid
    repo_counts[repo] += 1
for repo, cnt in repo_counts.most_common(15):
    print(f"  {cnt:>2}x  {repo}")
print(f"  ... {len(repo_counts)} unique repos total")

# 6. Task class distribution
print("\n=== TASK CLASS DISTRIBUTION ===")
tc_counts = collections.Counter(taxonomy[s["session_id"]]["domain"] for s in sessions if s["session_id"] in taxonomy)
for cls, cnt in tc_counts.most_common():
    print(f"  {cls:<30} {cnt:>3}")

# 7. Language check (from patch diffs)
print("\n=== FILE EXTENSIONS IN PATCHES ===")
import re
ext_counts: collections.Counter = collections.Counter()
for s in sessions:
    diff = s["outcome"].get("patch_diff") or ""
    for path in re.findall(r"^(?:\+\+\+|---) (?:a|b)/(.+)$", diff, re.MULTILINE):
        ext = pathlib.Path(path).suffix or "(no ext)"
        ext_counts[ext] += 1
for ext, cnt in ext_counts.most_common(15):
    print(f"  {ext:<20} {cnt}")

# 8. Diversity score
n_scaffolds = len(scaffold_counts)
n_models = len(model_counts)
n_repos = len(repo_counts)
print(f"\n=== DIVERSITY SUMMARY ===")
print(f"  Scaffolds:      {n_scaffolds} (min 3 required)")
print(f"  Base models:    {n_models} (min 2 required)")
print(f"  Unique repos:   {n_repos}")
print(f"  Languages:      1 (Python only — no non-Python patches)")
print(f"  Task types:     SWE-bench bugfix only (no feature/refactor/docs)")
print(f"  OOD coverage:   None")
