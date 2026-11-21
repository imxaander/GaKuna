"""Database operations."""
import sqlite3
from typing import List, Dict, Optional
from contextlib import contextmanager


class Database:
    """SQLite database wrapper."""
    
    def __init__(self, path):
        self.path = path
        self._init_db()
    
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def _init_db(self):
        """Initialize database tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Nodes table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    ip TEXT,
                    port INTEGER,
                    floor INTEGER,
                    area TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Cameras table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS cameras (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    node_id TEXT,
                    ip TEXT,
                    port INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(node_id) REFERENCES nodes(id)
                )
            ''')
            
            # Settings table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
    
    # Node operations
    def add_node(self, node_id: str, name: str, ip: str, port: int, floor: int, area: str):
        """Add a node to the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO nodes (id, name, ip, port, floor, area)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (node_id, name, ip, port, floor, area))
            conn.commit()
    
    def get_node(self, node_id: str) -> Optional[Dict]:
        """Get node by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM nodes WHERE id = ?', (node_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_all_nodes(self) -> List[Dict]:
        """Get all nodes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM nodes')
            return [dict(row) for row in cursor.fetchall()]
    
    def delete_node(self, node_id: str):
        """Delete a node."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM nodes WHERE id = ?', (node_id,))
            conn.commit()
    
    # Camera operations
    def add_camera(self, cam_id: str, name: str, node_id: str, ip: str, port: int):
        """Add a camera to the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO cameras (id, name, node_id, ip, port)
                VALUES (?, ?, ?, ?, ?)
            ''', (cam_id, name, node_id, ip, port))
            conn.commit()
    
    def get_camera(self, cam_id: str) -> Optional[Dict]:
        """Get camera by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM cameras WHERE id = ?', (cam_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_all_cameras(self) -> List[Dict]:
        """Get all cameras."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM cameras')
            return [dict(row) for row in cursor.fetchall()]
    
    def delete_camera(self, cam_id: str):
        """Delete a camera."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM cameras WHERE id = ?', (cam_id,))
            conn.commit()
    
    # Settings operations
    def set_setting(self, key: str, value: str):
        """Set a configuration setting."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO settings (key, value)
                VALUES (?, ?)
            ''', (key, value))
            conn.commit()
    
    def get_setting(self, key: str, default: str = '') -> str:
        """Get a configuration setting."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
            row = cursor.fetchone()
            return row[0] if row else default
    
    def get_all_settings(self) -> Dict[str, str]:
        """Get all settings."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT key, value FROM settings')
            return {row[0]: row[1] for row in cursor.fetchall()}
