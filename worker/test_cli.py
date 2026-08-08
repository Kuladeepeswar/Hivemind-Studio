import asyncio
import os
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

if __name__ == "__main__":
    asyncio.run(run_test())
