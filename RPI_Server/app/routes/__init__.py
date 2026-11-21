"""API routes blueprints."""
from flask import Blueprint

nodes_bp = Blueprint('nodes', __name__, url_prefix='/api/nodes')
cameras_bp = Blueprint('cameras', __name__, url_prefix='/api/cameras')
settings_bp = Blueprint('settings', __name__, url_prefix='/api/settings')
ui_bp = Blueprint('ui', __name__)

# Import route handlers to register them
from . import nodes_routes, cameras_routes, settings_routes, ui_routes

__all__ = ['nodes_bp', 'cameras_bp', 'settings_bp', 'ui_bp']
