"""GitHub connector."""

from typing import Iterator, Optional

from .base import Connector, ConnectorConfig, RemoteFile


class GitHubConnector(Connector):
    """
    Connector for GitHub repositories.

    Requires:
    - Personal access token (PAT) or GitHub App
    - Repository access permissions

    Features:
    - Repository file sync
    - Issue/PR content extraction
    - Code file processing with language detection
    - Incremental sync using commit SHAs
    """

    name = "github"
    tier = "pro"

    GITHUB_API_BASE = "https://api.github.com"

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self._headers = None
        self._owner = None
        self._repo = None

    def connect(self) -> bool:
        """Fail closed because the GitHub connector is available in Pro."""
        print("Pro tier required")
        return False

    def list_files(self, since: Optional[str] = None) -> Iterator[RemoteFile]:
        """
        List repository files using Git tree API.

        Uses commit SHA for incremental sync.
        """
        # TODO: Implement
        # Get default branch
        # repo = requests.get(
        #     f"{self.GITHUB_API_BASE}/repos/{self._owner}/{self._repo}",
        #     headers=self._headers
        # ).json()
        # default_branch = repo.get("default_branch", "main")
        #
        # Get tree recursively
        # tree = requests.get(
        #     f"{self.GITHUB_API_BASE}/repos/{self._owner}/{self._repo}/git/trees/{default_branch}",
        #     headers=self._headers,
        #     params={"recursive": "1"}
        # ).json()
        #
        # for item in tree.get("tree", []):
        #     if item.get("type") == "blob":
        #         yield RemoteFile(
        #             path=item["path"],
        #             source_id=item["sha"],
        #             size_bytes=item.get("size", 0),
        #             modified_at=datetime.now().isoformat(),  # Tree doesn't have timestamps
        #             file_type=self._detect_language(item["path"]),
        #         )
        raise NotImplementedError("GitHub connector not yet implemented")

    def get_content(self, file: RemoteFile) -> bytes:
        """Download file content from GitHub."""
        # TODO: Implement
        # response = requests.get(
        #     f"{self.GITHUB_API_BASE}/repos/{self._owner}/{self._repo}/git/blobs/{file.source_id}",
        #     headers=self._headers
        # ).json()
        #
        # content = response.get("content", "")
        # encoding = response.get("encoding", "base64")
        #
        # if encoding == "base64":
        #     return base64.b64decode(content)
        # return content.encode("utf-8")
        raise NotImplementedError("GitHub connector not yet implemented")

    def list_issues(self, state: str = "all") -> Iterator[RemoteFile]:
        """List issues as virtual files."""
        # TODO: Implement
        # page = 1
        # while True:
        #     response = requests.get(
        #         f"{self.GITHUB_API_BASE}/repos/{self._owner}/{self._repo}/issues",
        #         headers=self._headers,
        #         params={"state": state, "page": page, "per_page": 100}
        #     ).json()
        #
        #     if not response:
        #         break
        #
        #     for issue in response:
        #         yield RemoteFile(
        #             path=f"issues/{issue['number']}-{self._slugify(issue['title'])}.md",
        #             source_id=str(issue["number"]),
        #             size_bytes=len(issue.get("body", "")),
        #             modified_at=issue.get("updated_at", datetime.now().isoformat()),
        #             file_type="issue",
        #         )
        #
        #     page += 1
        raise NotImplementedError("GitHub connector not yet implemented")

    def _detect_language(self, path: str) -> str:
        """Detect programming language from file extension."""
        ext_to_lang = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".jsx": "javascript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
            ".rb": "ruby",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".md": "markdown",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
        }
        for ext, lang in ext_to_lang.items():
            if path.endswith(ext):
                return lang
        return "unknown"

    def _slugify(self, text: str) -> str:
        """Convert text to URL-safe slug."""
        import re

        text = text.lower()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[\s_]+", "-", text)
        return text[:50]
