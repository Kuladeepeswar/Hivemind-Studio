import logging
import os
from typing import Optional, Tuple

from llm import LLM

logger = logging.getLogger(__name__)

# Llama-class free models write shorter, weaker HTML than Gemini did, and their max
# completion length varies by provider. Overridable so a bigger model can be given room.
BUILDER_MAX_TOKENS = int(os.environ.get("BUILDER_MAX_TOKENS", "16000"))

ARCHITECT_SYSTEM_PROMPT = """You are the Architect agent in a 3-agent team that builds tiny working web apps from a one-line prompt.
Everything must be achievable as a single self-contained HTML file with inline React. No backend calls, no external APIs.

Reply with a JSON object only, in exactly this shape:
{"title": string, "features": [3 to 5 short strings], "data_model": string, "ui_notes": string}

Keep the scope small. Three or four features that actually work beat eight that do not."""

BUILDER_SYSTEM_PROMPT = """You are the Builder agent. Given the Architect's plan (and any Reviewer issues), output ONE complete HTML document.

The document is rendered inside a locked-down sandboxed iframe, so you MUST use exactly these
four CDN tags and no others. Any other host is blocked by CSP and the app will render blank:
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/react@18/umd/react.development.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>

Then one inline <script type="text/babel"> that defines the app and mounts it with
ReactDOM.createRoot(document.getElementById('root')).render(<App />).
Destructure hooks from the global React object: const { useState, useMemo } = React;
Client-side state only. Realistic mock data. No network calls, no fetch, no localStorage.

Tailwind's CDN builds its CSS after load, so also put a small inline <style> in <head>
setting html/body background and text colour to match your palette. Without it the app
flashes as unstyled white text for a second.

Write ONE component called App plus at most two small helper components. Aim for roughly
200 to 300 lines total. A short app that works completely is the goal; a long one that
gets cut off mid-tag is a failure.

Output ONLY the raw HTML, starting with <!doctype html> and ending with </html>.
No explanation, no markdown code fences."""

REVIEWER_SYSTEM_PROMPT = """You are the Reviewer agent, a strict but fair QA engineer.
Approve unless something is visibly broken, unstyled, or missing a core feature from the plan.

Reply with a JSON object only, in exactly this shape:
{"approved": boolean, "issues": [0 to 4 short actionable strings]}"""


class AgentLoop:
    def __init__(self, publish_status_cb, publish_token_cb, publish_artifact_cb,
                 llm: Optional[LLM] = None):
        self.publish_status = publish_status_cb
        self.publish_token = publish_token_cb
        self.publish_artifact = publish_artifact_cb
        self.llm = llm or LLM()

    async def run(self, prompt: str, parent_html: Optional[str] = None) -> Tuple[str, dict]:
        # --- ARCHITECT ---
        await self.publish_status("architect", "thinking")

        user_msg = "PROMPT: " + prompt
        if parent_html:
            user_msg += "\n\nEXISTING HTML (this is a remix, plan a change to it):\n" + parent_html

        logger.info("Calling Architect...")
        plan = await self.llm.json(ARCHITECT_SYSTEM_PROMPT, user_msg, "architect")
        logger.info("Architect plan: %s", plan)
        await self.publish_status("architect", "done")

        # --- BUILDER (pass 1) ---
        await self.publish_status("builder", "thinking")
        builder_msg = "ARCHITECT PLAN:\n" + _plan_text(plan) + "\n\nOutput only raw HTML."
        if parent_html:
            builder_msg += "\n\nStart from this EXISTING HTML and modify it:\n" + parent_html

        html_out = await self._build_html(builder_msg)
        await self.publish_artifact(html_out)
        await self.publish_status("builder", "done")

        # --- REVIEWER ---
        await self.publish_status("reviewer", "thinking")
        logger.info("Calling Reviewer...")
        review = await self.llm.json(
            REVIEWER_SYSTEM_PROMPT,
            "ARCHITECT PLAN:\n" + _plan_text(plan) + "\n\nBUILDER HTML:\n" + html_out,
            "reviewer",
        )
        logger.info("Reviewer result: %s", review)

        if review.get("approved"):
            await self.publish_status("reviewer", "done")
            return html_out, plan

        # --- BUILDER (pass 2, one revision only) ---
        await self.publish_status("reviewer", "done")
        await self.publish_status("builder", "revising")

        issues = "\n".join(str(i) for i in review.get("issues", [])[:4])
        revision_msg = builder_msg + "\n\nFix these specific problems:\n" + issues

        logger.info("Running Builder revision...")
        html_out_2 = await self._build_html(revision_msg)
        await self.publish_artifact(html_out_2)
        await self.publish_status("builder", "done")

        return html_out_2, plan

    @staticmethod
    def _looks_like_html(html: str) -> bool:
        head = html[:200].lstrip().lower()
        return head.startswith("<!doctype html") and "</html>" in html[-2000:].lower()

    @staticmethod
    def _strip_fences(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```html"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return cleaned.strip()

    async def _build_html(self, message: str) -> str:
        """Stream the Builder, retrying once if the result is not a complete document."""
        async def on_delta(text):
            await self.publish_token("builder", text)

        raw = await self.llm.stream(BUILDER_SYSTEM_PROMPT, message, "builder",
                                    on_delta, max_tokens=BUILDER_MAX_TOKENS)
        html = self._strip_fences(raw)
        if self._looks_like_html(html):
            return html

        logger.warning("Builder returned malformed HTML (%d chars), retrying once.", len(html))
        retry_msg = message + (
            "\n\nYour previous answer was not a complete HTML document. Output ONLY a "
            "complete document starting with <!doctype html> and ending with </html>. "
            "Make it shorter so it fits in one response."
        )
        raw = await self.llm.stream(BUILDER_SYSTEM_PROMPT, retry_msg, "builder",
                                    on_delta, max_tokens=BUILDER_MAX_TOKENS)
        return self._strip_fences(raw)


def _plan_text(plan: dict) -> str:
    """Flatten the Architect's plan for the Builder and Reviewer prompts.

    Built by hand rather than json.dumps so a model that returns a slightly different
    shape still produces a readable prompt instead of a wall of escaped JSON.
    """
    features = plan.get("features") or []
    if isinstance(features, str):
        features = [features]
    lines = [
        "Title: " + str(plan.get("title", "App")),
        "Features:",
    ]
    lines += ["  - " + str(f) for f in features]
    lines.append("Data model: " + str(plan.get("data_model", "")))
    lines.append("UI notes: " + str(plan.get("ui_notes", "")))
    return "\n".join(lines)
