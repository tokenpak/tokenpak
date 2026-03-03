"""Text processor for markdown, plaintext, and HTML files."""

import re


class TextProcessor:
    """Compress text by preserving structure and aggressively reducing verbosity."""

    def __init__(self, aggressive: bool = True):
        self.aggressive = aggressive

    def process(self, content: str, path: str = "") -> str:
        """
        Compress text content while preserving meaning.
        
        Aggressive strategy (default):
        - Keep all headers (# ## ###)
        - Keep bullet points, truncate to 80 chars
        - Keep numbered list items, truncate to 80 chars
        - For paragraphs >80 chars, keep first sentence (max 100 chars)
        - Drop low-signal boilerplate lines
        - Preserve code fences (but not prose around them)
        - Remove excessive blank lines
        - Strip HTML tags for .html files
        """
        if path.endswith((".html", ".htm")):
            content = self._strip_html(content)

        lines = content.split("\n")
        result = []
        in_code_block = False
        in_frontmatter = False
        prev_blank = False
        kept_in_section = 0

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Handle YAML frontmatter
            if i == 0 and stripped == "---":
                in_frontmatter = True
                continue
            if in_frontmatter:
                if stripped == "---":
                    in_frontmatter = False
                continue

            # Handle fenced code blocks
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                result.append(line)
                prev_blank = False
                continue

            if in_code_block:
                result.append(line)
                prev_blank = False
                continue

            # Blank lines — collapse multiples
            if not stripped:
                if not prev_blank:
                    result.append("")
                prev_blank = True
                continue
            prev_blank = False

            # In aggressive mode, cap detail per section
            if self.aggressive and kept_in_section >= 5 and not stripped.startswith('#'):
                # still allow occasional bullet with strong signal
                if not (re.match(r"^[\s]*[-*+•]\s", line) and any(k in stripped.lower() for k in ["critical", "risk", "decision", "result", "metric", "cost", "%"])):
                    continue

            # Headers — always keep
            if stripped.startswith("#"):
                result.append(line)
                kept_in_section = 0
                continue

            # Bullet/numbered points — truncate aggressively
            if re.match(r"^[\s]*[-*+•]\s", line) or re.match(r"^[\s]*\d+\.\s", line):
                max_len = 80 if self.aggressive else 120
                if len(stripped) > max_len:
                    result.append(line[:max_len].rsplit(" ", 1)[0] + "…")
                else:
                    result.append(line)
                kept_in_section += 1
                continue

            # Blockquotes — keep as-is
            if stripped.startswith(">"):
                result.append(line)
                kept_in_section += 1
                continue

            # Drop low-signal boilerplate in aggressive mode
            if self.aggressive and self._is_boilerplate(stripped):
                continue

            # Regular paragraphs — aggressively keep first sentence
            para_limit = 80 if self.aggressive else 150
            if len(stripped) > para_limit:
                first_sentence = self._first_sentence(stripped, max_chars=100 if self.aggressive else 150)
                result.append(first_sentence)
            else:
                result.append(line)
            kept_in_section += 1

        return "\n".join(result).strip()

    def _first_sentence(self, text: str, max_chars: int = 150) -> str:
        """Extract the first sentence from text with max length."""
        match = re.search(r'[.!?](?:\s|$)', text)
        if match:
            sent = text[:match.end()].strip()
            if len(sent) > max_chars:
                return sent[:max_chars].rsplit(" ", 1)[0] + "…"
            return sent
        if len(text) > max_chars:
            return text[:max_chars].rsplit(" ", 1)[0] + "…"
        return text

    def _is_boilerplate(self, line: str) -> bool:
        low = line.lower()
        patterns = [
            "all rights reserved",
            "privacy policy",
            "terms of service",
            "click here",
            "subscribe",
            "follow us",
            "source:",
            "prepared by:",
        ]
        return any(p in low for p in patterns)

    def _strip_html(self, content: str) -> str:
        """Remove HTML tags, keeping text content."""
        # Remove script and style blocks
        content = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', content, flags=re.DOTALL | re.IGNORECASE)
        # Remove tags
        content = re.sub(r'<[^>]+>', ' ', content)
        # Collapse whitespace
        content = re.sub(r'\s+', ' ', content)
        return content.strip()
