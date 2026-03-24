"""
Tests for Flask Health Endpoints

Verifies:
- Health endpoint routes return correct status codes
- JSON response format matches specification
- All four health endpoints work correctly
"""

import json
import pytest
import tempfile
import time
from pathlib import Path
from datetime import datetime

from flask import Flask
from flask_health_endpoint import attach_health_endpoints, create_health_blueprint


@pytest.fixture
def temp_index_dir():
    """Create temporary index/blocks directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        index_dir = tmpdir_path / ".tokenpak"
        blocks_dir = index_dir / "blocks"
        index_dir.mkdir(parents=True)
        blocks_dir.mkdir(parents=True)
        
        yield {
            "root": tmpdir_path,
            "index_dir": index_dir,
            "blocks_dir": blocks_dir,
            "index_path": index_dir / "index.json",
        }


@pytest.fixture
def app(temp_index_dir):
    """Create Flask test app with health endpoints."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    
    attach_health_endpoints(
        app,
        index_path=temp_index_dir["index_path"],
        blocks_dir=temp_index_dir["blocks_dir"],
    )
    
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def valid_index_data():
    """Sample valid index data."""
    return {
        "version": "1.0",
        "meta": {
            "source_dir": "/home/user/vault",
            "indexed_at": datetime.utcnow().isoformat(),
            "stats": {"scanned": 100, "indexed": 95},
        },
        "blocks": {
            "readme.md": {"block_id": "readme.md", "raw_tokens": 100},
            "config.yaml": {"block_id": "config.yaml", "raw_tokens": 50},
        }
    }


class TestIndexStatusEndpoint:
    """Test GET /health/index-status"""
    
    def test_missing_index_returns_503(self, client):
        """Missing index should return 503 (Service Unavailable)."""
        response = client.get("/health/index-status")
        
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["status"] == "error"
        assert "issues" in data
    
    def test_healthy_index_returns_200(self, client, temp_index_dir, valid_index_data):
        """Healthy index should return 200 OK."""
        # Write valid index
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        # Create block files
        for block_id in valid_index_data["blocks"]:
            block_file = temp_index_dir["blocks_dir"] / f"{block_id}.txt"
            block_file.write_text("content")
        
        response = client.get("/health/index-status")
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert len(data["issues"]) == 0
    
    def test_response_format(self, client, temp_index_dir, valid_index_data):
        """Response should have correct JSON format."""
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        for block_id in valid_index_data["blocks"]:
            block_file = temp_index_dir["blocks_dir"] / f"{block_id}.txt"
            block_file.write_text("content")
        
        response = client.get("/health/index-status")
        data = json.loads(response.data)
        
        # Check required fields
        assert "status" in data
        assert "age_seconds" in data
        assert "issues" in data
        assert "timestamp" in data
        
        # Check types
        assert isinstance(data["status"], str)
        assert isinstance(data["age_seconds"], (int, float))
        assert isinstance(data["issues"], list)
        assert isinstance(data["timestamp"], str)
    
    def test_stale_index_returns_503(self, client, temp_index_dir, valid_index_data):
        """Stale index should return 503."""
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        # Create block files
        for block_id in valid_index_data["blocks"]:
            block_file = temp_index_dir["blocks_dir"] / f"{block_id}.txt"
            block_file.write_text("content")
        
        # Make index stale
        old_time = time.time() - 400
        import os
        os.utime(temp_index_dir["index_path"], (old_time, old_time))
        
        response = client.get("/health/index-status")
        
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["status"] == "warn"
        assert any("stale" in issue.lower() for issue in data["issues"])


class TestFreshnessEndpoint:
    """Test GET /health/index-freshness"""
    
    def test_fresh_index_returns_200(self, client, temp_index_dir, valid_index_data):
        """Fresh index should return 200."""
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        response = client.get("/health/index-freshness")
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["is_fresh"] is True
        assert "age_seconds" in data
        assert data["issue"] is None
    
    def test_stale_index_returns_503(self, client, temp_index_dir, valid_index_data):
        """Stale index should return 503."""
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        old_time = time.time() - 400
        import os
        os.utime(temp_index_dir["index_path"], (old_time, old_time))
        
        response = client.get("/health/index-freshness")
        
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["is_fresh"] is False
        assert data["issue"] is not None
    
    def test_missing_index_returns_503(self, client):
        """Missing index should return 503."""
        response = client.get("/health/index-freshness")
        
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["is_fresh"] is False


