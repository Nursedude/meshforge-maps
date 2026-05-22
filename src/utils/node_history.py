"""
MeshForge Maps - Node History Database (v2: movement-triggered trajectory)

Two-table schema:
  - ``nodes_current``: one row per node, UPSERT on every position message.
    Always the latest position. Bounded by node_count.
  - ``trajectory``: append-only motion log. A new row is written only when
    haversine distance from the node's previous trajectory row exceeds
    ``move_threshold_meters`` (default 50 m). Per-node cap evicts oldest
    row in the same transaction so DB size is bounded by
    (node_count × trajectory_rows_per_node).

This replaces the v1 firehose model where every broker message that survived
a coarse heartbeat became an ``observations`` row. v1's DB grew with message
volume; v2's grows with actual node motion + node count, both bounded on
Pi-class hardware regardless of broker rate.

Migration is in-place: ``PRAGMA user_version`` checked at startup. v1 DBs
get their latest-row-per-node seeded into ``nodes_current``, ``observations``
is dropped, and ``user_version`` is bumped to 2.

Storage location: ~/.local/share/meshforge/maps_node_history.db
"""

import logging
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .paths import get_data_dir

logger = logging.getLogger(__name__)

# Default DB location
DEFAULT_DB_PATH = get_data_dir() / "maps_node_history.db"

# Default retention period (seconds). Time-based prune still applies to the
# trajectory table — old motion events get aged out even if a node is well
# under its per-node cap. Default 1 day matches the fleet-default since the
# 2026-05-21 moc1 incident (PR #81 / `a460b22`).
DEFAULT_RETENTION_SECONDS = 1 * 24 * 3600

# Movement threshold for appending to trajectory. Below this many meters of
# haversine distance from the node's previous trajectory point, the message
# updates ``nodes_current`` only and skips the trajectory append. 50 m skips
# GPS jitter (~5-15 m on consumer hardware) while catching pedestrian and
# vehicle motion.
DEFAULT_MOVE_THRESHOLD_METERS = 50.0

# Per-node trajectory row cap. When a node's row count would exceed this,
# the oldest row for that node is deleted in the same transaction. Hard
# ceiling on DB size: node_count × cap × ~150 bytes per row.
DEFAULT_TRAJECTORY_ROWS_PER_NODE = 500

# Maximum trajectory points returned by a single get_trajectory_geojson call.
# Independent from the per-node storage cap — this is just a query limit.
MAX_TRAJECTORY_POINTS = 1000

# Earth radius in meters for the haversine helper.
_EARTH_RADIUS_M = 6_371_000.0

# Schema version stamped into PRAGMA user_version. Bump when the schema
# changes incompatibly. 0 / unset = v1 (observations table) or fresh DB.
_USER_VERSION = 2


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two (lat, lon) pairs in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


