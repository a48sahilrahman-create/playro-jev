"""
Fast Zero-Dependency DOM Pruner for JEV System One Media Stream Resolver.
Reduces heavy HTML (100KB - 1MB) down to clean signal content (<3.5KB / <800 tokens)
in under 5ms, preserving essential download links, quality tags, and form actions.
"""
import re

def prune_media_dom(html: str, max_chars: int = 3500) -> str:
    """
    Surgically cleans and condenses raw HTML for instant, high-signal JEV classification.
    Strips noise (scripts, styles, SVG, comments, tracking pixels, ads) and preserves
    headings, resolution badges (4K, 1080p, 720p), download buttons, and forms.
    """
    if not html:
        return ""

    # Strict bounded input length cap (500KB) to prevent ReDoS on huge HTML payloads
    if len(html) > 500000:
        # Find clean tag boundary before 500KB to guarantee tag and comparison operator safety
        cut_idx = html[:500000].rfind('</')
        if cut_idx > 300000:
            end_tag = html[cut_idx:500000].find('>')
            if end_tag != -1:
                html = html[:cut_idx + end_tag + 1]
            else:
                html = html[:cut_idx]
        else:
            last_gt = html[:500000].rfind('>')
            if last_gt > 300000:
                html = html[:last_gt + 1]
            else:
                html = html[:500000]
        if "<!--" in html and "-->" not in html[html.rfind("<!--"):]:
            html += "-->"

    if len(html) <= max_chars and not any(tag in html for tag in ["<script", "<style", "<svg"]):
        return html

    # 1. Extract and preserve high-value token scripts & JSON hydration state before stripping noise
    def _strip_js_comments(body: str) -> str:
        # Robust JS tokenizer regex: preserves string literals, removes /*...*/ and // comments
        pattern = re.compile(
            r'("(?:\\.|[^"\\])*"|'      # double-quoted string
            r"'(?:\\.|[^'\\])*'|"      # single-quoted string
            r'`(?:\\.|[^`\\])*`)|'     # template literal
            r'/\*.*?\*/|'              # block comment
            r'(?<![:\w])//.*$',        # line comment (never inside strings or after protocol :)
            flags=re.DOTALL | re.MULTILINE
        )
        def _repl(m):
            return m.group(1) if m.group(1) else ''
        return pattern.sub(_repl, body).strip()

    preserved_scripts = []
    for sm in re.finditer(r'<script([^>]*)>(.*?)</script>', html, flags=re.DOTALL | re.I):
        if len(preserved_scripts) >= 8:
            break
        s_attrs = sm.group(1).lower()
        s_body = sm.group(2).strip()
        if "application/json" in s_attrs:
            s_lower = s_body.lower()
            if any(tok in s_lower for tok in ["url", "stream", "download", "link", "drive", "file"]):
                preserved_scripts.append(f"<script type=\"application/json\">{s_body[:1500]}</script>")
        else:
            cleaned_body = _strip_js_comments(s_body)
            c_lower = cleaned_body.lower()
            if any(tok in c_lower for tok in ["atob", "location", "token", "nonce", "sid=", "pass", "download", "link", "url"]):
                # Keep concise representation without comments
                preserved_scripts.append(f"<script>{cleaned_body[:1200]}</script>")

    # 2. Strip standard script, style, svg, noscript, iframe blocks
    cleaned = re.sub(r'<(script|style|svg|noscript|iframe)[^>]*>.*?</\1>', ' ', html, flags=re.DOTALL | re.I)

    # 3. Strip base64 image data and inline CSS background URLs
    cleaned = re.sub(r'data:image/[^;]+;base64,[a-zA-Z0-9+/=]{30,}', '[IMG]', cleaned)
    cleaned = re.sub(r'url\([^\)]+\)', ' ', cleaned)

    # 4. Strip HTML comments
    cleaned = re.sub(r'<!--.*?-->', ' ', cleaned, flags=re.DOTALL)

    # 5. Remove obvious ad and banner wrappers by class/id pattern
    cleaned = re.sub(r'<div[^>]*(?:class|id)=[\'"][^\'"]*(?:advert|banner|google-ad|ad-box|pop-under|sticky-ad)[^\'"]*[\'"][^>]*>.*?</div\s*>', ' ', cleaned, flags=re.DOTALL | re.I)

    # 6. Extract high-signal interactive and media elements:
    pattern = re.compile(
        r'<(?:a|button|form|input|h[1-6]|p|div)[^>]*>'
        r'(?:.*?)</(?:a|button|form|h[1-6]|p|div)>'
        r'|<input[^>]*>',
        re.I | re.DOTALL
    )

    matches = pattern.findall(cleaned)
    if preserved_scripts:
        matches = preserved_scripts + matches
    if matches:
        # Filter matches to retain elements with media signals or links
        filtered = []
        for m in matches:
            m_lower = m.lower()
            if any(sig in m_lower for sig in [
                "download", "drive", "seed", "cloud", "4k", "2160p", "1080p", "720p", "480p",
                "hevc", "x265", "x264", "hdr", "dual", "hindi", "episode", "season", "mkv", "mp4",
                "href=", "action=", "method=", "type=\"hidden\"", "atob"
            ]):
                filtered.append(m)
        condensed = "\n".join(filtered) if filtered else "\n".join(matches[:25])
    else:
        # Fallback strip all tags
        condensed = re.sub(r'<[^>]+>', ' ', cleaned)

    # 6. Normalize whitespace
    condensed = re.sub(r'[ \t\r\f\v]+', ' ', condensed)
    condensed = re.sub(r'\n\s*\n+', '\n', condensed).strip()

    return condensed[:max_chars]
