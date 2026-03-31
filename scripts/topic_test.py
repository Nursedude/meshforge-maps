#!/usr/bin/env python3
"""Test which MQTT topic prefixes receive messages."""
import paho.mqtt.client as mqtt
import time

counts = {}

def on_connect(c, u, f, rc, p=None):
    print("Connected:", rc)
    c.subscribe("msh/US/HI/2/e/#")
    c.subscribe("msh/US/HI/2/json/#")
    c.subscribe("msh/US/2/e/#")
    c.subscribe("msh/US/2/json/#")
    print("Subscribed to msh/US/HI/... and msh/US/... - waiting 20s...")

def on_message(c, u, msg):
    prefix = "/".join(msg.topic.split("/")[:3])
    counts[prefix] = counts.get(prefix, 0) + 1

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.username_pw_set("meshdev", "large4cats")
c.on_connect = on_connect
c.on_message = on_message
c.connect("mqtt.meshtastic.org", 1883)
c.loop_start()
time.sleep(20)
c.loop_stop()
print("\nMessages by topic prefix:")
for k, v in sorted(counts.items()):
    print("  %s: %d" % (k, v))
print("Total: %d" % sum(counts.values()))
