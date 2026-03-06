# network/mqtt_client.py
# paho-mqtt v2 wrapper.  Runs paho's threaded loop in the background and
# re-emits events as Qt signals so the rest of the app never touches threads.

import logging
import os
import threading
import ssl

import paho.mqtt.client as mqtt
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

# paho will back off between these bounds on repeated reconnect attempts
_RECONNECT_MIN = 2   # seconds
_RECONNECT_MAX = 30  # seconds


class MQTTClient(QObject):
    """
    Thin Qt-friendly wrapper around paho-mqtt v2.

    Signals (emitted from paho's internal thread — Qt queues them safely):
        connected()                    broker handshake complete
        disconnected(int)              lost connection; int = reason code value
        message_received(str, bytes)   topic, raw payload bytes
    """

    connected        = pyqtSignal()
    disconnected     = pyqtSignal(int)
    message_received = pyqtSignal(str, bytes)

    def __init__(self, client_id: str = "", parent=None):
        super().__init__(parent)
        self._client_id = client_id
        self._client: mqtt.Client | None = None
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────────────

    def connect_to_broker(self, host: str, port: int = 8883,
                          use_tls: bool = False,
                          ca_cert: str = "", cert_file: str = "", key_file: str = ""):
        """
        Create a new paho client and start connecting asynchronously.
        Safe to call from the main thread.  If already connected, the old
        client is cleanly stopped first.
        """
        if not host:
            log.warning("MQTT: host not configured — skipping connection attempt")
            return

        with self._lock:
            if self._client is not None:
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception:
                    pass

            c = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self._client_id,
                clean_session=True,
            )
            c.on_connect    = self._on_connect
            c.on_disconnect = self._on_disconnect
            c.on_message    = self._on_message


            try:
                log.info("MQTT: begin connect pipeline (host=%s port=%d tls=%s)", host, port, use_tls)
                if use_tls:
                    if ca_cert or cert_file or key_file:
                        # mTLS required for AWS IoT Core. Pre-validate files first
                        # so bad cert/key inputs fail cleanly instead of crashing later.
                        for label, path in (
                            ("ca_cert", ca_cert),
                            ("cert_file", cert_file),
                            ("key_file", key_file),
                        ):
                            if not path or not os.path.isfile(path):
                                raise FileNotFoundError(f"MQTT TLS {label} missing: {path!r}")
                            if os.path.getsize(path) == 0:
                                raise ValueError(f"MQTT TLS {label} is empty: {path!r}")

                        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
                        ctx.load_verify_locations(cafile=ca_cert)
                        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
                        c.tls_set_context(ctx)
                        c.tls_insecure_set(False)
                        log.info("MQTT: TLS context initialized")
                    else:
                        # Standard server-side TLS only (no client cert/key).
                        c.tls_set()
                        log.info("MQTT: basic TLS initialized")

                # paho will automatically retry after disconnect with exponential
                # back-off between _RECONNECT_MIN and _RECONNECT_MAX seconds
                c.reconnect_delay_set(_RECONNECT_MIN, _RECONNECT_MAX)
                log.info("MQTT: calling connect_async")
                c.connect_async(host, port, keepalive=60)
                log.info("MQTT: starting loop thread")
                c.loop_start()
                self._client = c
                log.info("MQTT: connecting to %s:%d (client_id=%r)", host, port, self._client_id)
            except Exception:
                log.exception("MQTT: connect failed during setup")

    def publish(self, topic: str, payload: str, qos: int = 1, retain: bool = False):
        """Publish a UTF-8 string payload.  No-op if not connected."""
        with self._lock:
            if self._client is None:
                return
        self._client.publish(topic, payload.encode(), qos=qos, retain=retain)

    def subscribe(self, topic: str, qos: int = 1):
        """Subscribe to a topic (wildcards supported).  No-op if not connected."""
        with self._lock:
            if self._client is None:
                return
        self._client.subscribe(topic, qos=qos)
        log.debug("MQTT: subscribed to %s", topic)

    def disconnect(self):
        """Cleanly shut down the paho loop and disconnect."""
        with self._lock:
            if self._client is None:
                return
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        log.info("MQTT: disconnected")

    # ── paho callbacks (called from paho's internal thread) ────────────────────

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("MQTT: broker refused connection: %s", reason_code)
            self.disconnected.emit(int(reason_code.value))
        else:
            log.info("MQTT: connected (%s)", reason_code)
            self.connected.emit()

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        code = int(reason_code.value) if reason_code is not None else 0
        log.info("MQTT: disconnected (code=%d)", code)
        self.disconnected.emit(code)

    def _on_message(self, client, userdata, message):
        log.debug("MQTT: rx %s (%d bytes)", message.topic, len(message.payload))
        self.message_received.emit(message.topic, bytes(message.payload))
