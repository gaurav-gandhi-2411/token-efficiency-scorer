"""Clean-room gate script for 0.8.0: runs from the INSTALLED wheel, not the repo."""
from __future__ import annotations

import pathlib
import sys

# --- 1. Confirm we are loading from site-packages, not the repo ---
import tes
import tes.web.server as srv

repo = pathlib.Path(r"C:\Users\gaura\ml-projects\token-efficiency-scorer")
pkg_path = pathlib.Path(tes.__file__)
if repo in pkg_path.parents:
    print("FAIL: tes is loading from the REPO, not from the installed wheel.")
    print(f"  tes.__file__ = {pkg_path}")
    sys.exit(1)
print(f"[OK] tes loads from site-packages (NOT repo): {pkg_path}")
print(f"[OK] tes.__version__ = {tes.__version__}")

assert tes.__version__ == "0.8.0", f"Version mismatch: {tes.__version__}"
print("[OK] version == 0.8.0")

# --- 2. Route registration: /patterns and /ask in url_map ---
app = srv.create_app(srv.ServerConfig())
rules = {str(r) for r in app.url_map.iter_rules() if not str(r).startswith("/static")}
print(f"[OK] registered routes: {sorted(rules)}")
assert "/patterns" in rules, f"/patterns MISSING from installed wheel url_map. Got: {rules}"
assert "/ask" in rules, f"/ask MISSING from installed wheel url_map. Got: {rules}"
print("[OK] /patterns registered in url_map")
print("[OK] /ask registered in url_map")

# --- 3. /patterns GET 200 via test client (uses installed templates) ---
from unittest.mock import patch

mock_cache = {
    "valid": False,
    "reason": "not_enough_sessions",
    "n_sessions": 0,
    "n_content_sessions_needed": 30,
    "status": "Not enough sessions.",
    "domain_of_validity": "n/a",
}
with patch("tes.web.server.get_or_compute_intelligence", return_value=mock_cache), \
     patch("tes.web.server._check_ollama", return_value=False):
    with app.test_client() as c:
        resp = c.get("/patterns")
assert resp.status_code == 200, f"/patterns returned {resp.status_code}, expected 200"
html = resp.data.decode()
assert "Not enough sessions" in html or "not enough" in html.lower(), \
    "Floor message not rendered"
assert "ask-panel" in html, "Ask panel not rendered"
print("[OK] GET /patterns -> 200 from installed wheel (templates bundled correctly)")
print("[OK] Floor message rendered")
print("[OK] Ask panel present")

# --- 4. POST /ask empty -> 400 ---
import json
with app.test_client() as c:
    r = c.post("/ask", data=json.dumps({"question": ""}), content_type="application/json")
assert r.status_code == 400, f"POST /ask empty -> {r.status_code}, expected 400"
print("[OK] POST /ask empty -> 400")

# --- 5. GET /ask -> 405 (route registered, wrong method), NOT 404 ---
with app.test_client() as c:
    r = c.get("/ask")
assert r.status_code == 405, f"GET /ask -> {r.status_code}, expected 405 (not 404)"
print("[OK] GET /ask -> 405 (route registered, not 404)")

# --- 6. /ask POST with mocked local LLM -> grounded answer ---
with patch("tes.web.server.ask_local", return_value="Corpus has 0 content sessions."), \
     patch("tes.web.server.ask_api", return_value=None):
    with app.test_client() as c:
        r = c.post("/ask",
                   data=json.dumps({"question": "How many sessions do I have?"}),
                   content_type="application/json")
assert r.status_code == 200, f"/ask mocked local -> {r.status_code}"
data = r.get_json()
assert data.get("answer"), f"No answer in response: {data}"
assert data.get("source") == "local"
print(f"[OK] POST /ask -> answer from local: '{data['answer']}'")

# --- 7. tes --version ---
import subprocess
result = subprocess.run(
    [sys.executable, "-m", "tes", "--version"],
    capture_output=True, text=True,
)
ver_out = (result.stdout + result.stderr).strip()
assert "0.8.0" in ver_out, f"tes --version output does not contain 0.8.0: {ver_out!r}"
print(f"[OK] tes --version: {ver_out}")

# --- 8. tes patterns import (ML deps available) ---
from tes.intelligence.cache import get_or_compute_intelligence
from tes.intelligence.chat import ask_local as chat_ask_local, CHAT_SYSTEM_PROMPT
print("[OK] tes.intelligence imports OK (numpy/sklearn available)")
assert "I don't predict" in CHAT_SYSTEM_PROMPT, "Honesty guard missing from system prompt"
print("[OK] CHAT_SYSTEM_PROMPT contains 'I don't predict' guard")

print()
print("=" * 60)
print("CLEAN-ROOM GATE PASSED — 0.8.0 wheel is publish-ready")
print("=" * 60)
