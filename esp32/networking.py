"""ESP32 WiFi, MQTT, and HTTP transmission module.

Connects to WiFi, then pushes SensorReading dicts to the Pi hub
via MQTT (preferred) or HTTP POST (fallback).

Power strategy:
  - Connect → transmit → disconnect → deep sleep
  - WiFi is the biggest power draw; minimize connection time

Dependencies (MicroPython):
  - ``network`` (built-in)
  - ``umqtt.simple`` (built-in since MicroPython v1.10)
  - ``urequests`` (install via ``mip`` if not present)
"""

import network
import time
import json

# Lazy imports for optional modules
try:
    from umqtt.simple import MQTTClient
except ImportError:
    MQTTClient = None

try:
    import urequests
except ImportError:
    urequests = None


class NetworkManager:
    """Manages WiFi connection and data transmission for the ESP32 node."""

    def __init__(self, config):
        self.config = config
        self._wlan = None
        self._mqtt = None

    def connect_wifi(self):
        """Connect to WiFi. Returns True on success."""
        self._wlan = network.WLAN(network.STA_IF)
        self._wlan.active(True)
        if self._wlan.isconnected():
            return True
        self._wlan.connect(self.config.WIFI_SSID, self.config.WIFI_PASSWORD)
        # Wait for connection
        for _ in range(self.config.WIFI_TIMEOUT_SECONDS * 2):
            if self._wlan.isconnected():
                print("[net] WiFi connected: {}".format(self._wlan.ifconfig()[0]))
                return True
            time.sleep(0.5)
        print("[net] WiFi connection failed")
        return False

    def disconnect_wifi(self):
        """Disconnect WiFi to save power before deep sleep."""
        if self._wlan and self._wlan.isconnected():
            self._wlan.disconnect()
            self._wlan.active(False)
            print("[net] WiFi disconnected")

    def transmit_mqtt(self, readings_dict):
        """Transmit readings via MQTT. Returns True on success."""
        if MQTTClient is None:
            print("[net] umqtt not available")
            return False
        try:
            topic = self.config.MQTT_TOPIC_TEMPLATE.format(
                station_id=self.config.STATION_ID
            )
            client = MQTTClient(
                client_id="{}_esp32".format(self.config.STATION_ID),
                server=self.config.MQTT_BROKER,
                port=self.config.MQTT_PORT,
                user=self.config.MQTT_USER or None,
                password=self.config.MQTT_PASSWORD or None,
                keepalive=60,
            )
            client.connect()
            payload = json.dumps(readings_dict)
            client.publish(topic.encode(), payload.encode(), qos=self.config.MQTT_QOS)
            client.disconnect()
            print("[net] MQTT TX OK: {} bytes".format(len(payload)))
            return True
        except Exception as e:
            print("[net] MQTT TX failed: {}".format(e))
            return False

    def transmit_http(self, readings_dict):
        """Transmit readings via HTTP POST. Returns True on success."""
        if urequests is None:
            print("[net] urequests not available")
            return False
        try:
            headers = {"Content-Type": "application/json"}
            payload = json.dumps(readings_dict)
            resp = urequests.post(
                self.config.HTTP_ENDPOINT,
                data=payload,
                headers=headers,
                timeout=self.config.HTTP_TIMEOUT_SECONDS,
            )
            ok = 200 <= resp.status_code < 300
            resp.close()
            print("[net] HTTP TX {}: {} bytes".format(
                "OK" if ok else "FAIL", len(payload)))
            return ok
        except Exception as e:
            print("[net] HTTP TX failed: {}".format(e))
            return False

    def transmit(self, readings_dict):
        """Try MQTT first, then HTTP fallback."""
        if self.config.USE_MQTT and self.transmit_mqtt(readings_dict):
            return True
        if self.config.USE_HTTP and self.transmit_http(readings_dict):
            return True
        return False
