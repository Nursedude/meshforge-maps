"""Tests for AREDN LQM neighbor resolution."""

import pytest

from src.collectors.aredn_collector import AREDNCollector


class TestLQMNeighborParsing:
    """Tests for _parse_lqm_neighbor()."""

    @pytest.fixture
    def collector(self):
        return AREDNCollector(cache_ttl_seconds=0)

    def test_parse_valid_neighbor(self, collector):
        neighbor = {
            "name": "KN6PLV-SECTOR1",
            "snr": 25,
            "quality": 100,
            "tx_quality": 98,
            "rx_quality": 100,
            "type": "RF",
        }
        result = collector._parse_lqm_neighbor(neighbor, "KN6PLV-HAP")
        assert result is not None
        assert result["source"] == "KN6PLV-HAP"
        assert result["target"] == "KN6PLV-SECTOR1"
        assert result["snr"] == 25.0
        assert result["quality"] == 100
        assert result["link_type"] == "RF"
        assert result["network"] == "aredn"

    def test_parse_neighbor_no_name(self, collector):
        result = collector._parse_lqm_neighbor({"snr": 10}, "source")
        assert result is None

    def test_parse_neighbor_empty_name(self, collector):
        result = collector._parse_lqm_neighbor({"name": ""}, "source")
        assert result is None

    def test_parse_blocked_neighbor(self, collector):
        result = collector._parse_lqm_neighbor(
            {"name": "blocked-node", "blocked": True, "snr": 5},
            "source",
        )
        assert result is None

    def test_parse_dtd_link(self, collector):
        result = collector._parse_lqm_neighbor(
            {"name": "local-switch", "type": "DTD", "quality": 100},
            "source",
        )
        assert result is not None
        assert result["link_type"] == "DTD"

    def test_parse_tunnel_link(self, collector):
        result = collector._parse_lqm_neighbor(
            {"name": "remote-node", "type": "TUN", "quality": 80},
            "source",
        )
        assert result is not None
        assert result["link_type"] == "TUN"

    def test_parse_invalid_snr(self, collector):
        result = collector._parse_lqm_neighbor(
            {"name": "node", "snr": "bad"},
            "source",
        )
        assert result is not None
        assert "snr" not in result  # None values stripped

    def test_parse_invalid_quality(self, collector):
        result = collector._parse_lqm_neighbor(
            {"name": "node", "quality": 150},  # > 100
            "source",
        )
        assert result is not None
        assert "quality" not in result  # Invalid quality stripped

    def test_parse_quality_negative(self, collector):
        result = collector._parse_lqm_neighbor(
            {"name": "node", "quality": -1},
            "source",
        )
        assert result is not None
        assert "quality" not in result

    def test_parse_minimal_neighbor(self, collector):
        result = collector._parse_lqm_neighbor({"name": "minimal"}, "src")
        assert result is not None
        assert result["source"] == "src"
        assert result["target"] == "minimal"
        assert result["network"] == "aredn"


class TestLQMTopologyLinks:
    """Tests for get_topology_links() coordinate resolution."""

    @pytest.fixture
    def collector(self):
        return AREDNCollector(cache_ttl_seconds=0)

    def test_empty_topology(self, collector):
        assert collector.get_topology_links() == []

    def test_resolved_links(self, collector):
        collector._lqm_links = [
            {"source": "nodeA", "target": "nodeB", "snr": 20.0, "network": "aredn"},
        ]
        collector._node_coords = {
            "nodeA": (35.0, 139.0),
            "nodeB": (35.1, 139.1),
        }
        links = collector.get_topology_links()
        assert len(links) == 1
        link = links[0]
        assert link["source_lat"] == 35.0
        assert link["source_lon"] == 139.0
        assert link["target_lat"] == 35.1
        assert link["target_lon"] == 139.1

    def test_unresolved_links_included(self, collector):
        collector._lqm_links = [
            {"source": "nodeA", "target": "nodeB", "snr": 10.0, "network": "aredn"},
        ]
        collector._node_coords = {
            "nodeA": (35.0, 139.0),
            # nodeB has no coordinates
        }
        links = collector.get_topology_links()
        assert len(links) == 1
        # Should be included but without resolved coordinates
        assert "source_lat" not in links[0]

    def test_lqm_links_persist_across_calls(self, collector):
        collector._lqm_links = [
            {"source": "a", "target": "b", "network": "aredn"},
            {"source": "b", "target": "c", "network": "aredn"},
        ]
        collector._node_coords = {}
        assert len(collector.get_topology_links()) == 2


class TestAREDNSysinfoLQMIntegration:
    """Integration tests for LQM parsing from sysinfo responses."""

    def test_parse_sysinfo_with_lqm(self, sample_aredn_sysinfo):
        """Verify LQM entries are parsed from full sysinfo response."""
        collector = AREDNCollector(cache_ttl_seconds=0)
        # Simulate what _fetch_from_node does with LQM
        lqm = sample_aredn_sysinfo.get("lqm", [])
        node_name = sample_aredn_sysinfo.get("node", "test")
        links = []
        for neighbor in lqm:
            link = collector._parse_lqm_neighbor(neighbor, node_name)
            if link:
                links.append(link)
        assert len(links) == 2
        assert links[0]["source"] == "KN6PLV-HAP"
        assert links[0]["target"] == "KN6PLV-SECTOR1"
        assert links[0]["snr"] == 25.0
        assert links[1]["target"] == "AB1CDE-OMNI"
        assert links[1]["snr"] == 18.0
