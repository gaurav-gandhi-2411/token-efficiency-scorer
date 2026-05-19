"""Find where anthropic client gets its key."""
from __future__ import annotations
import os, anthropic

print("Checking env vars:")
for var in ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"]:
    val = os.environ.get(var, "NOT_SET")
    if val != "NOT_SET":
        print(f"  {var}={val[:12]}...")
    else:
        print(f"  {var}=NOT_SET")

client = anthropic.Anthropic()
try:
    print("api_key:", repr(client.api_key)[:20] if client.api_key else "None")
except Exception as e:
    print("api_key error:", e)

# Try a real call
try:
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": "hi"}],
    )
    print("API call OK:", msg.content[0].text[:20])
except Exception as e:
    print("API call failed:", e)
