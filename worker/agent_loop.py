import os
import json
import logging
import asyncio
from typing import List, Optional, Tuple
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# gemini-1.5-* is fully retired and returns 404. Override via env if you want
# to trade cost for quality (e.g. a preview Pro model).
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
# Used only after the primary model has exhausted its retries, so a capacity spike
# on one model doesn't take the whole demo down.
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")

# 429 = rate limited, 5xx = upstream capacity. All transient and worth retrying;
# 400/403/404 (bad key, bad model id) are not and must surface immediately.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_retryable(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int) and code in RETRYABLE_STATUS:
        return True
    text = str(exc).upper()
    return any(m in text for m in ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL", "OVERLOADED"))

class ArchitectOutput(BaseModel):
    title: str = Field(description="The title of the app")
    features: List[str] = Field(description="3 to 5 core features of the app")
    data_model: str = Field(description="Description of the client-side state/data model")
    ui_notes: str = Field(description="Notes on layout, color palette, and styling")

class ReviewerOutput(BaseModel):
    approved: bool = Field(description="Whether the app is approved")
    issues: List[str] = Field(description="0 to 4 short, actionable issues if not approved", default_factory=list)

ARCHITECT_SYSTEM_PROMPT = """You are the Architect agent in a 3-agent team that builds tiny working web apps from a one-line prompt.
Everything must be achievable as a single self-contained HTML file with inline React — no backend calls, no external APIs.
"""

BUILDER_SYSTEM_PROMPT = """You are the Builder agent. Given the Architect's plan (and any Reviewer issues), output ONE complete HTML document.

The document is rendered inside a locked-down sandboxed iframe, so you MUST use exactly these
four CDN tags and no others — any other host is blocked by CSP and the app will render blank:
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/react@18/umd/react.development.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>

Then one inline <script type="text/babel"> that defines the app and mounts it with
ReactDOM.createRoot(document.getElementById('root')).render(<App />).
Client-side state only (useState/useReducer) — destructure hooks from the global React object.
Realistic mock data, no network calls, no localStorage, no fetch. Images only from https://picsum.photos.
Make it visually polished with a real color palette and at least one working interaction.

Keep the whole document under ~500 lines so it never gets truncated.
Output ONLY the raw HTML starting with <!doctype html> — no explanation, no markdown code fences."""

REVIEWER_SYSTEM_PROMPT = """You are the Reviewer agent, a strict but fair QA engineer. 
Approve unless something is visibly broken, unstyled, or missing a core feature from the plan. Keep issues short and actionable."""

class AgentLoop:
    def __init__(self, publish_status_cb, publish_token_cb, publish_artifact_cb):
        self.publish_status = publish_status_cb
        self.publish_token = publish_token_cb
        self.publish_artifact = publish_artifact_cb
        self.client = genai.Client()

    async def _with_retry(self, make_call, label: str, attempts: int = 4):
        """Run a Gemini call, retrying transient upstream failures.

        `make_call` takes a model id so the last attempt can fall back to a
        different model when the primary one is saturated.
        """
        delay = 2.0
        for attempt in range(1, attempts + 1):
            model = GEMINI_MODEL if attempt < attempts else GEMINI_FALLBACK_MODEL
            try:
                return await make_call(model)
            except Exception as exc:
                if not _is_retryable(exc) or attempt == attempts:
                    raise
                logger.warning(
                    "%s failed on %s (attempt %d/%d): %s — retrying in %.0fs",
                    label, model, attempt, attempts, exc, delay,
                )
                # Keep the UI honest instead of looking frozen mid-build.
                await self.publish_status(label, "retrying")
                await asyncio.sleep(delay)
                delay *= 2

    async def run(self, prompt: str, parent_html: Optional[str] = None) -> Tuple[str, str]:
        # --- ARCHITECT ---
        await self.publish_status("architect", "thinking")
        
        user_msg = f"PROMPT: {prompt}"
        if parent_html:
            user_msg += f"\n\nEXISTING HTML (Remix context):\n{parent_html}"

        logger.info("Calling Architect...")
        # Run synchronous call in thread pool
        arch_response = await self._with_retry(
            lambda model: asyncio.to_thread(
                self.client.models.generate_content,
                model=model,
                contents=user_msg,
                config=types.GenerateContentConfig(
                    system_instruction=ARCHITECT_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=ArchitectOutput,
                )
            ),
            "architect",
        )
        arch_plan = arch_response.text
        logger.info(f"Architect Plan: {arch_plan}")
        await self.publish_status("architect", "done")

        # --- BUILDER (Pass 1) ---
        await self.publish_status("builder", "thinking")
        builder_msg = f"ARCHITECT PLAN:\n{arch_plan}\n\nOutput only raw HTML."
        if parent_html:
            builder_msg += f"\n\nMake sure to start from this EXISTING HTML:\n{parent_html}"
            
        html_out = await self._build_html(builder_msg)
        await self.publish_artifact(html_out)
        await self.publish_status("builder", "done")

        # --- REVIEWER ---
        await self.publish_status("reviewer", "thinking")
        reviewer_msg = f"ARCHITECT PLAN:\n{arch_plan}\n\nBUILDER HTML:\n{html_out}"
        logger.info("Calling Reviewer...")
        rev_response = await self._with_retry(
            lambda model: asyncio.to_thread(
                self.client.models.generate_content,
                model=model,
                contents=reviewer_msg,
                config=types.GenerateContentConfig(
                    system_instruction=REVIEWER_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=ReviewerOutput,
                )
            ),
            "reviewer",
        )
        rev_result_str = rev_response.text
        logger.info(f"Reviewer Result: {rev_result_str}")
        rev_result = json.loads(rev_result_str)
        
        if rev_result.get("approved"):
            await self.publish_status("reviewer", "done")
            return html_out, arch_plan
            
        # --- BUILDER (Pass 2) ---
        await self.publish_status("reviewer", "revising")
        await self.publish_status("builder", "thinking")
        
        issues = "\n".join(rev_result.get("issues", []))
        revision_msg = builder_msg + f"\n\nREVIEWER ISSUES to fix:\n{issues}"
        
        logger.info("Running Builder Revision...")
        html_out_2 = await self._build_html(revision_msg)
        await self.publish_artifact(html_out_2)
        await self.publish_status("builder", "done")
        
        return html_out_2, arch_plan

    async def _stream_builder(self, message: str) -> str:
        # A 503 can land on the initial call OR partway through the iterator, so the
        # entire stream is retried as one unit — resuming would splice two different
        # generations into one broken document.
        return await self._with_retry(
            lambda model: self._stream_builder_once(message, model), "builder"
        )

    async def _stream_builder_once(self, message: str, model: str) -> str:
        # We'll use a thread to get the iterator, but to avoid blocking the event loop 
        # completely while iterating, we iterate in thread too, or just accept it's a generator.
        # Since the iterator might do network I/O, we can wrap the `next()` calls.
        def get_stream():
            return self.client.models.generate_content_stream(
                model=model,
                contents=message,
                config=types.GenerateContentConfig(
                    system_instruction=BUILDER_SYSTEM_PROMPT,
                    # A full polished HTML app blows past the default output cap and
                    # comes back silently truncated, which renders as a broken preview.
                    max_output_tokens=32768,
                )
            )
            
        response = await asyncio.to_thread(get_stream)
        
        full_text = ""
        batch = ""
        
        # It's an iterator, we need to consume it without blocking
        def get_next(resp):
            try:
                return next(resp)
            except StopIteration:
                return None
                
        while True:
            chunk = await asyncio.to_thread(get_next, response)
            if chunk is None:
                break
                
            if chunk.text:
                full_text += chunk.text
                batch += chunk.text
                if len(batch) >= 40:
                    await self.publish_token("builder", batch)
                    batch = ""
                    
        if batch:
            await self.publish_token("builder", batch)
            
        clean_html = full_text.strip()
        if clean_html.startswith("```html"):
            clean_html = clean_html[7:]
        if clean_html.startswith("```"):
            clean_html = clean_html[3:]
        if clean_html.endswith("```"):
            clean_html = clean_html[:-3]

        return clean_html.strip()

    @staticmethod
    def _looks_like_html(html: str) -> bool:
        head = html[:200].lstrip().lower()
        return head.startswith("<!doctype html") and "</html>" in html[-2000:].lower()

    async def _build_html(self, message: str) -> str:
        """Stream the Builder, and retry once if the model returned something that
        isn't a complete HTML document (truncated output, stray prose, etc)."""
        html = await self._stream_builder(message)
        if self._looks_like_html(html):
            return html

        logger.warning("Builder returned malformed HTML (%d chars), retrying once.", len(html))
        retry_msg = (
            message
            + "\n\nYour previous answer was not a complete HTML document. Output ONLY a "
              "complete document starting with <!doctype html> and ending with </html>. "
              "Keep it shorter so it fits in one response."
        )
        return await self._stream_builder(retry_msg)
