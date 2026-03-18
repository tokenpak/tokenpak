"""Google Drive connector (Pro tier)."""

from datetime import datetime
from typing import Iterator, Optional

from .base import Connector, ConnectorConfig, RemoteFile


class GoogleDriveConnector(Connector):
    """
    Connector for Google Drive.

    Pro tier — requires:
    - OAuth2 credentials (client_id, client_secret)
    - User authorization flow

    Features:
    - Full Drive or specific folder sync
    - Google Docs/Sheets/Slides export to text
    - Shared drive support
    - Incremental sync using Drive API changes
    """

    name = "google_drive"
    tier = "pro"

    # Supported Google Workspace MIME types and their export formats
    EXPORT_FORMATS = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self._service = None
        self._start_page_token = None

    def connect(self) -> bool:
        """
        Establish connection using OAuth2.

        Requires config.auth_token to contain either:
        - Serialized credentials JSON
        - Path to credentials file
        """
        try:
            # TODO: Implement when adding Pro tier
            # from google.oauth2.credentials import Credentials
            # from googleapiclient.discovery import build
            #
            # creds = Credentials.from_authorized_user_info(
            #     json.loads(self.config.auth_token)
            # )
            # self._service = build('drive', 'v3', credentials=creds)
            # return True
            raise NotImplementedError("Google Drive connector requires Pro tier")
        except Exception as e:
            print(f"Google Drive connection failed: {e}")
            return False

    def list_files(self, since: Optional[str] = None) -> Iterator[RemoteFile]:
        """
        List files using Drive API.

        Uses changes API for incremental sync when `since` is provided
        as a page token.
        """
        if not self._service:
            raise RuntimeError("Not connected")

        # TODO: Implement when adding Pro tier
        # if since:
        #     # Incremental sync using changes API
        #     changes = self._service.changes().list(
        #         pageToken=since,
        #         fields="changes(fileId,file(name,mimeType,size,modifiedTime))"
        #     ).execute()
        #     for change in changes.get('changes', []):
        #         file = change.get('file')
        #         if file:
        #             yield self._to_remote_file(file)
        # else:
        #     # Full sync
        #     page_token = None
        #     while True:
        #         response = self._service.files().list(
        #             pageSize=100,
        #             pageToken=page_token,
        #             fields="nextPageToken,files(id,name,mimeType,size,modifiedTime)"
        #         ).execute()
        #         for file in response.get('files', []):
        #             yield self._to_remote_file(file)
        #         page_token = response.get('nextPageToken')
        #         if not page_token:
        #             break
        raise NotImplementedError("Google Drive connector requires Pro tier")

    def get_content(self, file: RemoteFile) -> bytes:
        """
        Download file content.

        For Google Workspace files, exports to text format.
        For binary files, downloads directly.
        """
        if not self._service:
            raise RuntimeError("Not connected")

        # TODO: Implement when adding Pro tier
        # mime_type = file.file_type
        # if mime_type in self.EXPORT_FORMATS:
        #     # Export Google Workspace file
        #     export_mime = self.EXPORT_FORMATS[mime_type]
        #     request = self._service.files().export_media(
        #         fileId=file.source_id,
        #         mimeType=export_mime
        #     )
        # else:
        #     # Download binary file
        #     request = self._service.files().get_media(fileId=file.source_id)
        #
        # return request.execute()
        raise NotImplementedError("Google Drive connector requires Pro tier")

    def _to_remote_file(self, drive_file: dict) -> RemoteFile:
        """Convert Drive API file to RemoteFile."""
        return RemoteFile(
            path=drive_file.get("name", "unknown"),
            source_id=drive_file.get("id"),  # type: ignore
            size_bytes=int(drive_file.get("size", 0)),
            modified_at=drive_file.get("modifiedTime", datetime.now().isoformat()),
            file_type=drive_file.get("mimeType"),
        )
