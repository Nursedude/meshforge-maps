"""
MeshForge Maps - Historical Analytics

Time-series aggregation and trend analysis for mesh network monitoring.
Queries the NodeHistoryDB to compute:
  - Network growth over time (unique nodes per time bucket)
  - Node activity heatmap (observations per hour-of-day)
  - Per-node uptime and observation frequency
  - Alert trend aggregation (alerts per time bucket by severity)
  - Network-wide telemetry statistics (battery, SNR distributions)

All queries are read-only against the existing SQLite node history database
and the in-memory alert engine history.

Thread-safe: delegates to NodeHistoryDB and AlertEngine which hold their own locks.
"""

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default time bucket size for aggregation
DEFAULT_BUCKET_SECONDS = 3600  # 1 hour

# Maximum number of buckets to return (prevents huge responses)
MAX_BUCKETS = 720  # 30 days at 1-hour buckets


class HistoricalAnalytics:
    """Read-only analytics engine over NodeHistoryDB and AlertEngine data.

    Computes time-series aggregations without writing to the database.
    All methods return plain dicts suitable for JSON serialization.

    Args:
        node_history: NodeHistoryDB instance for observation queries.
        alert_engine: AlertEngine instance for alert history queries.
    """

    def __init__(self, node_history=None, alert_engine=None):
        self._history = node_history
        self._alert_engine = alert_engine

    def network_growth(
        self,
        since: Optional[int] = None,
        until: Optional[int] = None,
        bucket_seconds: int = DEFAULT_BUCKET_SECONDS,
    ) -> Dict[str, Any]:
        """Compute unique node count per time bucket.

        Returns time-series of network size (how many distinct nodes were
        observed in each bucket).

        Args:
            since: Start timestamp (default: 24 hours ago)
            until: End timestamp (default: now)
            bucket_seconds: Width of each time bucket in seconds

        Returns:
            Dict with "buckets" list and metadata
        """
        if not self._history:
            return {"buckets": [], "error": "Node history not available"}

        now = int(time.time())
        if until is None:
            until = now
        if since is None:
            since = until - (24 * 3600)

        bucket_seconds = max(60, min(bucket_seconds, 86400))
        num_buckets = min(MAX_BUCKETS, (until - since) // bucket_seconds + 1)

        query = """
            SELECT
                (timestamp / ?) * ? AS bucket_start,
                COUNT(DISTINCT node_id) AS unique_nodes,
                COUNT(*) AS total_observations
            FROM observations
            WHERE timestamp >= ? AND timestamp <= ?
            GROUP BY bucket_start
            ORDER BY bucket_start ASC
        """

        rows = self._history.execute_read(
            query, (bucket_seconds, bucket_seconds, since, until)
        )

        buckets = []
        for row in rows[:num_buckets]:
            buckets.append({
                "timestamp": row[0],
                "unique_nodes": row[1],
                "observations": row[2],
            })

        return {
            "buckets": buckets,
            "bucket_seconds": bucket_seconds,
            "since": since,
            "until": until,
            "total_buckets": len(buckets),
        }

    def activity_heatmap(
        self,
        since: Optional[int] = None,
        until: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Compute observation counts by hour of day (0-23).

        Useful for understanding when the mesh network is most active.

        Returns:
            Dict with "hours" list (24 entries, index=hour)
        """
        if not self._history:
            return {"hours": [0] * 24, "error": "Node history not available"}

        now = int(time.time())
        if until is None:
            until = now
        if since is None:
            since = until - (7 * 24 * 3600)  # Last 7 days

        # SQLite strftime gives hour as string "00".."23"
        query = """
            SELECT
                CAST(strftime('%H', timestamp, 'unixepoch') AS INTEGER) AS hour,
                COUNT(*) AS obs_count
            FROM observations
            WHERE timestamp >= ? AND timestamp <= ?
            GROUP BY hour
            ORDER BY hour ASC
        """

        rows = self._history.execute_read(query, (since, until))

        hours = [0] * 24
        for hour, count in rows:
            if 0 <= hour < 24:
                hours[hour] = count

        return {
            "hours": hours,
            "since": since,
            "until": until,
            "peak_hour": hours.index(max(hours)) if max(hours) > 0 else None,
            "total_observations": sum(hours),
        }

    def node_activity_ranking(
        self,
        since: Optional[int] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Rank nodes by observation count within a time window.

        Returns:
            Dict with ranked "nodes" list
        """
        if not self._history:
            return {"nodes": [], "error": "Node history not available"}

        now = int(time.time())
        if since is None:
            since = now - (24 * 3600)

        query = """
            SELECT
                node_id,
                COUNT(*) AS observation_count,
                MIN(timestamp) AS first_seen,
                MAX(timestamp) AS last_seen,
                network
            FROM observations
            WHERE timestamp >= ?
            GROUP BY node_id
            ORDER BY observation_count DESC
            LIMIT ?
        """

        rows = self._history.execute_read(query, (since, limit))

        nodes = []
        for row in rows:
            nodes.append({
                "node_id": row[0],
                "observation_count": row[1],
                "first_seen": row[2],
                "last_seen": row[3],
                "network": row[4],
                "active_seconds": row[3] - row[2] if row[3] and row[2] else 0,
            })

        return {
            "nodes": nodes,
            "since": since,
            "count": len(nodes),
        }

    def network_summary(
        self,
        since: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Compute high-level network statistics over a time window.

        Returns total nodes, observations, per-network breakdowns, and
        average observations per node.
        """
        if not self._history:
            return {"error": "Node history not available"}

        now = int(time.time())
        if since is None:
            since = now - (24 * 3600)

        query_totals = """
            SELECT
                COUNT(DISTINCT node_id) AS unique_nodes,
                COUNT(*) AS total_observations
            FROM observations
            WHERE timestamp >= ?
        """

        query_networks = """
            SELECT
                COALESCE(network, 'unknown') AS net,
                COUNT(DISTINCT node_id) AS node_count,
                COUNT(*) AS obs_count
            FROM observations
            WHERE timestamp >= ?
            GROUP BY net
            ORDER BY node_count DESC
        """

        totals_rows = self._history.execute_read(query_totals, (since,))
        network_rows = self._history.execute_read(query_networks, (since,))

        totals_row = totals_rows[0] if totals_rows else None
        unique_nodes = totals_row[0] if totals_row else 0
        total_obs = totals_row[1] if totals_row else 0

        networks = {}
        for row in network_rows:
            networks[row[0]] = {
                "node_count": row[1],
                "observation_count": row[2],
            }

        return {
            "unique_nodes": unique_nodes,
            "total_observations": total_obs,
            "avg_observations_per_node": (
                round(total_obs / unique_nodes, 1) if unique_nodes > 0 else 0
            ),
            "networks": networks,
            "since": since,
            "until": now,
        }

    def alert_trends(
        self,
        bucket_seconds: int = DEFAULT_BUCKET_SECONDS,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """Aggregate alert history into time buckets by severity.

        Reads from the in-memory AlertEngine history.

        Returns:
            Dict with "buckets" list containing per-severity counts
        """
        if not self._alert_engine:
            return {"buckets": [], "error": "Alert engine not available"}

        # Get raw alert history (most recent first)
        alerts = self._alert_engine.get_alert_history(limit=500)
        if not alerts:
            return {"buckets": [], "total_alerts": 0}

        # Group alerts into time buckets
        bucket_map: Dict[int, Dict[str, int]] = {}
        for a in alerts:
            ts = a.get("timestamp", 0)
            bucket_key = int(ts // bucket_seconds) * bucket_seconds
            if bucket_key not in bucket_map:
                bucket_map[bucket_key] = {
                    "critical": 0, "warning": 0, "info": 0, "total": 0,
                }
            severity = a.get("severity", "info")
            bucket_map[bucket_key][severity] = (
                bucket_map[bucket_key].get(severity, 0) + 1
            )
            bucket_map[bucket_key]["total"] += 1

        # Sort by timestamp and limit
        sorted_keys = sorted(bucket_map.keys())[-limit:]
        buckets = []
        for key in sorted_keys:
            entry = bucket_map[key]
            entry["timestamp"] = key
            buckets.append(entry)

        return {
            "buckets": buckets,
            "bucket_seconds": bucket_seconds,
            "total_alerts": len(alerts),
            "total_buckets": len(buckets),
        }
