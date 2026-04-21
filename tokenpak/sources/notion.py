"""Notion connector (Pro tier)."""

from datetime import datetime
from typing import Iterator, Optional

from .base import Connector, ConnectorConfig, RemoteFile


class NotionConnector(Connector):
    """
    Connector for Notion workspaces.

    Pro tier — requires:
    - Notion integration token
    - Workspace access permissions

    Features:
    - Page and database sync
    - Block-level content extraction
    - Property/metadata extraction
    - Incremental sync using last_edited_time
    """

    name = "notion"
    tier = "pro"

    NOTION_API_BASE = "https://api.notion.com/v1"

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self._headers = None

    def connect(self) -> bool:
        """
        Establish connection using integration token.

        config.auth_token should be the Notion integration token.
        """
        if not self.config.auth_token:
            print("Notion connector requires auth_token (integration token)")
            return False

        self._headers = {  # type: ignore[assignment]
            "Authorization": f"Bearer {self.config.auth_token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

        # TODO: Implement connection test when adding Pro tier
        # try:
        #     response = requests.get(
        #         f"{self.NOTION_API_BASE}/users/me",
        #         headers=self._headers
        #     )
        #     return response.status_code == 200
        # except Exception as e:
        #     print(f"Notion connection failed: {e}")
        #     return False
        raise NotImplementedError("Notion connector requires Pro tier")

    def list_files(self, since: Optional[str] = None) -> Iterator[RemoteFile]:
        """
        Search all pages and databases in the workspace.

        Uses search API with last_edited_time filter for incremental sync.
        """
        # TODO: Implement when adding Pro tier
        # filter_params = {}
        # if since:
        #     filter_params["filter"] = {
        #         "property": "last_edited_time",
        #         "timestamp": "last_edited_time",
        #         "after": since
        #     }
        #
        # has_more = True
        # start_cursor = None
        #
        # while has_more:
        #     response = requests.post(
        #         f"{self.NOTION_API_BASE}/search",
        #         headers=self._headers,
        #         json={
        #             "start_cursor": start_cursor,
        #             "page_size": 100,
        #             **filter_params
        #         }
        #     ).json()
        #
        #     for result in response.get("results", []):
        #         yield self._to_remote_file(result)
        #
        #     has_more = response.get("has_more", False)
        #     start_cursor = response.get("next_cursor")
        raise NotImplementedError("Notion connector requires Pro tier")

    def get_content(self, file: RemoteFile) -> bytes:
        """
        Retrieve page content by fetching all blocks.

        Recursively fetches nested blocks and converts to markdown.
        """
        # TODO: Implement when adding Pro tier
        # blocks = []
        # has_more = True
        # start_cursor = None
        #
        # while has_more:
        #     response = requests.get(
        #         f"{self.NOTION_API_BASE}/blocks/{file.source_id}/children",
        #         headers=self._headers,
        #         params={"start_cursor": start_cursor, "page_size": 100}
        #     ).json()
        #
        #     blocks.extend(response.get("results", []))
        #     has_more = response.get("has_more", False)
        #     start_cursor = response.get("next_cursor")
        #
        # markdown = self._blocks_to_markdown(blocks)
        # return markdown.encode("utf-8")
        raise NotImplementedError("Notion connector requires Pro tier")

    def _to_remote_file(self, notion_obj: dict) -> RemoteFile:
        """Convert Notion API object to RemoteFile."""
        obj_type = notion_obj.get("object", "page")
        props = notion_obj.get("properties", {})

        # Extract title
        title = "Untitled"
        if "title" in props:
            title_prop = props["title"]
            if isinstance(title_prop, list) and title_prop:
                title = title_prop[0].get("plain_text", "Untitled")
            elif isinstance(title_prop, dict) and "title" in title_prop:
                title_arr = title_prop["title"]
                if title_arr:
                    title = title_arr[0].get("plain_text", "Untitled")
        elif "Name" in props:
            name_prop = props["Name"]
            if "title" in name_prop:
                title_arr = name_prop["title"]
                if title_arr:
                    title = title_arr[0].get("plain_text", "Untitled")

        return RemoteFile(
            path=f"{title}.md",
            source_id=notion_obj.get("id"),  # type: ignore
            size_bytes=0,  # Notion doesn't provide size
            modified_at=notion_obj.get("last_edited_time", datetime.now().isoformat()),
            file_type=obj_type,
        )

    def _blocks_to_markdown(self, blocks: list) -> str:
        """Convert Notion blocks to markdown."""
        # TODO: Implement block-to-markdown conversion
        # This would handle: paragraph, heading_1/2/3, bulleted_list_item,
        # numbered_list_item, code, quote, callout, image, etc.
        lines = []
        for block in blocks:
            block_type = block.get("type", "paragraph")
            content = block.get(block_type, {})

            if "rich_text" in content:
                text = "".join(rt.get("plain_text", "") for rt in content["rich_text"])

                if block_type.startswith("heading_"):
                    level = int(block_type[-1])
                    lines.append("#" * level + " " + text)
                elif block_type == "bulleted_list_item":
                    lines.append("- " + text)
                elif block_type == "numbered_list_item":
                    lines.append("1. " + text)
                elif block_type == "code":
                    lang = content.get("language", "")
                    lines.append(f"```{lang}\n{text}\n```")
                elif block_type == "quote":
                    lines.append("> " + text)
                else:
                    lines.append(text)

        return "\n\n".join(lines)
