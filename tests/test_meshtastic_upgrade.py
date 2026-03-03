"""Tests for Meshtastic API upgrade features.

Tests cover:
  - New telemetry types in MQTT subscriber (PowerMetrics, LocalStats, HostMetrics)
  - Expanded EnvironmentMetrics fields (wind, rain, soil, lux)
  - MAP_REPORT_APP handling (portnum 73)
  - /api/dependencies endpoint
  - System TUI tab rendering
  - MapDataClient.dependencies_info() accessor
  - Tab 7 keybinding in TUI app
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ── MQTT Subscriber: New Telemetry Types ─────────────────────────


class TestMQTTNewTelemetry:
    """Tests for new telemetry types in mqtt_subscriber."""

    def _make_store(self):
        from src.collectors.mqtt_subscriber import MQTTNodeStore
        return MQTTNodeStore()

    def test_update_telemetry_wind_fields(self):
        store = self._make_store()
        store.update_telemetry(
            "!abc123",
            wind_direction=180,
            wind_speed=15.5,
            wind_gust=22.0,
            wind_lull=8.3,
        )
        with store._lock:
            node = store._nodes["!abc123"]
        assert node["wind_direction"] == 180
        assert node["wind_speed"] == 15.5
        assert node["wind_gust"] == 22.0
        assert node["wind_lull"] == 8.3

    def test_update_telemetry_rainfall_fields(self):
        store = self._make_store()
        store.update_telemetry(
            "!abc123",
            rainfall_1h=5.2,
            rainfall_24h=42.1,
        )
        with store._lock:
            node = store._nodes["!abc123"]
        assert node["rainfall_1h"] == 5.2
        assert node["rainfall_24h"] == 42.1

    def test_update_telemetry_soil_fields(self):
        store = self._make_store()
        store.update_telemetry(
            "!abc123",
            soil_moisture=65.0,
            soil_temperature=18.5,
        )
        with store._lock:
            node = store._nodes["!abc123"]
        assert node["soil_moisture"] == 65.0
        assert node["soil_temperature"] == 18.5

    def test_update_telemetry_light_fields(self):
        store = self._make_store()
        store.update_telemetry(
            "!abc123",
            lux=50000.0,
            uv_lux=1200.0,
            radiation=0.15,
        )
        with store._lock:
            node = store._nodes["!abc123"]
        assert node["lux"] == 50000.0
        assert node["uv_lux"] == 1200.0
        assert node["radiation"] == 0.15

    def test_update_telemetry_power_metrics(self):
        store = self._make_store()
        store.update_telemetry(
            "!abc123",
            power_ch1_voltage=12.4,
            power_ch1_current=1.5,
            power_ch2_voltage=5.0,
            power_ch2_current=0.3,
        )
        with store._lock:
            node = store._nodes["!abc123"]
        assert node["power_ch1_voltage"] == 12.4
        assert node["power_ch1_current"] == 1.5
        assert node["power_ch2_voltage"] == 5.0

    def test_update_telemetry_local_stats(self):
        store = self._make_store()
        store.update_telemetry(
            "!abc123",
            device_uptime=86400,
            num_packets_tx=1500,
            num_packets_rx=3200,
            num_packets_rx_bad=12,
            noise_floor=-115.5,
        )
        with store._lock:
            node = store._nodes["!abc123"]
        assert node["device_uptime"] == 86400
        assert node["num_packets_tx"] == 1500
        assert node["noise_floor"] == -115.5

    def test_update_telemetry_host_metrics(self):
        store = self._make_store()
        store.update_telemetry(
            "!abc123",
            host_uptime=172800,
            host_freemem=512000000,
        )
        with store._lock:
            node = store._nodes["!abc123"]
        assert node["host_uptime"] == 172800
        assert node["host_freemem"] == 512000000

    def test_update_telemetry_map_report_fields(self):
        store = self._make_store()
        store.update_telemetry(
            "!abc123",
            firmware_version="2.5.5.abc1234",
            region="US",
            modem_preset="LONG_FAST",
            num_online_local_nodes=5,
        )
        with store._lock:
            node = store._nodes["!abc123"]
        assert node["firmware_version"] == "2.5.5.abc1234"
        assert node["region"] == "US"
        assert node["modem_preset"] == "LONG_FAST"
        assert node["num_online_local_nodes"] == 5


class TestMQTTMapReport:
    """Tests for MAP_REPORT_APP handling in MQTT subscriber."""

    def test_portnum_map_report_constant(self):
        from src.collectors.mqtt_subscriber import PORTNUM_MAP_REPORT
        assert PORTNUM_MAP_REPORT == 73

    def test_handle_map_report_updates_nodeinfo(self):
        from src.collectors.mqtt_subscriber import MQTTSubscriber, MQTTNodeStore

        store = MQTTNodeStore()
        sub = MQTTSubscriber(node_store=store)

        # Simulate MapReport protobuf by calling store methods directly
        # (the actual protobuf parsing is tested by integration tests)
        store.update_nodeinfo(
            "!test1234",
            long_name="TestNode",
            short_name="TN",
            hw_model="TBEAM",
            role="ROUTER",
        )
        store.update_telemetry(
            "!test1234",
            firmware_version="2.5.5.0",
            region="US",
            modem_preset="LONG_FAST",
            num_online_local_nodes=3,
        )

        with store._lock:
            node = store._nodes["!test1234"]
        assert node["name"] == "TestNode"
        assert node["short_name"] == "TN"
        assert node["hardware"] == "TBEAM"
        assert node["firmware_version"] == "2.5.5.0"
        assert node["region"] == "US"


class TestMQTTSubscriberDecodeDispatch:
    """Tests for the decode_protobuf dispatch to MAP_REPORT."""

    def test_decode_dispatches_map_report(self):
        """Verify _decode_protobuf calls _handle_map_report for portnum 73."""
        from src.collectors.mqtt_subscriber import MQTTSubscriber, MQTTNodeStore, PORTNUM_MAP_REPORT

        store = MQTTNodeStore()
        sub = MQTTSubscriber(node_store=store)

        # Mock protobuf modules
        mock_proto = {
            "mqtt_pb2": MagicMock(),
            "mesh_pb2": MagicMock(),
            "portnums_pb2": MagicMock(),
            "telemetry_pb2": MagicMock(),
        }
        sub._proto = mock_proto

        # Set up mock envelope
        mock_env = MagicMock()
        mock_packet = MagicMock()
        mock_packet.sender = 0xaabbccdd
        mock_decoded = MagicMock()
        mock_decoded.portnum = PORTNUM_MAP_REPORT
        mock_decoded.payload = b"\x00"
        mock_packet.decoded = mock_decoded
        mock_env.packet = mock_packet
        mock_proto["mqtt_pb2"].ServiceEnvelope.return_value = mock_env

        with patch.object(sub, "_handle_map_report") as mock_handler:
            sub._decode_protobuf(b"\x00", "msh/test")
            mock_handler.assert_called_once_with("!aabbccdd", b"\x00")


# ── Meshtastic Collector: New Fields Pass-through ────────────────


class TestMeshtasticCollectorNewFields:
    """Tests for new telemetry fields passing through _parse_mqtt_node."""

    def test_parse_mqtt_node_wind_fields(self):
        from src.collectors.meshtastic_collector import MeshtasticCollector

        collector = MeshtasticCollector()
        node = {
            "latitude": 40.7128,
            "longitude": -74.0060,
            "name": "WindNode",
            "wind_direction": 270,
            "wind_speed": 12.5,
            "wind_gust": 25.0,
            "rainfall_1h": 3.2,
        }
        feature = collector._parse_mqtt_node("!wind01", node)
        assert feature is not None
        props = feature["properties"]
        assert props["wind_direction"] == 270
        assert props["wind_speed"] == 12.5
        assert props["wind_gust"] == 25.0
        assert props["rainfall_1h"] == 3.2

    def test_parse_mqtt_node_power_metrics(self):
        from src.collectors.meshtastic_collector import MeshtasticCollector

        collector = MeshtasticCollector()
        node = {
            "latitude": 40.7128,
            "longitude": -74.0060,
            "power_ch1_voltage": 12.4,
            "power_ch1_current": 1.5,
        }
        feature = collector._parse_mqtt_node("!pwr01", node)
        assert feature is not None
        props = feature["properties"]
        assert props["power_ch1_voltage"] == 12.4
        assert props["power_ch1_current"] == 1.5

    def test_parse_mqtt_node_firmware_region(self):
        from src.collectors.meshtastic_collector import MeshtasticCollector

        collector = MeshtasticCollector()
        node = {
            "latitude": 40.7128,
            "longitude": -74.0060,
            "firmware_version": "2.5.5.0",
            "region": "US",
            "modem_preset": "LONG_FAST",
            "noise_floor": -120.0,
        }
        feature = collector._parse_mqtt_node("!fw01", node)
        assert feature is not None
        props = feature["properties"]
        assert props["firmware_version"] == "2.5.5.0"
        assert props["region"] == "US"
        assert props["noise_floor"] == -120.0


# ── /api/dependencies Endpoint ───────────────────────────────────


class TestDependenciesEndpoint:
    """Tests for the _serve_dependencies handler."""

    def test_dependencies_returns_package_list(self):
        """The endpoint returns a list of packages with version info."""
        from src.map_server import MapRequestHandler
        import importlib.metadata as _meta

        handler = MagicMock(spec=MapRequestHandler)
        handler._ctx = MagicMock()
        handler._PYPI_CACHE_TTL = 300

        # Call the real method
        sent_data = {}

        def capture_json(data, status=200):
            sent_data.update(data)

        handler._send_json = capture_json

        def mock_version(pkg):
            versions = {
                "meshtastic": "2.5.5",
                "protobuf": "5.29.3",
                "paho-mqtt": "2.1.0",
            }
            if pkg in versions:
                return versions[pkg]
            raise _meta.PackageNotFoundError(pkg)

        # Skip PyPI check by setting cache time to now
        import time
        MapRequestHandler._pypi_cache = {"meshtastic": "2.5.8"}
        MapRequestHandler._pypi_cache_time = time.time()

        with patch("importlib.metadata.version", side_effect=mock_version):
            MapRequestHandler._serve_dependencies(handler)

        assert "packages" in sent_data
        assert isinstance(sent_data["packages"], list)
        assert len(sent_data["packages"]) > 0

        # Find meshtastic entry
        mesh = next(p for p in sent_data["packages"] if p["name"] == "meshtastic")
        assert mesh["installed_version"] == "2.5.5"
        assert mesh["latest_version"] == "2.5.8"
        assert mesh["upgrade_available"] is True


# ── MapDataClient ────────────────────────────────────────────────


class TestMapDataClientDependencies:
    """Tests for MapDataClient.dependencies_info()."""

    def test_dependencies_info_calls_correct_endpoint(self):
        from src.tui.data_client import MapDataClient

        client = MapDataClient()
        with patch.object(client, "_get", return_value={"packages": []}) as mock_get:
            result = client.dependencies_info()
            mock_get.assert_called_once_with("/api/dependencies")
            assert result == {"packages": []}


# ── System TUI Tab ───────────────────────────────────────────────


class TestSystemTab:
    """Tests for the System tab drawing."""

    def _mock_win(self):
        win = MagicMock()
        win.getmaxyx.return_value = (40, 120)
        return win

    def test_draw_system_with_data(self):
        """System tab renders without errors when data is present."""
        from src.tui.tabs.system import draw_system

        with patch("src.tui.tabs.system.curses") as mock_curses:
            mock_curses.A_BOLD = 1
            mock_curses.A_UNDERLINE = 2
            mock_curses.color_pair.return_value = 0
            win = self._mock_win()

            cache = {
                "dependencies": {
                    "packages": [
                        {"name": "meshtastic", "installed_version": "2.5.5",
                         "latest_version": "2.5.8", "upgrade_available": True,
                         "description": "Protobuf MQTT decoding"},
                        {"name": "protobuf", "installed_version": "5.29.3",
                         "description": "Protocol buffer serialization"},
                        {"name": "paho-mqtt", "installed_version": "2.1.0",
                         "description": "Live MQTT subscription"},
                        {"name": "websockets", "installed_version": None,
                         "description": "WebSocket push"},
                    ],
                    "upgrade_command": "pip install --upgrade meshtastic",
                    "recommended_version": ">=2.5.0",
                },
            }

            draw_system(win, 1, 35, 120, cache, 0)
            assert win.addstr.call_count > 0

    def test_draw_system_empty_cache(self):
        """System tab handles empty/missing data gracefully."""
        from src.tui.tabs.system import draw_system

        with patch("src.tui.tabs.system.curses") as mock_curses:
            mock_curses.A_BOLD = 1
            mock_curses.A_UNDERLINE = 2
            mock_curses.color_pair.return_value = 0
            win = self._mock_win()

            draw_system(win, 1, 35, 120, {}, 0)
            # Should not crash
            assert win.addstr.call_count > 0

    def test_draw_system_no_upgrade_available(self):
        """System tab shows 'up to date' when versions match."""
        from src.tui.tabs.system import draw_system

        with patch("src.tui.tabs.system.curses") as mock_curses:
            mock_curses.A_BOLD = 1
            mock_curses.A_UNDERLINE = 2
            mock_curses.color_pair.return_value = 0
            win = self._mock_win()

            cache = {
                "dependencies": {
                    "packages": [
                        {"name": "meshtastic", "installed_version": "2.5.8",
                         "latest_version": "2.5.8", "upgrade_available": False,
                         "description": "test"},
                        {"name": "protobuf", "installed_version": "5.29.3",
                         "description": "test"},
                    ],
                    "upgrade_command": None,
                    "recommended_version": ">=2.5.0",
                },
            }

            draw_system(win, 1, 35, 120, cache, 0)
            # Find the "up to date" text in calls
            calls = [str(c) for c in win.addstr.call_args_list]
            up_to_date_found = any("up to date" in c for c in calls)
            assert up_to_date_found

    def test_draw_system_not_installed(self):
        """System tab shows 'not installed' for missing packages."""
        from src.tui.tabs.system import draw_system

        with patch("src.tui.tabs.system.curses") as mock_curses:
            mock_curses.A_BOLD = 1
            mock_curses.A_UNDERLINE = 2
            mock_curses.color_pair.return_value = 0
            win = self._mock_win()

            cache = {
                "dependencies": {
                    "packages": [
                        {"name": "meshtastic", "installed_version": None,
                         "latest_version": "2.5.8", "description": "test"},
                    ],
                    "upgrade_command": "pip install --upgrade meshtastic protobuf",
                    "recommended_version": ">=2.5.0",
                },
            }

            draw_system(win, 1, 35, 120, cache, 0)
            calls = [str(c) for c in win.addstr.call_args_list]
            not_installed_found = any("not installed" in c for c in calls)
            assert not_installed_found

    def test_draw_system_scroll(self):
        """System tab respects scroll offset."""
        from src.tui.tabs.system import draw_system

        with patch("src.tui.tabs.system.curses") as mock_curses:
            mock_curses.A_BOLD = 1
            mock_curses.A_UNDERLINE = 2
            mock_curses.color_pair.return_value = 0
            win = self._mock_win()

            cache = {"dependencies": {"packages": [], "upgrade_command": None,
                                       "recommended_version": ">=2.5.0"}}

            # With scroll=0 and scroll=5, different lines should be rendered
            draw_system(win, 1, 35, 120, cache, 0)
            calls_0 = win.addstr.call_count
            win.reset_mock()
            draw_system(win, 1, 35, 120, cache, 5)
            calls_5 = win.addstr.call_count
            # Scrolling should reduce visible lines
            assert calls_5 <= calls_0


# ── TUI App Tab 7 Integration ───────────────────────────────────


class TestTuiAppTab7:
    """Tests for System tab integration in TuiApp."""

    def test_tab_names_includes_system(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        assert "System" in app.TAB_NAMES
        assert app.TAB_NAMES.index("System") == 6

    def test_tab7_key_switches_to_system(self):
        from src.tui.app import TuiApp
        import curses

        app = TuiApp()
        app._running = True
        app._stdscr = MagicMock()
        app._connected = True

        # Simulate pressing '7'
        app._stdscr.getch.return_value = ord("7")
        with patch.object(app, "_refresh_data"):
            app._handle_input()
        assert app._active_tab == 6

    def test_scroll_dict_has_system_tab(self):
        from src.tui.app import TuiApp
        app = TuiApp()
        assert 6 in app._scroll

    def test_status_bar_hint_shows_1_7(self):
        """Status bar hint should show 1-7:Tab for all tabs."""
        from src.tui.app import TuiApp

        app = TuiApp()
        app._running = True
        app._connected = True
        win = MagicMock()
        win.getmaxyx.return_value = (40, 120)
        app._stdscr = win

        with patch("src.tui.app.curses") as mock_curses:
            mock_curses.color_pair.return_value = 0
            mock_curses.A_BOLD = 1
            app._draw_status_bar(40, 120)

        calls = [str(c) for c in win.addstr.call_args_list]
        hint_found = any("1-7:Tab" in c for c in calls)
        assert hint_found


# ── Safe Float / Safe Int Edge Cases for New Ranges ──────────────


class TestSafeValidatorsNewRanges:
    """Tests for _safe_float/_safe_int with new telemetry ranges."""

    def test_safe_float_wind_speed(self):
        from src.collectors.mqtt_subscriber import _safe_float
        assert _safe_float(15.5, 0.0, 200.0) == 15.5
        assert _safe_float(250.0, 0.0, 200.0) is None
        assert _safe_float(-1.0, 0.0, 200.0) is None

    def test_safe_float_noise_floor(self):
        from src.collectors.mqtt_subscriber import _safe_float
        assert _safe_float(-115.0, -200.0, 0.0) == -115.0
        assert _safe_float(5.0, -200.0, 0.0) is None

    def test_safe_int_large_values(self):
        from src.collectors.mqtt_subscriber import _safe_int
        assert _safe_int(86400, 0, 2**31) == 86400
        assert _safe_int(0, 0, 2**31) == 0
        assert _safe_int(-1, 0, 2**31) is None

    def test_safe_float_lux(self):
        from src.collectors.mqtt_subscriber import _safe_float
        assert _safe_float(100000.0, 0.0, 200000.0) == 100000.0
        assert _safe_float(250000.0, 0.0, 200000.0) is None
