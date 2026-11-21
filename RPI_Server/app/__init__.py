"""GaKuna Flask application factory."""
import os
from flask import Flask
from flask_socketio import SocketIO

socketio = SocketIO()


def create_app(config=None):
    """Create and configure Flask app."""
    # Get the app directory
    app_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(app_dir)
    
    # Create Flask app with templates folder pointing to parent/templates
    app = Flask(
        __name__,
        template_folder=os.path.join(parent_dir, 'templates'),
        static_folder=os.path.join(parent_dir, 'static')
    )
    
    # Load config
    if config is None:
        from .config import DevelopmentConfig
        config = DevelopmentConfig
    app.config.from_object(config)
    
    # Initialize extensions
    socketio.init_app(app, cors_allowed_origins="*")
    
    # Register blueprints
    from .routes import nodes_bp, cameras_bp, settings_bp, ui_bp
    app.register_blueprint(nodes_bp)
    app.register_blueprint(cameras_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(ui_bp)
    
    return app
