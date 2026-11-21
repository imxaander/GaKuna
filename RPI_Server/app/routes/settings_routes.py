"""Settings API routes."""
from flask import jsonify, request
from . import settings_bp


@settings_bp.route('/thresholds', methods=['GET'])
def get_thresholds():
    """Get alert thresholds."""
    # TODO: Fetch from database
    return jsonify({})


@settings_bp.route('/thresholds', methods=['POST'])
def update_thresholds():
    """Update alert thresholds."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    
    # TODO: Validate and update thresholds
    return jsonify(data)


@settings_bp.route('/evacuation-mode', methods=['GET'])
def get_evacuation_mode():
    """Get evacuation mode status."""
    return jsonify({"mode": "normal"})


@settings_bp.route('/evacuation-mode', methods=['POST'])
def set_evacuation_mode():
    """Set evacuation mode."""
    data = request.get_json(silent=True)
    # TODO: Update mode
    return jsonify({"mode": data.get("mode", "normal")})
