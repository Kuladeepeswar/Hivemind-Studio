import asyncio
import os
import sys

# Generated apps are full of emoji and typographic glyphs. On a Windows console
# (cp1252) printing them raises UnicodeEncodeError and kills the test run, which
# looks like an agent failure but isn't.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent_loop import AgentLoop

# Ensure GEMINI_API_KEY is set in environment for this to work
# e.g., $env:GEMINI_API_KEY="your-key" python test_cli.py

async def mock_publish_status(agent, status):
    print(f"[{agent.upper()}] Status: {status}")

async def mock_publish_token(agent, delta):
    # Print inline without newline for tokens
    print(delta, end="", flush=True)

async def mock_publish_artifact(html):
    print(f"\n[ARTIFACT] Produced HTML ({len(html)} bytes)")

async def run_test():
    prompt = "A simple pomodoro timer with start, pause, and reset buttons."
    print(f"Testing Prompt: {prompt}")
    
    loop = AgentLoop(mock_publish_status, mock_publish_token, mock_publish_artifact)
    html, plan = await loop.run(prompt)

    print("\n--- TEST COMPLETE ---")
    print(f"Final HTML Length: {len(html)}")
    print(f"Starts with doctype: {html.lstrip().lower().startswith('<!doctype html')}")
    print(f"Closes </html>: {'</html>' in html.lower()}")
    print(f"Mounts to #root: {'root' in html}")

    out = os.path.join(os.path.dirname(__file__), "last_build.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out} — open it in a browser to check it actually works.")

if __name__ == "__main__":
    asyncio.run(run_test())
