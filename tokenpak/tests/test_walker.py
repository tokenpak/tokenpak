"""Unit tests for tokenpak.walker module."""

import os
import tempfile
from pathlib import Path
import pytest

from tokenpak.walker import (
    walk_directory,
    detect_file_type,
    FILE_TYPES,
    FILE_NAME_TYPES,
    SKIP_DIRS,
    MAX_FILE_SIZE,
)


class TestDetectFileType:
    """Tests for detect_file_type function."""

    def test_markdown_extension(self):
        """Markdown files should be detected as 'text'."""
        assert detect_file_type("/path/to/file.md") == "text"

    def test_python_extension(self):
        """Python files should be detected as 'code'."""
        assert detect_file_type("/path/to/script.py") == "code"

    def test_json_extension(self):
        """JSON files should be detected as 'data'."""
        assert detect_file_type("/path/to/config.json") == "data"

    def test_image_extensions(self):
        """Image files should be detected as 'image'."""
        assert detect_file_type("/path/to/photo.png") == "image"
        assert detect_file_type("/path/to/photo.jpg") == "image"
        assert detect_file_type("/path/to/photo.jpeg") == "image"
        assert detect_file_type("/path/to/photo.gif") == "image"
        assert detect_file_type("/path/to/photo.webp") == "image"
        assert detect_file_type("/path/to/photo.svg") == "image"

    def test_audio_extensions(self):
        """Audio files should be detected as 'audio'."""
        assert detect_file_type("/path/to/song.mp3") == "audio"
        assert detect_file_type("/path/to/song.wav") == "audio"
        assert detect_file_type("/path/to/song.m4a") == "audio"

    def test_video_extensions(self):
        """Video files should be detected as 'video'."""
        assert detect_file_type("/path/to/movie.mp4") == "video"
        assert detect_file_type("/path/to/movie.mkv") == "video"
        assert detect_file_type("/path/to/movie.avi") == "video"

    def test_pdf_extension(self):
        """PDF files should be detected as 'pdf'."""
        assert detect_file_type("/path/to/doc.pdf") == "pdf"

    def test_dotfile_env(self):
        """Dotfile .env should be detected as 'data' by basename."""
        assert detect_file_type("/path/to/.env") == "data"

    def test_yaml_and_yml_extensions(self):
        """YAML files should be detected as 'data'."""
        assert detect_file_type("/path/to/config.yaml") == "data"
        assert detect_file_type("/path/to/config.yml") == "data"

    def test_case_insensitivity(self):
        """File type detection should be case-insensitive."""
        assert detect_file_type("/path/to/file.MD") == "text"
        assert detect_file_type("/path/to/script.PY") == "code"
        assert detect_file_type("/path/to/photo.PNG") == "image"

    def test_unknown_extension(self):
        """Unknown file extensions should return None."""
        assert detect_file_type("/path/to/file.unknown") is None
        assert detect_file_type("/path/to/file.xyz123") is None

    def test_no_extension(self):
        """Files with no extension should return None."""
        assert detect_file_type("/path/to/README") is None
        assert detect_file_type("/path/to/Makefile") is None

    def test_multiple_dots_in_filename(self):
        """Files with multiple dots should use final extension."""
        assert detect_file_type("/path/to/archive.tar.gz") is None  # .gz not in FILE_TYPES
        assert detect_file_type("/path/to/build.min.js") == "code"


