"""Application entry point."""
import sys
from pathlib import Path

# Add the RPI_Server directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import create_app, socketio
from app.config import DevelopmentConfig
from app.comms.tcp_server import TCPServer


def main():
    """Start the GaKuna server."""
    # Create Flask app
    app = create_app(DevelopmentConfig)
    
    # Initialize TCP server for device communication
    tcp_server = TCPServer(
        host=app.config.get('TCP_HOST', '0.0.0.0'),
        port=app.config.get('TCP_PORT', 5001)
    )
    tcp_server.start()
    
    # Start Flask-SocketIO server
    socketio.run(
        app,
        host='0.0.0.0',
        port=5000,
        debug=app.config.get('DEBUG', False),
        allow_unsafe_werkzeug=True
    )


if __name__ == '__main__':
    main()

