"""Check which instance_ids are unclassified."""
from __future__ import annotations
import json, pathlib, collections

taxonomy_path = pathlib.Path("data/validation-corpus/taxonomy/task_taxonomy.json")
records = json.loads(taxonomy_path.read_text())

unknowns = [r for r in records if r["domain"] == "unknown"]
repo_names = collections.Counter()
for r in unknowns:
    iid = r["instance_id"]
    repo_part = iid.split("__")[-1] if "__" in iid else iid
    # strip issue number
    repo = "-".join(repo_part.split("-")[:-1]) if "-" in repo_part else repo_part
    repo_names[repo] += 1

print(f"Unknown repos ({len(unknowns)} sessions, {len(repo_names)} unique repos):")
for repo, cnt in repo_names.most_common(30):
    print(f"  {cnt:>2}x  {repo}")
