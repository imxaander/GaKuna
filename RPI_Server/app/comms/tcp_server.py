"""TCP server for receiving device data."""
import struct
import socket
import threading
from typing import Callable, Optional


class TCPServer:
    """Receives TCP connections from ESP32 nodes."""
    
    def __init__(self, host='0.0.0.0', port=5001):
        self.host = host
        self.port = port
        self.socket = None
        self.running = False
        self.thread = None
        self.on_data_received: Optional[Callable] = None
    
    def start(self):
        """Start the TCP server."""
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop the TCP server."""
        self.running = False
        if self.socket:
            self.socket.close()
    
    def _run(self):
        """Main server loop."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(5)
            
            while self.running:
                try:
                    client_sock, addr = self.socket.accept()
                    self._handle_client(client_sock, addr)
                except Exception as e:
                    if self.running:
                        print(f"Error accepting connection: {e}")
        except Exception as e:
            print(f"TCP Server error: {e}")
    
    def _handle_client(self, client_sock, addr):
        """Handle a single client connection."""
        try:
            while self.running:
                data = client_sock.recv(1024)
                if not data:
                    break
                
                if self.on_data_received:
                    self.on_data_received(addr[0], data)
        except Exception as e:
            print(f"Error handling client {addr}: {e}")
        finally:
            client_sock.close()
    
    def send_command(self, host, port, command_bytes):
        """Send command to a device."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((host, port))
            sock.send(command_bytes)
            sock.close()
            return True
        except Exception as e:
            print(f"Error sending command to {host}:{port}: {e}")
            return False
