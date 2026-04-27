# Testing Rules — meshforge-maps

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_meshtastic_collector.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=term-missing
```

## Test Structure

```
tests/                              # 982 tests across 37 files
├── conftest.py                     # Shared fixtures
├── test_base.py                    # BaseCollector, validate_coordinates()
├── test_config.py                  # MapsConfig
├── test_meshtastic_collector.py    # Meshtastic data
├── test_reticulum_collector.py     # RNS/RMAP
├── test_hamclock_collector.py      # OpenHamClock + NOAA
├── test_aredn_collector.py         # AREDN mesh
├── test_mqtt_subscriber.py         # Live MQTT
├── test_noaa_alert_collector.py    # NOAA weather alerts
├── test_aggregator.py              # Multi-source merge
├── test_health_scoring.py          # Composite health
├── test_alert_engine.py            # Threshold rules
├── test_analytics.py               # Growth, heatmap
├── test_node_history.py            # SQLite trajectory
├── test_node_state.py              # State machine
├── test_event_bus.py               # Pub/sub
├── test_websocket_server.py        # Real-time
├── test_reliability.py             # Fault tolerance
└── ...
```

## Key Patterns

### Test collectors with mock HTTP
```python
@patch('urllib.request.urlopen')
def test_meshtastic_fetch(mock_urlopen):
    mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"nodes": []}'
```

### Test coordinate validation
```python
def test_null_island_rejected():
    assert validate_coordinates(0.0, 0.0) == (None, None)

def test_meshtastic_integer_coords():
    lat, lon = validate_coordinates(377749000, -1224194000, convert_int=True)
    assert abs(lat - 37.7749) < 0.001
```

### Test GeoJSON output
```python
def test_make_feature():
    f = make_feature("!abc", 37.7, -122.4, "meshtastic")
    assert f["geometry"]["coordinates"] == [-122.4, 37.7]
```
