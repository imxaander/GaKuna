"""UI rendering routes."""
from flask import render_template, render_template_string
from . import ui_bp


@ui_bp.route('/')
def dashboard():
    """Render main dashboard."""
    # TODO: Load template from templates/dashboard.html
    # Return render_template('dashboard.html',
    #     nodes=node_snapshot(),
    #     cameras=camera_snapshot(),
    #     sound_tracks=SOUND_TRACKS,
    #     floor_map_nodes=FLOOR_MAP_NODES,
    # )
    return "Dashboard - Template not yet extracted"
