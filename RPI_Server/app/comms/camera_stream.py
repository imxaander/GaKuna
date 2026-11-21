"""Camera stream management."""
import threading
from typing import Optional, Callable


class CameraStreamWorker:
    """Manages streaming from a single camera."""
    
    def __init__(self, cam_id: str, ip: str, port: int = 80):
        self.cam_id = cam_id
        self.ip = ip
        self.port = port
        self.running = False
        self.thread = None
        self.on_frame: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
    
    def start(self):
        """Start streaming."""
        self.running = True
        self.thread = threading.Thread(target=self._stream_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop streaming."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
    
    def _stream_loop(self):
        """Main streaming loop."""
        # TODO: Implement camera streaming logic
        # Connect to camera, receive frames, process
        pass


class CameraStreamManager:
    """Manages all active camera streams."""
    
    def __init__(self):
        self.streams = {}
    
    def add_stream(self, cam_id: str, ip: str, port: int = 80) -> CameraStreamWorker:
        """Add and start a camera stream."""
        if cam_id not in self.streams:
            worker = CameraStreamWorker(cam_id, ip, port)
            self.streams[cam_id] = worker
            worker.start()
        return self.streams[cam_id]
    
    def remove_stream(self, cam_id: str):
        """Remove and stop a camera stream."""
        if cam_id in self.streams:
            self.streams[cam_id].stop()
            del self.streams[cam_id]
    
    def get_stream(self, cam_id: str) -> Optional[CameraStreamWorker]:
        """Get a stream by camera ID."""
        return self.streams.get(cam_id)
    
    def stop_all(self):
        """Stop all streams."""
        for worker in self.streams.values():
            worker.stop()
        self.streams.clear()
