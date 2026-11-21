"""Node API routes."""
from flask import jsonify, request
from . import nodes_bp

# These will be populated from services
# Example implementations shown


@nodes_bp.route('', methods=['GET'])
def list_nodes():
    """Get all nodes with their current state."""
    # TODO: Import from services and return node state
    return jsonify([])


@nodes_bp.route('', methods=['POST'])
def add_node():
    """Add a new node to the registry."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    
    # TODO: Validate and add node via service
    return jsonify({"status": "created"}), 201


@nodes_bp.route('/<node_id>', methods=['GET'])
def get_node(node_id):
    """Get a specific node's state."""
    # TODO: Fetch from service
    return jsonify({}), 404


@nodes_bp.route('/<node_id>/command', methods=['POST'])
def send_node_command(node_id):
    """Send command to a node."""
    data = request.get_json(silent=True)
    # TODO: Implement command sending
    return jsonify({"status": "sent"})
