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
        if self._server:
            self._server.stop()
            self._server = None
        if self._config:
            self._config.save()
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


def main() -> None:
    """Standalone entry point for running outside MeshForge."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = MapsConfig()
    server = MapServer(config)

    if not server.start():
        print("ERROR: Failed to start map server. Check if the port is available.")
        sys.exit(1)

    print(f"MeshForge Maps running at http://127.0.0.1:{server.port}")
    print("Press Ctrl+C to stop")

    try:
        import time

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()


if __name__ == "__main__":
    main()
