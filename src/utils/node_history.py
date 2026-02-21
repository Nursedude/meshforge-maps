"""
MeshForge Maps - Node History Database

SQLite-backed storage for node position observations over time.
Enables trajectory visualization, historical playback, and growth statistics.

Modeled after meshforge core's utils/node_history.py pattern:
  - Throttled recording (1 observation per node per configurable interval)
  - Trajectory export as GeoJSON LineString
  - Point-in-time snapshots
  - WAL mode for concurrent read/write
  - Automatic old-data pruning

Storage location: ~/.local/share/meshforge/maps_node_history.db
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .paths import get_data_dir

logger = logging.getLogger(__name__)

# Default DB location
DEFAULT_DB_PATH = get_data_dir() / "maps_node_history.db"

# Minimum interval between observations for the same node (seconds)
DEFAULT_THROTTLE_SECONDS = 60

# Default retention period (seconds) - 30 days
DEFAULT_RETENTION_SECONDS = 30 * 24 * 3600

# Maximum observations per node for trajectory queries
MAX_TRAJECTORY_POINTS = 1000


class NodeHistoryDB:
    """SQLite-backed node position history with trajectory GeoJSON export.

    Thread-safe: all operations acquire a lock around DB access.
    Uses WAL mode for better concurrent read performance.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        throttle_seconds: int = DEFAULT_THROTTLE_SECONDS,
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
    ):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._throttle_seconds = throttle_seconds
        self._retention_seconds = retention_seconds
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        # In-memory throttle tracker: node_id -> last_recorded_timestamp
        self._last_recorded: Dict[str, float] = {}
        self._init_db()

    def _init_db(self) -> None:
        """Create database and tables if they don't exist."""
        conn = None
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    altitude REAL,
                    network TEXT,
                    snr REAL,
                    battery INTEGER,
                    name TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_obs_node_time
                ON observations (node_id, timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_obs_time
                ON observations (timestamp)
            """)
            conn.commit()
            self._conn = conn
            logger.info("Node history DB initialized at %s", self._db_path)
        except Exception as e:
            logger.error("Failed to initialize node history DB: %s", e)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            self._conn = None

    def execute_read(self, query: str, params: tuple = ()) -> list:
        """Execute a read-only query under the DB lock. Returns list of rows."""
        with self._lock:
            if not self._conn:
                return []
            try:
                return self._conn.execute(query, params).fetchall()
            except sqlite3.OperationalError:
                return []

    def record_observation(
        self,
        node_id: str,
        lat: float,
        lon: float,
        altitude: Optional[float] = None,
        network: str = "",
        snr: Optional[float] = None,
        battery: Optional[int] = None,
        name: str = "",
        timestamp: Optional[int] = None,
    ) -> bool:
        """Record a node position observation if not throttled.

        Returns True if the observation was recorded, False if throttled
        or an error occurred.
        """
        if not self._conn:
            return False

        now = timestamp if timestamp is not None else int(time.time())

        with self._lock:
            # Throttle check inside lock to prevent duplicate observations
            # from concurrent calls passing the check simultaneously
            last = self._last_recorded.get(node_id, 0)
            if (now - last) < self._throttle_seconds:
                return False

            try:
                self._conn.execute(
                    """INSERT INTO observations
                       (node_id, timestamp, latitude, longitude, altitude,
                        network, snr, battery, name)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (node_id, now, lat, lon, altitude, network, snr, battery, name),
                )
                self._conn.commit()
                self._last_recorded[node_id] = now
                return True
            except Exception as e:
                logger.debug("Failed to record observation for %s: %s", node_id, e)
                return False

    def get_trajectory_geojson(
        self,
        node_id: str,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: int = MAX_TRAJECTORY_POINTS,
    ) -> Dict[str, Any]:
        """Get node trajectory as a GeoJSON Feature with LineString geometry.

        Returns a GeoJSON Feature with:
          - geometry: LineString of [lon, lat, alt] coordinates
          - properties: node_id, point_count, time_span, timestamps

        If the node has only one observation, returns a Point geometry instead.
        If no observations found, returns an empty FeatureCollection.
        """
        if not self._conn:
            return {"type": "FeatureCollection", "features": []}

        query = "SELECT timestamp, latitude, longitude, altitude FROM observations WHERE node_id = ?"
        params: List[Any] = [node_id]

        if since is not None:
            query += " AND timestamp >= ?"
            params.append(since)
        if until is not None:
            query += " AND timestamp <= ?"
            params.append(until)

        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        with self._lock:
            try:
                rows = self._conn.execute(query, params).fetchall()
            except Exception as e:
                logger.error("Trajectory query failed for %s: %s", node_id, e)
                return {"type": "FeatureCollection", "features": []}

        if not rows:
            return {"type": "FeatureCollection", "features": []}

        coordinates = []
        timestamps = []
        for ts, lat, lon, alt in rows:
            coord = [lon, lat]
            if alt is not None:
                coord.append(alt)
            coordinates.append(coord)
            timestamps.append(ts)

        if len(coordinates) == 1:
            geometry = {"type": "Point", "coordinates": coordinates[0]}
        else:
            geometry = {"type": "LineString", "coordinates": coordinates}

        feature = {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "node_id": node_id,
                "point_count": len(coordinates),
                "first_seen": timestamps[0] if timestamps else None,
                "last_seen": timestamps[-1] if timestamps else None,
                "time_span_seconds": (
                    timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0
                ),
            },
        }
        return {"type": "FeatureCollection", "features": [feature]}

    def get_node_history(
        self,
        node_id: str,
        since: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get observation history for a node as a list of dicts."""
        if not self._conn:
            return []

        query = """SELECT timestamp, latitude, longitude, altitude, network,
                          snr, battery, name
                   FROM observations WHERE node_id = ?"""
        params: List[Any] = [node_id]

        if since is not None:
            query += " AND timestamp >= ?"
            params.append(since)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            try:
                rows = self._conn.execute(query, params).fetchall()
            except Exception as e:
                logger.error("History query failed for %s: %s", node_id, e)
                return []

        return [
            {
                "timestamp": r[0],
                "latitude": r[1],
                "longitude": r[2],
                "altitude": r[3],
                "network": r[4],
                "snr": r[5],
                "battery": r[6],
                "name": r[7],
            }
            for r in rows
        ]

    def get_snapshot(
        self,
        timestamp: int,
    ) -> Dict[str, Any]:
        """Get the most recent observation for each node at or before a timestamp.

        Returns a GeoJSON FeatureCollection of Point features representing
        the network state at the given point in time.
        """
        if not self._conn:
            return {"type": "FeatureCollection", "features": []}

        # Get most recent observation for each node before the timestamp.
        # Use MAX(id) to break ties when multiple observations share the
        # same timestamp for a node, preventing duplicate rows.
        query = """
            SELECT o.node_id, o.timestamp, o.latitude, o.longitude,
                   o.altitude, o.network, o.snr, o.battery, o.name
            FROM observations o
            INNER JOIN (
                SELECT MAX(id) as max_id
                FROM observations
                WHERE timestamp <= ?
                GROUP BY node_id
            ) latest ON o.id = latest.max_id
        """

        with self._lock:
            try:
                rows = self._conn.execute(query, (timestamp,)).fetchall()
            except Exception as e:
                logger.error("Snapshot query failed: %s", e)
                return {"type": "FeatureCollection", "features": []}

        features = []
        for row in rows:
            node_id, ts, lat, lon, alt, network, snr, battery, name = row
            props: Dict[str, Any] = {
                "id": node_id,
                "name": name or node_id,
                "network": network or "unknown",
                "last_seen": ts,
            }
            if snr is not None:
                props["snr"] = snr
            if battery is not None:
                props["battery"] = battery
            if alt is not None:
                props["altitude"] = alt

            coord = [lon, lat]
            if alt is not None:
                coord.append(alt)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coord},
                "properties": props,
            })

        return {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "snapshot_time": timestamp,
                "node_count": len(features),
            },
        }

    def get_tracked_nodes(self) -> List[Dict[str, Any]]:
        """List all nodes with observation counts and time ranges."""
        if not self._conn:
            return []

        query = """
            SELECT node_id, COUNT(*) as obs_count,
                   MIN(timestamp) as first_seen,
                   MAX(timestamp) as last_seen
            FROM observations
            GROUP BY node_id
            ORDER BY last_seen DESC
        """

        with self._lock:
            try:
                rows = self._conn.execute(query).fetchall()
            except Exception as e:
                logger.error("Tracked nodes query failed: %s", e)
                return []

        return [
            {
                "node_id": r[0],
                "observation_count": r[1],
                "first_seen": r[2],
                "last_seen": r[3],
            }
            for r in rows
        ]

    def prune_old_data(self, before_timestamp: Optional[int] = None) -> int:
        """Remove observations older than the retention period.

        Returns the number of rows deleted.
        """
        if not self._conn:
            return 0

        if before_timestamp is None:
            before_timestamp = int(time.time()) - self._retention_seconds

        with self._lock:
            try:
                cursor = self._conn.execute(
                    "DELETE FROM observations WHERE timestamp < ?",
                    (before_timestamp,),
                )
                self._conn.commit()
                deleted = cursor.rowcount
                if deleted:
                    logger.info("Pruned %d old node history observations", deleted)
                return deleted
            except Exception as e:
                logger.error("Prune failed: %s", e)
                return 0

    @property
    def observation_count(self) -> int:
        """Total number of observations in the database."""
        if not self._conn:
            return 0
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM observations"
                ).fetchone()
                return row[0] if row else 0
            except Exception as e:
                logger.debug("observation_count query failed: %s", e)
                return 0

    @property
    def node_count(self) -> int:
        """Number of distinct nodes with observations."""
        if not self._conn:
            return 0
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT COUNT(DISTINCT node_id) FROM observations"
                ).fetchone()
                return row[0] if row else 0
            except Exception as e:
                logger.debug("node_count query failed: %s", e)
                return 0

    def get_density_points(
        self,
        since: Optional[int] = None,
        until: Optional[int] = None,
        precision: int = 4,
        network: Optional[str] = None,
    ) -> List[Tuple[float, float, int]]:
        """Get observation density as (lat, lon, count) tuples for heatmap rendering.

        Observations are grouped by rounded (latitude, longitude) at the given
        decimal ``precision`` (default 4 ≈ ~11 m).  Each tuple represents a
        grid cell with its total observation count, suitable for feeding
        directly into a Leaflet.heat layer.

        Parameters
        ----------
        since : int, optional
            Unix timestamp — only include observations at or after this time.
        until : int, optional
            Unix timestamp — only include observations at or before this time.
        precision : int
            Decimal places to round lat/lon (controls grid resolution).
            4 → ~11 m cells, 3 → ~110 m, 2 → ~1.1 km.
        network : str, optional
            Filter to a specific network (e.g. "meshtastic").

        Returns
        -------
        list of (lat, lon, count)
            Sorted descending by count so the densest cells come first.
        """
        if not self._conn:
            return []

        query = (
            "SELECT ROUND(latitude, ?) AS lat, ROUND(longitude, ?) AS lon, "
            "COUNT(*) AS cnt FROM observations WHERE 1=1"
        )
        params: List[Any] = [precision, precision]

        if since is not None:
            query += " AND timestamp >= ?"
            params.append(since)
        if until is not None:
            query += " AND timestamp <= ?"
            params.append(until)
        if network is not None:
            query += " AND network = ?"
            params.append(network)

        query += " GROUP BY lat, lon ORDER BY cnt DESC"

        with self._lock:
            try:
                rows = self._conn.execute(query, params).fetchall()
            except Exception as e:
                logger.error("Density query failed: %s", e)
                return []

        return [(r[0], r[1], r[2]) for r in rows]

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            try:
                self._conn.close()
            except Exception as e:
                logger.debug("Error closing node history DB: %s", e)
            self._conn = None
            logger.debug("Node history DB closed")
