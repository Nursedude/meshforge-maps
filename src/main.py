"""
MeshForge Maps - Main Plugin Entry Point

Extension plugin for MeshForge that provides a unified multi-source
mesh network map. Aggregates data from Meshtastic, Reticulum/RMAP,
HamClock/propagation, and AREDN into a configurable Leaflet.js web map.

Plugin type: extension
License: GPL-3.0 (matches MeshForge core)

Usage:
  - As MeshForge plugin: placed in ~/.config/meshforge/plugins/meshforge-maps/
  - Standalone: python -m src.main
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .map_server import MapServer
from .utils.config import MapsConfig

logger = logging.getLogger(__name__)

# MeshForge plugin base class import (graceful fallback for standalone use)
try:
    from meshforge.core.plugin_base import Plugin, PluginContext
except ImportError:
    try:
        from core.plugin_base import Plugin, PluginContext
    except ImportError:
        # Standalone mode - define minimal stubs
        class PluginContext:  # type: ignore[no-redef]
            app_version: str = "standalone"
            data_dir: Path = Path.home() / ".local" / "share" / "meshforge"
            config_dir: Path = Path.home() / ".config" / "meshforge"

            def register_panel(self, *args: Any, **kwargs: Any) -> None:
                pass

            def register_tool(self, *args: Any, **kwargs: Any) -> None:
                pass

            def subscribe(self, *args: Any, **kwargs: Any) -> None:
                pass

            def notify(self, *args: Any, **kwargs: Any) -> None:
                pass

            def get_service(self, name: str) -> Any:
                return None

        class Plugin:  # type: ignore[no-redef]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.state = "loaded"
                self._context: Optional[PluginContext] = None
                self._settings: Dict[str, Any] = {}

            def activate(self, context: PluginContext) -> None:
                raise NotImplementedError

            def deactivate(self) -> None:
                raise NotImplementedError


class MeshForgeMapsPlugin(Plugin):
    """MeshForge Maps extension plugin.

    Provides a unified web map that aggregates mesh network node data
    from multiple sources with configurable tile layers and overlays.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._config: Optional[MapsConfig] = None
        self._server: Optional[MapServer] = None

    def activate(self, context: PluginContext) -> None:
        """Initialize and start the map server."""
        self._context = context
        self._config = MapsConfig()

        # Apply any saved settings from MeshForge
        if hasattr(context, "settings") and context.settings:
            self._config.update(context.settings)

        # Start the HTTP map server
        self._server = MapServer(self._config)
        started = self._server.start()

        if not started:
            logger.error("MeshForge Maps plugin failed to start map server")
            if hasattr(context, "notify"):
                context.notify(
                    "MeshForge Maps",
                    "Failed to start map server -- check port availability",
                )
            return

        # Register with MeshForge TUI if available
        if hasattr(context, "register_panel"):
            context.register_panel(
                panel_id="meshforge_maps",
                panel_class=None,
                title="MeshForge Maps",
                icon="map-symbolic",
            )

        if hasattr(context, "register_tool"):
            context.register_tool(
                tool_id="meshforge_maps_refresh",
                tool_func=self._refresh_data,
                name="Refresh Map Data",
                description="Force refresh all map data sources",
            )
            context.register_tool(
                tool_id="meshforge_maps_status",
                tool_func=self._get_status,
                name="Map Status",
                description="Show map server status and node counts",
            )
            context.register_tool(
                tool_id="meshforge_maps_propagation",
                tool_func=self._get_propagation,
                name="HF Propagation",
                description="Show current HF propagation conditions and band predictions",
            )
            context.register_tool(
                tool_id="meshforge_maps_dxspots",
                tool_func=self._get_dxspots,
                name="DX Spots",
                description="Show recent DX cluster spots from HamClock",
            )
            context.register_tool(
                tool_id="meshforge_maps_hamclock_status",
                tool_func=self._get_hamclock_status,
                name="HamClock Status",
                description="Show HamClock connection status and full data summary",
            )

        # Subscribe to node events for live updates
        if hasattr(context, "subscribe"):
            context.subscribe("node_discovered", self._on_node_discovered)
            context.subscribe("config_changed", self._on_config_changed)

        if hasattr(context, "notify"):
            port = self._server.port
            context.notify(
                "MeshForge Maps",
                f"Map server started on http://127.0.0.1:{port}",
            )

        logger.info("MeshForge Maps plugin activated on port %d", self._server.port)

    def deactivate(self) -> None:
        """Stop the map server and clean up all resources."""
        try:
            if self._server:
                self._server.stop()
                self._server = None
        except Exception as e:
            logger.error("Error stopping map server: %s", e)
        try:
            if self._config:
                self._config.save()
        except Exception as e:
            logger.error("Error saving config on deactivate: %s", e)
        logger.info("MeshForge Maps plugin deactivated")

    def _refresh_data(self) -> str:
        """Force refresh all data sources."""
        if self._server:
            self._server.aggregator.clear_all_caches()
            data = self._server.aggregator.collect_all()
            count = data.get("properties", {}).get("total_nodes", 0)
            sources = data.get("properties", {}).get("sources", {})
            return f"Refreshed: {count} nodes from {sources}"
        return "Server not running"

    def _get_status(self) -> str:
        """Return current server status for MeshForge TUI display."""
        if not self._server:
            return "Map server is not running"
        agg = self._server.aggregator
        mqtt_status = "unavailable"
        if agg._mqtt_subscriber:
            mqtt_status = "connected" if agg._mqtt_subscriber._running else "stopped"
        sources = list(agg._collectors.keys())
        return (
            f"Port: {self._server.port} | "
            f"Sources: {', '.join(sources)} | "
            f"MQTT: {mqtt_status}"
        )

    def _get_propagation(self) -> str:
        """Return current HF propagation conditions for TUI display."""
        if not self._server:
            return "Server not running"
        hc = self._server.aggregator._collectors.get("hamclock")
        if not hc:
            return "HamClock source not enabled"
        data = hc.get_hamclock_data()
        lines = []
        lines.append(f"Source: {data.get('source', 'unknown')}")
        # Space weather summary
        sw = data.get("space_weather", {})
        if sw:
            sfi = sw.get("solar_flux", "--")
            kp = sw.get("kp_index", "--")
            cond = sw.get("band_conditions", "unknown")
            lines.append(f"SFI: {sfi} | Kp: {kp} | Conditions: {cond}")
        # VOACAP predictions
        voacap = data.get("voacap")
        if voacap and voacap.get("bands"):
            lines.append("VOACAP Band Predictions:")
            best = voacap.get("best_band")
            for band, info in voacap["bands"].items():
                rel = info.get("reliability", 0)
                status = info.get("status", "?")
                marker = " <-- BEST" if band == best else ""
                lines.append(f"  {band}: {rel}% ({status}){marker}")
        # Band conditions from HamClock
        bc = data.get("band_conditions")
        if bc and bc.get("bands"):
            lines.append("Band Conditions:")
            for band, cond in bc["bands"].items():
                lines.append(f"  {band}: {cond}")
        return "\n".join(lines)

    def _get_dxspots(self) -> str:
        """Return recent DX spots for TUI display."""
        if not self._server:
            return "Server not running"
        hc = self._server.aggregator._collectors.get("hamclock")
        if not hc:
            return "HamClock source not enabled"
        data = hc.get_hamclock_data()
        if not data.get("available"):
            return "HamClock not available -- DX spots require HamClock API"
        spots = data.get("dxspots")
        if not spots:
            return "No DX spots available"
        lines = [f"DX Spots ({len(spots)} recent):"]
        lines.append(f"{'DX Call':<10} {'Freq':>8} {'DE Call':<10} {'UTC':<6}")
        lines.append("-" * 38)
        for s in spots[:15]:
            dx = s.get("dx_call", "?")
            freq = s.get("freq_khz", "?")
            de = s.get("de_call", "")
            utc = s.get("utc", "")
            lines.append(f"{dx:<10} {freq:>8} {de:<10} {utc:<6}")
        return "\n".join(lines)

    def _get_hamclock_status(self) -> str:
        """Return full HamClock status for TUI display."""
        if not self._server:
            return "Server not running"
        hc = self._server.aggregator._collectors.get("hamclock")
        if not hc:
            return "HamClock source not enabled"
        data = hc.get_hamclock_data()
        lines = []
        available = data.get("available", False)
        lines.append(f"HamClock: {'CONNECTED' if available else 'UNAVAILABLE'}")
        lines.append(f"Host: {data.get('host', '?')}:{data.get('port', '?')}")
        lines.append(f"Data Source: {data.get('source', 'unknown')}")
        # DE/DX
        de = data.get("de_station")
        dx = data.get("dx_station")
        if de:
            lines.append(f"DE: {de.get('call', '--')} ({de.get('grid', '--')})")
        if dx:
            lines.append(f"DX: {dx.get('call', '--')} ({dx.get('grid', '--')})")
        # Spot count
        spots = data.get("dxspots")
        if spots:
            lines.append(f"DX Spots: {len(spots)} active")
        # Circuit breaker state
        cb_states = self._server.aggregator.get_circuit_breaker_states()
        hc_state = cb_states.get("hamclock", {})
        if hc_state:
            lines.append(f"Circuit Breaker: {hc_state.get('state', '?')} "
                         f"(failures: {hc_state.get('failure_count', 0)})")
        return "\n".join(lines)

    def _on_node_discovered(self, data: Any) -> None:
        """Handle new node discovery events from MeshForge core.

        Clears relevant collector caches so the next API request
        picks up fresh data that may include the new node.
        """
        if self._server:
            self._server.aggregator.clear_all_caches()
            logger.debug("Node discovered, caches cleared: %s", data)

    def _on_config_changed(self, data: Any) -> None:
        """Handle configuration change events from MeshForge TUI.

        Applies setting changes (e.g. toggling sources) without
        requiring a restart.
        """
        if self._config and isinstance(data, dict):
            self._config.update(data)
            self._config.save()
            logger.info("Config updated from MeshForge: %s", data)
        else:
            logger.debug("Config changed event (unhandled format): %s", data)