class NodeHistoryDB:
    """SQLite-backed node position store with movement-triggered trajectory.

    Thread-safe: all operations acquire a lock around DB access.
    Uses WAL mode for better concurrent read performance.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
        move_threshold_meters: float = DEFAULT_MOVE_THRESHOLD_METERS,
        trajectory_rows_per_node: int = DEFAULT_TRAJECTORY_ROWS_PER_NODE,
        # Deprecated v1 constructor params — accepted and ignored for one
        # cycle so MapServer doesn't break on stale config. The v2 writer
        # doesn't throttle (UPSERT is cheap) or heartbeat-dedupe (movement
        # is the trigger).
        throttle_seconds: Optional[int] = None,
        heartbeat_seconds: Optional[int] = None,
    ):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._retention_seconds = retention_seconds
        self._move_threshold_meters = max(0.0, float(move_threshold_meters))
        self._trajectory_rows_per_node = max(1, int(trajectory_rows_per_node))
        if throttle_seconds is not None:
            logger.debug(
                "NodeHistoryDB v2 ignores throttle_seconds=%s (deprecated)",
                throttle_seconds,
            )
        if heartbeat_seconds is not None:
            logger.debug(
                "NodeHistoryDB v2 ignores heartbeat_seconds=%s (deprecated)",
                heartbeat_seconds,
            )
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        # In-memory per-node trajectory state — lazily populated on first
        # write for a node, then kept in sync with the DB on every append.
        # Avoids a (lat, lon) query and a COUNT(*) per write.
        self._last_traj_pos: Dict[str, Tuple[float, float]] = {}
        self._traj_count: Dict[str, int] = {}
        # Cached count queries (still useful — trajectory count drives
        # /api/health observation_count surfacing).
        self._cached_obs_count: int = 0
        self._cached_node_count: int = 0
        self._count_cache_time: float = float("-inf")
        self._COUNT_CACHE_TTL = 300  # 5 minutes
        # Last fatal write error (disk full / IO error). Surfaced in
        # /api/health so an operator sees the silent backlog instead of
        # having to grep journalctl.
        self._last_write_error_at: Optional[int] = None
        self._last_write_error_msg: Optional[str] = None
        self._init_db()

    def _init_db(self) -> None:
        """Open DB, ensure v2 schema, run v1→v2 migration if needed."""
        conn = None
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

            # Check directory ownership — warn if owned by different user
            import os as _os
            if self._db_path.parent.exists():
                dir_stat = self._db_path.parent.stat()
                uid = _os.getuid()
                if dir_stat.st_uid != uid and uid != 0:
                    logger.warning(
                        "Data directory %s owned by uid %d but running as uid %d. "
                        "Fix with: sudo chown -R %d:%d %s",
                        self._db_path.parent, dir_stat.st_uid, uid,
                        uid, _os.getgid(), self._db_path.parent,
                    )

            conn = self._open_connection()

            # Integrity check — rotate corrupt databases
            try:
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result and result[0] != "ok":
                    logger.critical(
                        "Database integrity check failed: %s — rotating corrupt DB",
                        result[0],
                    )
                    conn.close()
                    corrupt_path = self._db_path.with_suffix(
                        f".db.corrupt.{int(time.time())}"
                    )
                    self._db_path.rename(corrupt_path)
                    logger.critical("Corrupt DB moved to %s", corrupt_path)
                    conn = self._open_connection()
            except sqlite3.DatabaseError as e:
                logger.critical("Cannot check DB integrity: %s — re-creating", e)
                conn.close()
                corrupt_path = self._db_path.with_suffix(
                    f".db.corrupt.{int(time.time())}"
                )
                try:
                    self._db_path.rename(corrupt_path)
                except OSError:
                    self._db_path.unlink(missing_ok=True)
                conn = self._open_connection()

            self._ensure_v2_schema(conn)
            conn.commit()
            self._conn = conn
            logger.info("Node history DB initialized at %s (v2 schema)", self._db_path)
        except Exception as e:
            logger.error("Failed to initialize node history DB: %s", e)
            if conn is not None:
                try:
                    conn.close()
                except Exception as close_exc:
                    logger.debug("Error closing DB connection during init failure: %s", close_exc)
            self._conn = None

    def _ensure_v2_schema(self, conn: sqlite3.Connection) -> None:
        """Create v2 tables; migrate v1 observations if present."""
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes_current (
                node_id   TEXT PRIMARY KEY,
                timestamp INTEGER NOT NULL,
                latitude  REAL    NOT NULL,
                longitude REAL    NOT NULL,
                altitude  REAL,
                network   TEXT,
                snr       REAL,
                battery   INTEGER,
                name      TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trajectory (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id   TEXT    NOT NULL,
                timestamp INTEGER NOT NULL,
                latitude  REAL    NOT NULL,
                longitude REAL    NOT NULL,
                altitude  REAL,
                network   TEXT,
                snr       REAL,
                battery   INTEGER,
                name      TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_traj_node_time
            ON trajectory (node_id, timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_traj_time
            ON trajectory (timestamp)
        """)

        if user_version < _USER_VERSION:
            self._migrate_v1_to_v2(conn)
            conn.execute(f"PRAGMA user_version = {_USER_VERSION}")

    def _migrate_v1_to_v2(self, conn: sqlite3.Connection) -> None:
        """Seed nodes_current from v1 observations and drop the old table.

        Fresh-start migration per the v2 plan: keep one row per node (the
        latest observation), discard the rest. trajectory starts empty.
        """
        has_v1 = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='observations'"
        ).fetchone()
        if not has_v1:
            logger.info("No v1 observations table found — fresh v2 install")
            return

        before = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        # Seed nodes_current with the latest row per node. MAX(id) on an
        # AUTOINCREMENT column gives a deterministic "latest" pick when
        # multiple rows share MAX(timestamp).
        conn.execute("""
            INSERT OR IGNORE INTO nodes_current
                (node_id, timestamp, latitude, longitude, altitude,
                 network, snr, battery, name)
            SELECT node_id, timestamp, latitude, longitude, altitude,
                   network, snr, battery, name
            FROM observations
            WHERE id IN (SELECT MAX(id) FROM observations GROUP BY node_id)
        """)
        seeded = conn.execute("SELECT COUNT(*) FROM nodes_current").fetchone()[0]
        conn.execute("DROP TABLE observations")
        # Reclaim the space the dropped table held. VACUUM here is bounded
        # by the post-migration DB size (nodes_current is small), not the
        # original v1 DB size.
        conn.commit()
        conn.execute("VACUUM")
        logger.info(
            "v1→v2 migration: dropped observations (%d rows), seeded "
            "nodes_current with %d nodes",
            before, seeded,
        )

    def _open_connection(self) -> sqlite3.Connection:
        """Open a SQLite connection with standard PRAGMAs."""
        conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False,
        )
        try:
            # auto_vacuum must be set BEFORE journal_mode=WAL, which
            # initializes the DB and locks in the vacuum mode.
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            conn.execute("PRAGMA journal_mode=WAL")
            # synchronous=NORMAL: with WAL this is durable across power loss
            # for most-recent commits; sufficient for telemetry.
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            # Cap the WAL file at 64 MB.
            conn.execute("PRAGMA journal_size_limit=67108864")
        except Exception:
            conn.close()
            raise
        return conn

    def execute_read(self, query: str, params: tuple = ()) -> list:
        """Execute a read-only query under the DB lock. Returns list of rows."""
        with self._lock:
            if not self._conn:
                return []
            try:
                return self._conn.execute(query, params).fetchall()
            except sqlite3.OperationalError:
                return []

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

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
        """Record a node position: always UPSERT nodes_current; conditionally
        append to trajectory if movement exceeds the threshold.

        Returns True if the write succeeded (regardless of whether trajectory
        was appended), False on DB error.
        """
        if not self._conn:
            return False

        now = timestamp if timestamp is not None else int(time.time())
        row = (node_id, now, lat, lon, altitude, network, snr, battery, name)

        with self._lock:
            try:
                self._upsert_current_locked(row)
                appended = self._maybe_append_trajectory_locked(row)
                self._conn.commit()
                if appended:
                    self._count_cache_time = float("-inf")
                self._last_write_error_at = None
                self._last_write_error_msg = None
                return True
            except sqlite3.Error as e:
                self._record_write_error(e, node_id)
                return False

    def record_observations_batch(
        self, observations: List[Dict[str, Any]]
    ) -> int:
        """Batch-record observations under a single lock + commit.

        Returns the number of observations that produced a trajectory append
        (NOT the number of UPSERTs — every observation touches nodes_current).
        """
        if not self._conn or not observations:
            return 0

        now = int(time.time())
        appended_count = 0

        with self._lock:
            try:
                for obs in observations:
                    node_id = obs.get("node_id")
                    if not node_id:
                        continue
                    lat = obs.get("lat")
                    lon = obs.get("lon")
                    if lat is None or lon is None:
                        continue
                    row = (
                        node_id, now, lat, lon, obs.get("altitude"),
                        obs.get("network", ""), obs.get("snr"),
                        obs.get("battery"), obs.get("name", ""),
                    )
                    self._upsert_current_locked(row)
                    if self._maybe_append_trajectory_locked(row):
                        appended_count += 1
                self._conn.commit()
                if appended_count:
                    self._count_cache_time = float("-inf")
                self._last_write_error_at = None
                self._last_write_error_msg = None
                return appended_count
            except sqlite3.Error as e:
                self._record_write_error(e, f"batch x{len(observations)}")
                return 0

    def _upsert_current_locked(self, row: Tuple[Any, ...]) -> None:
        """INSERT OR REPLACE into nodes_current. Must be called under self._lock."""
        self._conn.execute(
            """INSERT OR REPLACE INTO nodes_current
                   (node_id, timestamp, latitude, longitude, altitude,
                    network, snr, battery, name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )

    def _maybe_append_trajectory_locked(self, row: Tuple[Any, ...]) -> bool:
        """Append to trajectory if movement threshold is met (or first sighting).

        Evicts the oldest row for this node in the same transaction when the
        per-node cap would be exceeded. Returns True if a row was inserted.
        Must be called under self._lock.
        """
        node_id, _ts, lat, lon = row[0], row[1], row[2], row[3]

        # Lazily prime the in-memory caches from the DB on first write per node.
        if node_id not in self._last_traj_pos:
            last = self._conn.execute(
                "SELECT latitude, longitude FROM trajectory WHERE node_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (node_id,),
            ).fetchone()
            if last is not None:
                self._last_traj_pos[node_id] = (last[0], last[1])
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM trajectory WHERE node_id = ?",
                    (node_id,),
                ).fetchone()[0]
                self._traj_count[node_id] = count

        prev = self._last_traj_pos.get(node_id)
        if prev is not None:
            distance = _haversine_meters(prev[0], prev[1], lat, lon)
            if distance < self._move_threshold_meters:
                return False

        self._conn.execute(
            """INSERT INTO trajectory
                   (node_id, timestamp, latitude, longitude, altitude,
                    network, snr, battery, name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        self._last_traj_pos[node_id] = (lat, lon)
        new_count = self._traj_count.get(node_id, 0) + 1

        if new_count > self._trajectory_rows_per_node:
            # Evict oldest row for this node in the same transaction.
            self._conn.execute(
                "DELETE FROM trajectory WHERE id = "
                "(SELECT MIN(id) FROM trajectory WHERE node_id = ?)",
                (node_id,),
            )
            new_count = self._trajectory_rows_per_node

        self._traj_count[node_id] = new_count
        return True

    # ------------------------------------------------------------------
    # Error visibility (consumed by /api/health)
    # ------------------------------------------------------------------

    _DISK_FULL_SIGNALS = ("disk i/o error", "database or disk is full", "no space left")

    def _record_write_error(self, exc: Exception, context: str) -> None:
        """Disk-full / IO errors → ERROR + remembered for /api/health.
        Other sqlite errors → WARNING. Must be called under self._lock."""
        msg = str(exc).lower()
        is_disk_problem = (
            isinstance(exc, sqlite3.OperationalError)
            and any(sig in msg for sig in self._DISK_FULL_SIGNALS)
        )
        if is_disk_problem:
            logger.error(
                "node_history disk-full / IO error while recording %s: %s",
                context, exc,
            )
            self._last_write_error_at = int(time.time())
            self._last_write_error_msg = str(exc)
        else:
            logger.warning(
                "node_history failed to record %s: %s", context, exc,
            )

    def write_error_state(self) -> Dict[str, Any]:
        """Snapshot of the last disk-fatal write error, for /api/health."""
        with self._lock:
            return {
                "last_write_error_at": self._last_write_error_at,
                "last_write_error_msg": self._last_write_error_msg,
            }

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

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

        If the node has only one trajectory row, returns a Point geometry instead.
        If no trajectory found, returns an empty FeatureCollection.
        """
        if not self._conn:
            return {"type": "FeatureCollection", "features": []}

        query = "SELECT timestamp, latitude, longitude, altitude FROM trajectory WHERE node_id = ?"
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

        from ..collectors.base import make_geometry_feature

        if len(coordinates) == 1:
            geometry = {"type": "Point", "coordinates": coordinates[0]}
        else:
            geometry = {"type": "LineString", "coordinates": coordinates}

        feature = make_geometry_feature(
            geometry,
            node_id=node_id,
            point_count=len(coordinates),
            first_seen=timestamps[0] if timestamps else None,
            last_seen=timestamps[-1] if timestamps else None,
            time_span_seconds=(
                timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0
            ),
        )
        return {"type": "FeatureCollection", "features": [feature]}

    def get_node_history(
        self,
        node_id: str,
        since: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get trajectory history for a node as a list of dicts."""
        if not self._conn:
            return []

        query = """SELECT timestamp, latitude, longitude, altitude, network,
                          snr, battery, name
                   FROM trajectory WHERE node_id = ?"""
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
        """Return the network state at or before a given timestamp.

        For ``timestamp`` >= now, returns ``nodes_current`` directly (fast,
        one row per node). For past timestamps, walks the trajectory table
        and returns the latest row per node before ``timestamp``.

        Nodes that have only ever upserted nodes_current (never moved enough
        to generate a trajectory row) won't appear in past-timestamp
        snapshots — by design, since we don't know where they were then.
        """
        if not self._conn:
            return {"type": "FeatureCollection", "features": []}

        now = int(time.time())
        if timestamp >= now:
            query = (
                "SELECT node_id, timestamp, latitude, longitude, altitude, "
                "network, snr, battery, name FROM nodes_current"
            )
            params: Tuple[Any, ...] = ()
        else:
            # Latest trajectory row per node before timestamp. MAX(id) breaks
            # ties when multiple rows share the same timestamp.
            query = """
                SELECT t.node_id, t.timestamp, t.latitude, t.longitude,
                       t.altitude, t.network, t.snr, t.battery, t.name
                FROM trajectory t
                INNER JOIN (
                    SELECT MAX(id) AS max_id
                    FROM trajectory
                    WHERE timestamp <= ?
                    GROUP BY node_id
                ) latest ON t.id = latest.max_id
            """
            params = (timestamp,)

        with self._lock:
            try:
                rows = self._conn.execute(query, params).fetchall()
            except Exception as e:
                logger.error("Snapshot query failed: %s", e)
                return {"type": "FeatureCollection", "features": []}

        from ..collectors.base import make_geometry_feature

        features = []
        for row in rows:
            node_id, ts, lat, lon, alt, network, snr, battery, name = row
            coord = [lon, lat]
            if alt is not None:
                coord.append(alt)
            feature = make_geometry_feature(
                {"type": "Point", "coordinates": coord},
                id=node_id,
                name=name or node_id,
                network=network or "unknown",
                last_seen=ts,
                snr=snr,
                battery=battery,
                altitude=alt,
            )
            features.append(feature)

        return {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "snapshot_time": timestamp,
                "node_count": len(features),
            },
        }

    def get_tracked_nodes(self) -> List[Dict[str, Any]]:
        """List all nodes that have generated trajectory data.

        ``observation_count`` here is the number of MOVEMENT EVENTS recorded
        in trajectory, not message count. v2 semantics: this measures
        mobility, not chattiness.
        """
        if not self._conn:
            return []

        query = """
            SELECT node_id, COUNT(*) AS obs_count,
                   MIN(timestamp) AS first_seen,
                   MAX(timestamp) AS last_seen
            FROM trajectory
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
        """Remove trajectory rows older than the retention period.

        ``nodes_current`` is never time-pruned (always the latest snapshot).
        Returns the number of trajectory rows deleted.
        """
        if not self._conn:
            return 0

        if before_timestamp is None:
            before_timestamp = int(time.time()) - self._retention_seconds

        with self._lock:
            try:
                cursor = self._conn.execute(
                    "DELETE FROM trajectory WHERE timestamp < ?",
                    (before_timestamp,),
                )
                self._conn.commit()
                deleted = cursor.rowcount
                if deleted:
                    logger.info("Pruned %d old trajectory rows", deleted)
                    self._count_cache_time = float("-inf")
                # Keep checkpointing — incremental_vacuum + wal_checkpoint
                # are cheap and prevent WAL growth even when prune is a no-op.
                try:
                    self._conn.execute("PRAGMA incremental_vacuum(2000)")
                except Exception as ve:
                    logger.debug("Incremental vacuum failed: %s", ve)
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception as we:
                    logger.warning("WAL checkpoint failed: %s", we)
                # Drop stale entries from the in-memory trajectory caches
                # for nodes whose latest trajectory row was pruned. Looking
                # up the new latest position lazily on next write is cheaper
                # than tracking it here.
                stale = []
                for nid in list(self._last_traj_pos.keys()):
                    has_any = self._conn.execute(
                        "SELECT 1 FROM trajectory WHERE node_id = ? LIMIT 1",
                        (nid,),
                    ).fetchone()
                    if not has_any:
                        stale.append(nid)
                for nid in stale:
                    self._last_traj_pos.pop(nid, None)
                    self._traj_count.pop(nid, None)
                if stale:
                    logger.debug(
                        "Cleared trajectory caches for %d fully-pruned nodes",
                        len(stale),
                    )
                return deleted
            except Exception as e:
                logger.error("Prune failed: %s", e)
                return 0

    def _refresh_count_cache(self) -> None:
        """Refresh cached trajectory/node counts (call under lock)."""
        try:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM trajectory"
            ).fetchone()
            self._cached_obs_count = row[0] if row else 0
            row = self._conn.execute(
                "SELECT COUNT(*) FROM nodes_current"
            ).fetchone()
            self._cached_node_count = row[0] if row else 0
            self._count_cache_time = time.monotonic()
        except Exception as e:
            logger.debug("Count cache refresh failed: %s", e)

    @property
    def observation_count(self) -> int:
        """Total trajectory rows in the database (cached, 5min TTL).

        v2 semantics: total movement events recorded, bounded by
        node_count × trajectory_rows_per_node.
        """
        if not self._conn:
            return 0
        if (time.monotonic() - self._count_cache_time) < self._COUNT_CACHE_TTL:
            return self._cached_obs_count
        with self._lock:
            self._refresh_count_cache()
            return self._cached_obs_count

    @property
    def node_count(self) -> int:
        """Number of distinct nodes tracked in nodes_current (cached, 5min TTL)."""
        if not self._conn:
            return 0
        if (time.monotonic() - self._count_cache_time) < self._COUNT_CACHE_TTL:
            return self._cached_node_count
        with self._lock:
            self._refresh_count_cache()
            return self._cached_node_count

    def get_density_points(
        self,
        since: Optional[int] = None,
        until: Optional[int] = None,
        precision: int = 4,
        network: Optional[str] = None,
    ) -> List[Tuple[float, float, int]]:
        """Get movement density as (lat, lon, count) tuples for heatmap rendering.

        v2 semantics: density of movement events, not message volume.
        """
        if not self._conn:
            return []

        query = (
            "SELECT ROUND(latitude, ?) AS lat, ROUND(longitude, ?) AS lon, "
            "COUNT(*) AS cnt FROM trajectory WHERE 1=1"
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

    def create_backup(self, backup_path: Path) -> bool:
        """Create an online SQLite backup at the given path."""
        with self._lock:
            if not self._conn:
                return False
            try:
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_conn = sqlite3.connect(str(backup_path))
                self._conn.backup(backup_conn)
                backup_conn.close()
                logger.info("DB backup created at %s", backup_path)
                return True
            except Exception as e:
                logger.error("Backup failed: %s", e)
                return False

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            try:
                self._conn.close()
            except Exception as e:
                logger.debug("Error closing node history DB: %s", e)
            self._conn = None
            logger.debug("Node history DB closed")
