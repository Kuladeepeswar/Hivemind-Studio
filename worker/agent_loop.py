import os
import json
import logging
import asyncio
from typing import List, Optional, Tuple
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

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

BUILDER_SYSTEM_PROMPT = """You are the Builder agent. Given the Architect's plan (and any Reviewer issues), output ONE complete HTML document:
Tailwind via the CDN script tag, React + ReactDOM + Babel standalone via CDN script tags, one inline <script type="text/babel"> mounting the app to #root.
Client-side state only (useState/useReducer), realistic mock data, no network calls except https://picsum.photos for placeholder images if truly needed.
Make it visually polished with a real color palette and at least one working interaction.
Output ONLY the raw HTML — no explanation, no markdown code fences."""

REVIEWER_SYSTEM_PROMPT = """You are the Reviewer agent, a strict but fair QA engineer. 
Approve unless something is visibly broken, unstyled, or missing a core feature from the plan. Keep issues short and actionable."""

class AgentLoop:
    def __init__(self, publish_status_cb, publish_token_cb, publish_artifact_cb):
        self.publish_status = publish_status_cb
        self.publish_token = publish_token_cb
        self.publish_artifact = publish_artifact_cb
        self.client = genai.Client()

    async def run(self, prompt: str, parent_html: Optional[str] = None) -> Tuple[str, str]:
        # --- ARCHITECT ---
        await self.publish_status("architect", "thinking")
        
        user_msg = f"PROMPT: {prompt}"
        if parent_html:
            user_msg += f"\n\nEXISTING HTML (Remix context):\n{parent_html}"

        logger.info("Calling Architect...")
        # Run synchronous call in thread pool
        arch_response = await asyncio.to_thread(
            self.client.models.generate_content,
            model='gemini-1.5-pro',
            contents=user_msg,
            config=types.GenerateContentConfig(
                system_instruction=ARCHITECT_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=ArchitectOutput,
            )
        )
        arch_plan = arch_response.text
        logger.info(f"Architect Plan: {arch_plan}")
        await self.publish_status("architect", "done")

        # --- BUILDER (Pass 1) ---
        await self.publish_status("builder", "thinking")
        builder_msg = f"ARCHITECT PLAN:\n{arch_plan}\n\nOutput only raw HTML."
        if parent_html:
            builder_msg += f"\n\nMake sure to start from this EXISTING HTML:\n{parent_html}"
            
        html_out = await self._stream_builder(builder_msg)
        await self.publish_artifact(html_out)
        await self.publish_status("builder", "done")

        # --- REVIEWER ---
        await self.publish_status("reviewer", "thinking")
        reviewer_msg = f"ARCHITECT PLAN:\n{arch_plan}\n\nBUILDER HTML:\n{html_out}"
        logger.info("Calling Reviewer...")
        rev_response = await asyncio.to_thread(
            self.client.models.generate_content,
            model='gemini-1.5-pro',
            contents=reviewer_msg,
            config=types.GenerateContentConfig(
                system_instruction=REVIEWER_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=ReviewerOutput,
            )
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
        html_out_2 = await self._stream_builder(revision_msg)
        await self.publish_artifact(html_out_2)
        await self.publish_status("builder", "done")
        
        return html_out_2, arch_plan

    async def _stream_builder(self, message: str) -> str:
        # We'll use a thread to get the iterator, but to avoid blocking the event loop 
        # completely while iterating, we iterate in thread too, or just accept it's a generator.
        # Since the iterator might do network I/O, we can wrap the `next()` calls.
        def get_stream():
            return self.client.models.generate_content_stream(
                model='gemini-1.5-pro',
                contents=message,
                config=types.GenerateContentConfig(
                    system_instruction=BUILDER_SYSTEM_PROMPT,
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
