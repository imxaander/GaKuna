"""Camera API routes."""
from flask import jsonify, request
from . import cameras_bp


@cameras_bp.route('', methods=['GET'])
def list_cameras():
    """Get all cameras with their current state."""
    # TODO: Import from services and return camera state
    return jsonify([])


@cameras_bp.route('', methods=['POST'])
def add_camera():
    """Add a new camera to the registry."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    
    # TODO: Validate and add camera via service
    return jsonify({"status": "created"}), 201


@cameras_bp.route('/<cam_id>/feed', methods=['GET'])
def camera_feed(cam_id):
    """Get camera feed stream."""
    # TODO: Return video stream
    return jsonify({"error": "Not found"}), 404


@cameras_bp.route('/<cam_id>/snapshot', methods=['GET'])
def camera_snapshot(cam_id):
    """Get camera snapshot."""
    # TODO: Return image
    return jsonify({"error": "Not found"}), 404


@cameras_bp.route('/<cam_id>/enable', methods=['POST'])
def enable_camera(cam_id):
    """Enable a camera."""
    # TODO: Enable via service
    return jsonify({"status": "enabled"})


@cameras_bp.route('/<cam_id>/disable', methods=['POST'])
def disable_camera(cam_id):
    """Disable a camera."""
    # TODO: Disable via service
    return jsonify({"status": "disabled"})
