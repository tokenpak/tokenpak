"""Text processor for markdown, plaintext, and HTML files."""

import re


class TextProcessor:
    """Compress text by preserving structure and trimming verbose content."""

    def process(self, content: str, path: str = "") -> str:
        """
        Compress text content while preserving meaning.
        
        Strategy:
        - Keep all headers (# ## ###)
        - Keep bullet points, truncate to 120 chars
        - For paragraphs >150 chars, keep first sentence only
        - Preserve code blocks (indented or fenced)
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

            # Headers — always keep
            if stripped.startswith("#"):
                result.append(line)
                continue

            # Bullet points — truncate to 120 chars
            if re.match(r"^[\s]*[-*+•]\s", line) or re.match(r"^[\s]*\d+\.\s", line):
                if len(stripped) > 120:
                    result.append(line[:120].rsplit(" ", 1)[0] + "…")
                else:
                    result.append(line)
                continue

            # Blockquotes — keep as-is
            if stripped.startswith(">"):
                result.append(line)
                continue

            # Regular paragraphs >150 chars — keep first sentence
            if len(stripped) > 150:
                first_sentence = self._first_sentence(stripped)
                result.append(first_sentence)
            else:
                result.append(line)

        return "\n".join(result).strip()

    def _first_sentence(self, text: str) -> str:
        """Extract the first sentence from text."""
        # Match sentence-ending punctuation followed by space or end
        match = re.search(r'[.!?](?:\s|$)', text)
        if match and match.end() < len(text):
            return text[:match.end()].strip()
        # No sentence boundary found, truncate at 150 chars
        if len(text) > 150:
            return text[:150].rsplit(" ", 1)[0] + "…"
        return text

    def _strip_html(self, content: str) -> str:
        """Remove HTML tags, keeping text content."""
        # Remove script and style blocks
        content = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', content, flags=re.DOTALL | re.IGNORECASE)
        # Remove tags
        content = re.sub(r'<[^>]+>', ' ', content)
        # Collapse whitespace
        content = re.sub(r'\s+', ' ', content)
        return content.strip()
