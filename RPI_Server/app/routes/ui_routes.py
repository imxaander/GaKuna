"""UI rendering routes."""
from flask import render_template
from . import ui_bp
from ..config import (
    SOUND_TRACKS,
    FLOOR_MAP_NODES,
    ALERT_THRESHOLDS,
)


@ui_bp.route('/')
def dashboard():
    """Render main dashboard."""
    # TODO: Load node and camera data from services
    context = {
        'nodes': [],
        'cameras': [],
        'sound_tracks': SOUND_TRACKS,
        'floor_map_nodes': FLOOR_MAP_NODES,
        'alert_thresholds': ALERT_THRESHOLDS,
    }
    
    return render_template('dashboard.html', **context)
