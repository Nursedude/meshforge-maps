#!/usr/bin/env python3
"""Quick MQTT connection test for meshforge-maps debugging."""
import paho.mqtt.client as mqtt
import time

def on_connect(c, u, f, rc, p=None):
    print(f"Connected: {rc}")
    c.subscribe("msh/US/2/e/#")
    print("Subscribed to msh/US/2/e/# — waiting 15s for messages...")

def on_disconnect(c, u, f, rc, p=None):
    print(f"Disconnected: {rc}")

def on_message(c, u, msg):
    print(f"Message on {msg.topic} ({len(msg.payload)} bytes)")

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.username_pw_set("meshdev", "large4cats")
c.on_connect = on_connect
c.on_disconnect = on_disconnect
c.on_message = on_message
c.connect("mqtt.meshtastic.org", 1883)
c.loop_start()
time.sleep(15)
c.loop_stop()
print("Done")