# Factory function for MeshForge plugin loader
def create_plugin() -> MeshForgeMapsPlugin:
    return MeshForgeMapsPlugin()


def _get_error_log_path() -> Path:
    """Get the path to the error log file."""
    try:
        log_dir = Path.home() / ".cache" / "meshforge" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "maps_errors.log"
    except Exception:
        return Path("/tmp/meshforge_maps_errors.log")


def main() -> None:
    """Standalone entry point for running outside MeshForge."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    server = None
    exit_code = 0
    try:
        config = MapsConfig()
        server = MapServer(config)

        if not server.start():
            print("ERROR: Failed to start map server. Check if the port is available.")
            sys.exit(1)

        print(f"MeshForge Maps running at http://127.0.0.1:{server.port}")
        print("Press Ctrl+C to stop")

        import time

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        # Log full traceback to error log file
        import datetime
        import traceback

        error_log = _get_error_log_path()
        try:
            with open(error_log, "a") as f:
                f.write(f"\n{'=' * 60}\n")
                f.write(f"[{datetime.datetime.now().isoformat()}] FATAL ERROR\n")
                f.write(traceback.format_exc())
                f.write(f"{'=' * 60}\n")
        except Exception:
            pass

        print(f"\nMeshForge Maps encountered a fatal error:\n")
        print(f"  {type(e).__name__}: {e}\n")
        print(f"Full error details saved to:\n  {error_log}\n")
        print("To report this issue:")
        print("  https://github.com/Nursedude/meshforge-maps/issues\n")
        exit_code = 1
    finally:
        if server:
            try:
                server.stop()
            except Exception:
                pass
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
