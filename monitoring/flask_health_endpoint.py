"""
Flask Health Endpoint for TokenPak Index Monitoring

Integrates the index health monitor with a Flask app to expose:
GET /health/index-status → IndexHealthStatus as JSON
"""

from flask import Blueprint, jsonify
from index_health import VaultIndexHealthMonitor, IndexHealthStatus


def create_health_blueprint(index_path=None, blocks_dir=None, prefix="/health"):
    """
    Create a Flask Blueprint for health checks.
    
    Args:
        index_path: Custom path to index.json (default: ~/.tokenpak/index.json)
        blocks_dir: Custom path to blocks directory (default: ~/.tokenpak/blocks)
        prefix: URL prefix for health endpoints (default: /health)
    
    Returns:
        Flask Blueprint with health endpoints
    """
    bp = Blueprint("health", __name__, url_prefix=prefix)
    monitor = VaultIndexHealthMonitor(index_path, blocks_dir)
    
    @bp.route("/index-status", methods=["GET"])
    def index_status():
        """
        Get comprehensive TokenPak vault index health status.
        
        Returns:
            JSON with status (ok|warn|error), age_seconds, issues[], timestamp
        """
        status = monitor.check_all()
        return jsonify(status.to_dict()), 200 if status.status == IndexHealthStatus.STATUS_OK else 503
    
    @bp.route("/index-freshness", methods=["GET"])
    def index_freshness():
        """
        Get index freshness check only.
        
        Returns:
            JSON with age_seconds and freshness_status
        """
        age_seconds, issue = monitor.check_index_freshness()
        return jsonify({
            "age_seconds": round(age_seconds, 2),
            "is_fresh": issue is None,
            "issue": issue,
        }), 200 if issue is None else 503
    
    @bp.route("/index-structure", methods=["GET"])
    def index_structure():
        """
        Get index structure validation result.
        
        Returns:
            JSON with is_valid and issues[]
        """
        is_valid, issues = monitor.validate_index_structure()
        return jsonify({
            "is_valid": is_valid,
            "issues": issues,
        }), 200 if is_valid else 503
    
    @bp.route("/blocks-verification", methods=["GET"])
    def blocks_verification():
        """
        Get block file verification result.
        
        Returns:
            JSON with missing_blocks[] and issues[]
        """
        missing, issues = monitor.verify_block_files_exist()
        return jsonify({
            "missing_blocks": missing,
            "issue_count": len(issues),
            "issues": issues,
        }), 200 if len(missing) == 0 else 503
    
    return bp


def attach_health_endpoints(app, index_path=None, blocks_dir=None, prefix="/health"):
    """
    Attach health endpoints to an existing Flask app.
    
    Args:
        app: Flask application instance
        index_path: Custom path to index.json
        blocks_dir: Custom path to blocks directory
        prefix: URL prefix for health endpoints
    
    Returns:
        The blueprint object (for testing/inspection)
    """
    bp = create_health_blueprint(index_path, blocks_dir, prefix)
    app.register_blueprint(bp)
    return bp


if __name__ == "__main__":
    # Example usage
    from flask import Flask
    
    app = Flask(__name__)
    attach_health_endpoints(app)
    
    # Start development server
    print("Health endpoints available at:")
    print("  GET http://localhost:5000/health/index-status")
    print("  GET http://localhost:5000/health/index-freshness")
    print("  GET http://localhost:5000/health/index-structure")
    print("  GET http://localhost:5000/health/blocks-verification")
    
    app.run(debug=True, port=5000)
