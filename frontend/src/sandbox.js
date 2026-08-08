// Generated apps run in an iframe with sandbox="allow-scripts" and no
// allow-same-origin, plus the CSP below. Hackathon-grade isolation, not a
// production guarantee.
//
// 'unsafe-eval' is required because Babel standalone compiles the inline
// <script type="text/babel"> block at runtime. The allowed hosts must stay in
// sync with the CDN list pinned in the Builder system prompt (worker/agent_loop.py).
const CSP_META =
  '<meta http-equiv="Content-Security-Policy" content="' +
  "default-src 'none'; " +
  "script-src 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net; " +
  "style-src 'unsafe-inline' https://cdn.tailwindcss.com https://fonts.googleapis.com; " +
  "font-src https: data:; " +
  "img-src https: data:;" +
  '">';

/**
 * Inject the CSP meta tag into a generated document.
 *
 * It has to land inside <head>, after the doctype — prepending it would push the
 * doctype out of first position and drop the page into quirks mode.
 */
export function withCsp(html) {
  if (!html) return html;

  const headOpen = html.match(/<head[^>]*>/i);
  if (headOpen) {
    const at = headOpen.index + headOpen[0].length;
    return html.slice(0, at) + CSP_META + html.slice(at);
  }

  // No <head> — put it right after the <html> tag, or fall back to the top.
  const htmlOpen = html.match(/<html[^>]*>/i);
  if (htmlOpen) {
    const at = htmlOpen.index + htmlOpen[0].length;
    return html.slice(0, at) + '<head>' + CSP_META + '</head>' + html.slice(at);
  }

  return CSP_META + html;
}
