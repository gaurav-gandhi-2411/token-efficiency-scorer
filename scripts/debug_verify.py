"""Quick verification that traces have content."""
from __future__ import annotations
import json
import pathlib
import random

random.seed(42)
files = list(pathlib.Path("data/validation-corpus/traces_normalized").glob("*.json"))
random.shuffle(files)

with_content = 0
for f in files[:20]:
    s = json.loads(f.read_text(encoding="utf-8"))
    ct = sum(1 for t in s["turns"] if len(t["content_text"]) > 20)
    if ct > 0:
        with_content += 1

print(f"20 sessions checked, {with_content} have substantive content turns.")

for f in files[:5]:
    s = json.loads(f.read_text(encoding="utf-8"))
    for t in s["turns"]:
        if len(t["content_text"]) > 60:
            print(f'  [{s["scaffold"]}] t{t["turn_index"]} {t["role"]}: {t["content_text"][:100]}')
            break
