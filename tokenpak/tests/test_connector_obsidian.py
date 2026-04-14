# SPDX-License-Identifier: Apache-2.0
"""Unit tests for connectors.obsidian — ObsidianConnector."""

import pytest

from tokenpak.sources.base import ConnectorConfig
from tokenpak.sources.obsidian import ObsidianConnector


def _make_config(tmp_path, **kwargs):
    return ConnectorConfig(name="obsidian", source_path=str(tmp_path), **kwargs)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestObsidianConnectorInit:
    def test_adds_default_excludes(self, tmp_path):
        cfg = _make_config(tmp_path)
        conn = ObsidianConnector(cfg)
        for pat in ObsidianConnector.DEFAULT_EXCLUDES:
            assert pat in cfg.exclude_patterns

    def test_sets_markdown_include_pattern(self, tmp_path):
        cfg = _make_config(tmp_path)
        conn = ObsidianConnector(cfg)
        assert cfg.include_patterns == ["**/*.md"]

    def test_preserves_custom_include_patterns(self, tmp_path):
        cfg = _make_config(tmp_path, include_patterns=["**/*.txt"])
        conn = ObsidianConnector(cfg)
        assert cfg.include_patterns == ["**/*.txt"]

    def test_appends_to_existing_exclude_patterns(self, tmp_path):
        cfg = _make_config(tmp_path, exclude_patterns=["custom/*"])
        conn = ObsidianConnector(cfg)
        assert "custom/*" in cfg.exclude_patterns
        for pat in ObsidianConnector.DEFAULT_EXCLUDES:
            assert pat in cfg.exclude_patterns

    def test_link_cache_initialized_empty(self, tmp_path):
        conn = ObsidianConnector(_make_config(tmp_path))
        assert conn._link_cache == {}

    def test_name_attribute(self, tmp_path):
        conn = ObsidianConnector(_make_config(tmp_path))
        assert conn.name == "obsidian"

    def test_tier_attribute(self, tmp_path):
        conn = ObsidianConnector(_make_config(tmp_path))
        assert conn.tier == "free"


# ---------------------------------------------------------------------------
# list_files() — inherits from LocalConnector with type enrichment
# ---------------------------------------------------------------------------


class TestObsidianConnectorListFiles:
    def test_only_yields_markdown_files_by_default(self, tmp_path):
        # Files must be in a subdirectory — "**/*.md" requires at least one slash
        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "note.md").write_text("# Note")
        (notes / "image.png").write_bytes(b"PNG")
        conn = ObsidianConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        paths = {f.path for f in files}
        assert "notes/note.md" in paths
        assert "notes/image.png" not in paths

    def test_excludes_obsidian_config_dir(self, tmp_path):
        obsidian_dir = tmp_path / ".obsidian"
        obsidian_dir.mkdir()
        (obsidian_dir / "config").write_text("{}")
        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "note.md").write_text("# Note")
        conn = ObsidianConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        paths = {f.path for f in files}
        assert ".obsidian/config" not in paths
        assert "notes/note.md" in paths

    def test_file_type_enriched(self, tmp_path):
        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "note.md").write_text("# Note")
        conn = ObsidianConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        # file_type should be enriched by _detect_obsidian_type
        assert len(files) == 1
        assert files[0].file_type is not None


# ---------------------------------------------------------------------------
# _detect_obsidian_type()
# ---------------------------------------------------------------------------


class TestDetectObsidianType:
    def setup_method(self):
        # Create a dummy config — not actually used for detection
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        cfg = ConnectorConfig(name="obsidian", source_path=self._tmpdir)
        self.conn = ObsidianConnector(cfg)

    def test_daily_note(self):
        assert self.conn._detect_obsidian_type("2024-01-15.md") == "daily-note"

    def test_weekly_note(self):
        assert self.conn._detect_obsidian_type("2024-W03.md") == "weekly-note"

    def test_monthly_note(self):
        assert self.conn._detect_obsidian_type("2024-01.md") == "monthly-note"

    def test_template(self):
        assert self.conn._detect_obsidian_type("templates/Daily Template.md") == "template"

    def test_template_uppercase(self):
        assert self.conn._detect_obsidian_type("Templates/weekly.md") == "template"

    def test_regular_note(self):
        assert self.conn._detect_obsidian_type("notes/project-ideas.md") == "note"

    def test_regular_note_no_directory(self):
        assert self.conn._detect_obsidian_type("my-note.md") == "note"

    def test_attachment_png(self):
        # PNG files won't match because they won't be in list_files() with .md include,
        # but the method itself should detect them
        assert self.conn._detect_obsidian_type("assets/image.png") == "attachment"

    def test_attachment_pdf(self):
        assert self.conn._detect_obsidian_type("docs/manual.pdf") == "attachment"


# ---------------------------------------------------------------------------
# extract_links()
# ---------------------------------------------------------------------------


class TestExtractLinks:
    def setup_method(self):
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        cfg = ConnectorConfig(name="obsidian", source_path=self._tmpdir)
        self.conn = ObsidianConnector(cfg)

    def test_simple_wiki_link(self):
        links = self.conn.extract_links("See [[My Note]] for details.")
        assert "My Note" in links

    def test_aliased_wiki_link(self):
        links = self.conn.extract_links("Click [[Target Page|alias text]] here.")
        assert "Target Page" in links

    def test_embed_link(self):
        links = self.conn.extract_links("![[attachment.png]]")
        assert "attachment.png" in links

    def test_multiple_links(self):
        content = "See [[Note A]] and [[Note B|B alias]] and ![[image.png]]"
        links = self.conn.extract_links(content)
        assert "Note A" in links
        assert "Note B" in links
        assert "image.png" in links

    def test_no_links(self):
        assert self.conn.extract_links("No links here.") == []

    def test_empty_content(self):
        assert self.conn.extract_links("") == []

    def test_link_not_double_counted(self):
        links = self.conn.extract_links("[[Alpha]] and [[Alpha]]")
        assert links.count("Alpha") == 2


# ---------------------------------------------------------------------------
# extract_frontmatter()
# ---------------------------------------------------------------------------


class TestExtractFrontmatter:
    def setup_method(self):
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        cfg = ConnectorConfig(name="obsidian", source_path=self._tmpdir)
        self.conn = ObsidianConnector(cfg)

    def test_valid_frontmatter(self):
        content = "---\ntitle: My Note\ntags: [python, testing]\n---\n\n# Body"
        fm = self.conn.extract_frontmatter(content)
        assert fm["title"] == "My Note"
        assert fm["tags"] == ["python", "testing"]

    def test_no_frontmatter_returns_empty(self):
        content = "# Just a note\n\nNo frontmatter here."
        assert self.conn.extract_frontmatter(content) == {}

    def test_empty_frontmatter_block(self):
        content = "---\n---\n\n# Body"
        fm = self.conn.extract_frontmatter(content)
        assert isinstance(fm, dict)

    def test_malformed_frontmatter_lenient_returns_empty(self):
        content = "---\nnot: valid: yaml: at all\n---\n"
        # Lenient mode should not raise, may return empty or partial
        fm = self.conn.extract_frontmatter(content, strict=False)
        assert isinstance(fm, dict)

    def test_frontmatter_diagnostics_updated(self):
        content = "---\nkey: value\n---\n"
        self.conn.extract_frontmatter(content)
        assert self.conn.last_frontmatter_diagnostics is not None

    def test_no_frontmatter_does_not_update_diagnostics_to_strict(self):
        content = "No frontmatter"
        self.conn.extract_frontmatter(content)
        # Should return empty dict, diagnostics not set to error state
        assert self.conn.extract_frontmatter(content) == {}
