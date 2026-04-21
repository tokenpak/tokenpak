"""Platform connectors for remote data sources."""

from .base import Connector, ConnectorConfig

# Connector implementations (loaded conditionally)
CONNECTORS = {}

try:
    from .local import LocalConnector

    CONNECTORS["local"] = LocalConnector
except ImportError:
    pass

try:
    from .obsidian import ObsidianConnector

    CONNECTORS["obsidian"] = ObsidianConnector
except ImportError:
    pass

# Future connectors (Pro/Enterprise tier)
# - google_drive: Google Drive OAuth connector
# - notion: Notion API connector
# - github: GitHub repos/issues/PRs connector
# - onedrive: OneDrive/SharePoint connector
# - dropbox: Dropbox connector
# - confluence: Confluence connector
# - slack: Slack export connector


def get_connector(name: str, config: ConnectorConfig) -> Connector:
    """Get a connector by name."""
    connector_class = CONNECTORS.get(name)
    if not connector_class:
        raise ValueError(f"Unknown connector: {name}")
    return connector_class(config)


def list_connectors() -> list:
    """List available connectors."""
    return list(CONNECTORS.keys())
