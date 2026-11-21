"""GaKuna Flask application factory."""
from flask import Flask
from flask_socketio import SocketIO

socketio = SocketIO()


def create_app(config=None):
    """Create and configure Flask app."""
    app = Flask(__name__)
    
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