class TestStructureEndpoint:
    """Test GET /health/index-structure"""
    
    def test_valid_structure_returns_200(self, client, temp_index_dir, valid_index_data):
        """Valid structure should return 200."""
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        response = client.get("/health/index-structure")
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["is_valid"] is True
        assert len(data["issues"]) == 0
    
    def test_invalid_structure_returns_503(self, client, temp_index_dir):
        """Invalid structure should return 503."""
        # Write corrupted JSON
        with open(temp_index_dir["index_path"], "w") as f:
            f.write("{invalid")
        
        response = client.get("/health/index-structure")
        
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["is_valid"] is False
        assert len(data["issues"]) > 0
    
    def test_missing_required_keys_returns_503(self, client, temp_index_dir):
        """Missing required keys should return 503."""
        data = {"version": "1.0", "blocks": {}}  # missing 'meta'
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(data, f)
        
        response = client.get("/health/index-structure")
        
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["is_valid"] is False


class TestBlocksVerificationEndpoint:
    """Test GET /health/blocks-verification"""
    
    def test_all_blocks_present_returns_200(self, client, temp_index_dir, valid_index_data):
        """All blocks present should return 200."""
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        # Create block files
        for block_id in valid_index_data["blocks"]:
            block_file = temp_index_dir["blocks_dir"] / f"{block_id}.txt"
            block_file.write_text("content")
        
        response = client.get("/health/blocks-verification")
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data["missing_blocks"]) == 0
        assert data["issue_count"] == 0
    
    def test_missing_blocks_returns_503(self, client, temp_index_dir, valid_index_data):
        """Missing blocks should return 503."""
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        # Don't create block files
        
        response = client.get("/health/blocks-verification")
        
        assert response.status_code == 503
        data = json.loads(response.data)
        assert len(data["missing_blocks"]) > 0
    
    def test_partial_missing_blocks_returns_503(self, client, temp_index_dir, valid_index_data):
        """Partial missing blocks should return 503."""
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        # Create only first block file
        first_block_id = list(valid_index_data["blocks"].keys())[0]
        block_file = temp_index_dir["blocks_dir"] / f"{first_block_id}.txt"
        block_file.write_text("content")
        
        response = client.get("/health/blocks-verification")
        
        assert response.status_code == 503
        data = json.loads(response.data)
        assert len(data["missing_blocks"]) == 1


class TestCustomPrefixes:
    """Test custom endpoint prefixes."""
    
    def test_custom_prefix(self, temp_index_dir, valid_index_data):
        """Should support custom URL prefix."""
        app = Flask(__name__)
        app.config["TESTING"] = True
        
        attach_health_endpoints(
            app,
            index_path=temp_index_dir["index_path"],
            blocks_dir=temp_index_dir["blocks_dir"],
            prefix="/api/health",
        )
        
        client = app.test_client()
        
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        for block_id in valid_index_data["blocks"]:
            block_file = temp_index_dir["blocks_dir"] / f"{block_id}.txt"
            block_file.write_text("content")
        
        # Endpoints should be at custom prefix
        response = client.get("/api/health/index-status")
        assert response.status_code == 200
        
        # Old prefix should not work
        response = client.get("/health/index-status")
        assert response.status_code == 404


class TestBlueprintCreation:
    """Test standalone blueprint creation."""
    
    def test_create_blueprint(self, temp_index_dir, valid_index_data):
        """Blueprint should be creatable standalone."""
        bp = create_health_blueprint(
            index_path=temp_index_dir["index_path"],
            blocks_dir=temp_index_dir["blocks_dir"],
        )
        
        assert bp is not None
        assert bp.name == "health"
        assert bp.url_prefix == "/health"
    
    def test_blueprint_registration(self, temp_index_dir, valid_index_data):
        """Blueprint should work when registered to app."""
        app = Flask(__name__)
        app.config["TESTING"] = True
        
        bp = create_health_blueprint(
            index_path=temp_index_dir["index_path"],
            blocks_dir=temp_index_dir["blocks_dir"],
            prefix="/custom",
        )
        
        app.register_blueprint(bp)
        client = app.test_client()
        
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        response = client.get("/custom/index-status")
        assert response.status_code in [200, 503]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
