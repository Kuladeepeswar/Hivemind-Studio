"""Run the full agent loop with no database, queue or cache.

The fastest way to check that the LLM providers, the JSON parsing and the Builder
prompt still work after a model or provider changes underneath you.

    GROQ_API_KEY=... python test_cli.py
    GROQ_API_KEY=... python test_cli.py "a markdown notes app"
"""

import asyncio
import os
import sys

# Generated apps are full of emoji and typographic glyphs. On a Windows console
# (cp1252) printing them raises UnicodeEncodeError and kills the run, which looks
# like an agent failure but is not.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

from agent_loop import AgentLoop
from llm import LLM, NoProvidersConfigured


async def mock_publish_status(agent, status):
    print(f"\n[{agent.upper()}] {status}")


async def mock_publish_token(agent, delta):
    print(delta, end="", flush=True)


async def mock_publish_artifact(html):
    print(f"\n[ARTIFACT] {len(html)} bytes")


async def run_test():
    prompt = sys.argv[1] if len(sys.argv) > 1 else "A pomodoro timer with start, pause and reset"

    try:
        llm = LLM()
    except NoProvidersConfigured as exc:
        print(f"ERROR: {exc}")
        print("\nGet a free key from console.groq.com or cloud.cerebras.ai")
        return 1

    print(f"Providers: {', '.join(p.name + ' -> ' + p.model for p in llm.providers)}")
    print(f"Prompt:    {prompt}\n")

    loop = AgentLoop(mock_publish_status, mock_publish_token, mock_publish_artifact, llm=llm)
    html, plan = await loop.run(prompt)

    print("\n\n--- RESULT ---")
    print(f"Title:              {plan.get('title')}")
    print(f"HTML length:        {len(html)}")
    print(f"Starts with doctype: {html.lstrip().lower().startswith('<!doctype html')}")
    print(f"Closes </html>:      {'</html>' in html.lower()}")
    print(f"Mounts to #root:     {'root' in html}")

    out = os.path.join(os.path.dirname(__file__), "last_build.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nWrote {out} - open it in a browser to check it actually works.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run_test()))
