#!/bin/bash
# MeshForge Maps Docker Entrypoint
#
# Maps environment variables to settings.json if no config file exists.
# Existing settings.json is preserved (mount it as a volume to override).

set -e

CONFIG_DIR="$HOME/.config/meshforge/plugins/org.meshforge.extension.maps"
CONFIG_FILE="$CONFIG_DIR/settings.json"

# Only generate config from env vars if no settings.json exists
if [ ! -f "$CONFIG_FILE" ]; then
    mkdir -p "$CONFIG_DIR"
    python3 -c "
import json, os

config = {}

# Map env vars to config keys (only set if env var is present)
env_map = {
    'MQTT_BROKER': ('mqtt_broker', str),
    'MQTT_PORT': ('mqtt_port', int),
    'MQTT_TOPIC': ('mqtt_topic', str),
    'MQTT_USERNAME': ('mqtt_username', str),
    'MQTT_PASSWORD': ('mqtt_password', str),
    'MQTT_TLS': ('mqtt_use_tls', lambda v: v.lower() in ('true', '1', 'yes')),
    'API_KEY': ('api_key', str),
    'HTTP_HOST': ('http_host', str),
    'HTTP_PORT': ('http_port', int),
    'MAP_CENTER_LAT': ('map_center_lat', float),
    'MAP_CENTER_LON': ('map_center_lon', float),
    'MAP_ZOOM': ('map_default_zoom', int),
    'ENABLE_MESHTASTIC': ('enable_meshtastic', lambda v: v.lower() in ('true', '1', 'yes')),
    'ENABLE_RETICULUM': ('enable_reticulum', lambda v: v.lower() in ('true', '1', 'yes')),
    'ENABLE_AREDN': ('enable_aredn', lambda v: v.lower() in ('true', '1', 'yes')),
    'ENABLE_HAMCLOCK': ('enable_hamclock', lambda v: v.lower() in ('true', '1', 'yes')),
    'ENABLE_NOAA_ALERTS': ('enable_noaa_alerts', lambda v: v.lower() in ('true', '1', 'yes')),
    'MESHTASTIC_SOURCE': ('meshtastic_source', str),
    'NOAA_AREA': ('noaa_alerts_area', str),
    'CORS_ORIGIN': ('cors_allowed_origin', str),
}

for env_key, (config_key, converter) in env_map.items():
    val = os.environ.get(env_key)
    if val is not None:
        try:
            config[config_key] = converter(val)
        except (ValueError, TypeError):
            pass

# Always bind to 0.0.0.0 in Docker
config.setdefault('http_host', '0.0.0.0')
config.setdefault('ws_host', '0.0.0.0')

if config:
    with open('$CONFIG_FILE', 'w') as f:
        json.dump(config, f, indent=2)
    print(f'Config generated from environment ({len(config)} keys)')
else:
    print('No env vars set, using defaults')
"
fi

exec "$@"
