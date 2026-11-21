"""Device command sending."""
import struct
from typing import Dict, Any


class DeviceCommand:
    """Represents a command to send to a device."""
    
    # Command types
    CMD_PING = 0x01
    CMD_SET_AUDIO = 0x02
    CMD_SET_VOLUME = 0x03
    CMD_QUERY_STATE = 0x04
    CMD_REBOOT = 0x05
    
    def __init__(self, cmd_type: int, node_id: int, payload: bytes = b''):
        self.cmd_type = cmd_type
        self.node_id = node_id
        self.payload = payload
    
    def to_bytes(self) -> bytes:
        """Serialize command to bytes."""
        # TODO: Implement serialization based on protocol
        # Header: magic (2), cmd_type (1), node_id (1), payload_len (2)
        return b''


class DeviceCommandSender:
    """Sends commands to devices."""
    
    def __init__(self, tcp_server):
        self.tcp_server = tcp_server
    
    def ping_node(self, node_id: int, host: str, port: int) -> bool:
        """Send ping to node."""
        cmd = DeviceCommand(DeviceCommand.CMD_PING, node_id)
        return self.tcp_server.send_command(host, port, cmd.to_bytes())
    
    def set_audio_track(self, node_id: int, host: str, port: int, track_id: int) -> bool:
        """Send audio track command."""
        payload = struct.pack('<H', track_id)
        cmd = DeviceCommand(DeviceCommand.CMD_SET_AUDIO, node_id, payload)
        return self.tcp_server.send_command(host, port, cmd.to_bytes())
    
    def set_volume(self, node_id: int, host: str, port: int, volume: int) -> bool:
        """Send volume control command."""
        payload = struct.pack('<B', min(100, max(0, volume)))
        cmd = DeviceCommand(DeviceCommand.CMD_SET_VOLUME, node_id, payload)
        return self.tcp_server.send_command(host, port, cmd.to_bytes())
    
    def reboot_node(self, node_id: int, host: str, port: int) -> bool:
        """Send reboot command."""
        cmd = DeviceCommand(DeviceCommand.CMD_REBOOT, node_id)
        return self.tcp_server.send_command(host, port, cmd.to_bytes())