class TestWalkDirectory:
    """Tests for walk_directory function."""

    def test_empty_directory(self):
        """Walking an empty directory should return empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = walk_directory(tmpdir)
            assert result == []

    def test_single_file(self):
        """Walking a directory with single file should return that file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a markdown file
            test_file = Path(tmpdir) / "test.md"
            test_file.write_text("# Test")
            
            result = walk_directory(tmpdir)
            
            assert len(result) == 1
            path, file_type, size = result[0]
            assert path == str(test_file)
            assert file_type == "text"
            assert size > 0

    def test_multiple_files_different_types(self):
        """Walking with mixed file types should return all with correct types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "doc.md").write_text("# Doc")
            (tmppath / "code.py").write_text("print('hi')")
            (tmppath / "config.json").write_text("{}")
            
            result = walk_directory(tmpdir)
            
            assert len(result) == 3
            types = {Path(r[0]).name: r[1] for r in result}
            assert types["doc.md"] == "text"
            assert types["code.py"] == "code"
            assert types["config.json"] == "data"

    def test_subdirectories(self):
        """Walking with subdirectories should find files recursively."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            subdir = tmppath / "subdir"
            subdir.mkdir()
            (tmppath / "root.md").write_text("Root")
            (subdir / "nested.py").write_text("code")
            
            result = walk_directory(tmpdir)
            
            assert len(result) == 2
            filenames = [Path(r[0]).name for r in result]
            assert "root.md" in filenames
            assert "nested.py" in filenames

    def test_skip_node_modules(self):
        """Directory walker should skip 'node_modules' directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "keep.md").write_text("Keep")
            
            skip_dir = tmppath / "node_modules"
            skip_dir.mkdir()
            (skip_dir / "package.json").write_text("{}")
            
            result = walk_directory(tmpdir)
            
            # Should only find keep.md, not package.json in node_modules
            assert len(result) == 1
            assert Path(result[0][0]).name == "keep.md"

    def test_skip_git_directory(self):
        """Directory walker should skip '.git' directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "file.md").write_text("Content")
            
            git_dir = tmppath / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text("git config")
            
            result = walk_directory(tmpdir)
            
            assert len(result) == 1
            assert Path(result[0][0]).name == "file.md"

    def test_skip_pycache_directory(self):
        """Directory walker should skip '__pycache__' directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "module.py").write_text("code")
            
            cache_dir = tmppath / "__pycache__"
            cache_dir.mkdir()
            (cache_dir / "module.pyc").write_text("bytecode")
            
            result = walk_directory(tmpdir)
            
            assert len(result) == 1
            assert Path(result[0][0]).name == "module.py"

    def test_skip_venv_directory(self):
        """Directory walker should skip 'venv' directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "app.py").write_text("code")
            
            venv_dir = tmppath / "venv"
            venv_dir.mkdir()
            (venv_dir / "pyvenv.cfg").write_text("venv config")
            
            result = walk_directory(tmpdir)
            
            assert len(result) == 1
            assert Path(result[0][0]).name == "app.py"

    def test_skip_dotfiles_in_directories(self):
        """Directory walker should skip directories starting with '.'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "visible.md").write_text("Visible")
            
            hidden_dir = tmppath / ".hidden"
            hidden_dir.mkdir()
            (hidden_dir / "secret.txt").write_text("Secret")
            
            result = walk_directory(tmpdir)
            
            assert len(result) == 1
            assert Path(result[0][0]).name == "visible.md"

    def test_empty_files_ignored(self):
        """Empty files (size 0) should be ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "content.md").write_text("Has content")
            (tmppath / "empty.md").write_text("")
            
            result = walk_directory(tmpdir)
            
            assert len(result) == 1
            assert Path(result[0][0]).name == "content.md"

    def test_max_file_size_exceeded(self):
        """Files exceeding max_size should be ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "small.txt").write_text("x" * 1000)
            
            # Use a very small max_size for testing
            result = walk_directory(tmpdir, max_size=500)
            
            # small.txt is 1000 bytes, exceeds max_size of 500
            assert len(result) == 0

    def test_custom_max_size(self):
        """Custom max_size parameter should be respected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "file.txt").write_text("x" * 100)
            
            # Pass large max_size
            result = walk_directory(tmpdir, max_size=1000)
            assert len(result) == 1
            
            # Pass small max_size
            result = walk_directory(tmpdir, max_size=50)
            assert len(result) == 0

    def test_results_sorted_by_path(self):
        """Results should be sorted by file path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "z_file.md").write_text("Z")
            (tmppath / "a_file.md").write_text("A")
            (tmppath / "m_file.md").write_text("M")
            
            result = walk_directory(tmpdir)
            
            paths = [Path(r[0]).name for r in result]
            assert paths == ["a_file.md", "m_file.md", "z_file.md"]

    def test_size_in_results(self):
        """Result tuples should include correct file size."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            content = "Hello, world!"
            test_file = tmppath / "test.txt"
            test_file.write_text(content)
            
            result = walk_directory(tmpdir)
            
            assert len(result) == 1
            path, file_type, size = result[0]
            assert size == len(content)

    def test_absolute_path_normalization(self):
        """walk_directory should return absolute paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "file.md").write_text("Content")
            
            # Call with relative path
            result = walk_directory(tmpdir)
            
            assert len(result) == 1
            path = result[0][0]
            assert os.path.isabs(path)

    def test_multiple_skip_dirs_together(self):
        """Multiple skip directories should all be skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "keep.md").write_text("Keep")
            
            # Create multiple skip directories
            for skip_dir in ["node_modules", ".git", "__pycache__"]:
                (tmppath / skip_dir).mkdir()
                (tmppath / skip_dir / "ignore.txt").write_text("Ignore")
            
            result = walk_directory(tmpdir)
            
            # Should only find keep.md
            assert len(result) == 1
            assert Path(result[0][0]).name == "keep.md"

    def test_deeply_nested_structure(self):
        """Should correctly handle deeply nested directory structures."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            deep = tmppath / "a" / "b" / "c" / "d" / "e"
            deep.mkdir(parents=True)
            (deep / "deep.txt").write_text("Deep content")
            
            result = walk_directory(tmpdir)
            
            assert len(result) == 1
            assert Path(result[0][0]).name == "deep.txt"


class TestFileTypeConstants:
    """Tests for FILE_TYPES and related constants."""

    def test_file_types_not_empty(self):
        """FILE_TYPES constant should not be empty."""
        assert len(FILE_TYPES) > 0

    def test_file_types_valid_values(self):
        """FILE_TYPES values should be valid type strings."""
        valid_types = {"text", "code", "data", "pdf", "image", "audio", "video"}
        for ext, ftype in FILE_TYPES.items():
            assert ftype in valid_types

    def test_skip_dirs_not_empty(self):
        """SKIP_DIRS should contain common directories."""
        assert "node_modules" in SKIP_DIRS
        assert ".git" in SKIP_DIRS
        assert "__pycache__" in SKIP_DIRS

    def test_max_file_size_is_positive(self):
        """MAX_FILE_SIZE should be a positive integer."""
        assert MAX_FILE_SIZE > 0
        assert isinstance(MAX_FILE_SIZE, int)
