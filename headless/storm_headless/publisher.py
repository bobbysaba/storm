import json
import logging
import os
import ssl
import threading

import paho.mqtt.client as mqtt

from .observation import Observation
from .settings import Settings
from .vehicle_payload import PUBLISH_EXPIRY_S, PUBLISH_QOS, build_payload, vehicle_topic

log = logging.getLogger(__name__)

# paho will back off between these bounds on repeated reconnect attempts
_RECONNECT_MIN = 2   # seconds
_RECONNECT_MAX = 30  # seconds


def build_tls_context(settings: Settings) -> ssl.SSLContext:
    """
    Build the mutual-TLS context AWS IoT Core requires.  Raises
    FileNotFoundError / ValueError / ssl.SSLError when a cert file is missing,
    empty, or the cert and key don't match.
    """
    for label, path in (
        ("ca_cert", settings.ca_cert),
        ("cert_file", settings.cert_file),
        ("key_file", settings.key_file),
    ):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"MQTT TLS {label} missing: {str(path)!r}")
        if os.path.getsize(path) == 0:
            raise ValueError(f"MQTT TLS {label} is empty: {str(path)!r}")

    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_verify_locations(cafile=str(settings.ca_cert))
    ctx.load_cert_chain(certfile=str(settings.cert_file), keyfile=str(settings.key_file))
    return ctx


class VehiclePublisher:
    """
    Publishes vehicle observations to the STORM broker with paho-mqtt v2.

    Keeps only the latest observation while the broker is unreachable and
    sends it once the connection comes back, instead of letting paho queue
    and replay a backlog of stale positions.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: mqtt.Client | None = None
        self._lock = threading.Lock()
        self._pending: Observation | None = None
        self._published_count = 0

    @property
    def connected(self) -> bool:
        client = self._client
        return client is not None and client.is_connected()

    @property
    def published_count(self) -> int:
        return self._published_count


    def start(self):
        """Validate certs, then connect asynchronously; paho reconnects on its own."""
        s = self._settings
        ctx = build_tls_context(s)

        c = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=s.effective_client_id,
            protocol=mqtt.MQTTv5,
        )
        c.on_connect    = self._on_connect
        c.on_disconnect = self._on_disconnect

        c.tls_set_context(ctx)
        c.tls_insecure_set(False)

        c.reconnect_delay_set(_RECONNECT_MIN, _RECONNECT_MAX)
        c.connect_async(s.mqtt_host, s.mqtt_port, keepalive=60)
        c.loop_start()
        self._client = c
        log.info("MQTT: connecting to %s:%d (client_id=%r)",
                 s.mqtt_host, s.mqtt_port, s.effective_client_id)

    def stop(self):
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.disconnect()
            client.loop_stop()
        except Exception:
            pass
        log.info("MQTT: disconnected")

    def publish_latest(self, obs: Observation):
        """Publish *obs* now if connected; otherwise hold it until reconnect."""
        with self._lock:
            self._pending = obs
        if self.connected:
            self._flush()


    def _flush(self):
        # take the pending obs under the lock but publish outside it, so this
        # never holds our lock while paho holds its own (on_connect path)
        with self._lock:
            obs, self._pending = self._pending, None
        client = self._client
        if obs is None or client is None:
            return

        payload = json.dumps(build_payload(obs, default_icon=self._settings.icon_type))
        props = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
        props.MessageExpiryInterval = PUBLISH_EXPIRY_S
        info = client.publish(vehicle_topic(obs.vehicle_id), payload.encode(),
                              qos=PUBLISH_QOS, properties=props)

        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("MQTT: publish failed (rc=%s); will retry on reconnect", info.rc)
            with self._lock:
                # keep it unless a newer obs arrived meanwhile
                if self._pending is None:
                    self._pending = obs
            return

        self._published_count += 1
        log.debug("MQTT: published %s %s", vehicle_topic(obs.vehicle_id), payload)

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("MQTT: broker refused connection: %s", reason_code)
            return
        log.info("MQTT: connected (%s)", reason_code)
        self._flush()

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        code = int(reason_code.value) if reason_code is not None else 0
        log.warning("MQTT: disconnected (code=%d); paho will reconnect", code)


class DryRunPublisher:
    """Stand-in that logs payloads instead of connecting (--dry-run)."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._published_count = 0

    connected = True

    @property
    def published_count(self) -> int:
        return self._published_count

    def start(self):
        log.info("dry run: not connecting to MQTT")

    def stop(self):
        pass

    def publish_latest(self, obs: Observation):
        payload = build_payload(obs, default_icon=self._settings.icon_type)
        self._published_count += 1
        log.info("dry run: %s %s", vehicle_topic(obs.vehicle_id), json.dumps(payload))
