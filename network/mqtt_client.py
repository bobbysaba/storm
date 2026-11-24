
import logging
import threading

import paho.mqtt.client as mqtt
from PyQt6.QtCore import QObject, pyqtSignal

from headless.storm_headless.connect_errors import describe_connect_error
from headless.storm_headless.mqtt_settings import MQTTSettings
from network import topics

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
        connect_failed(str)            couldn't reach the broker or finish TLS,
                                       and why (describe_connect_error)
        subscribe_refused(str, str)    the broker refused a subscription:
                                       topic filter, reason (not authorized)
        message_received(str, bytes)   topic, raw payload bytes
    """

    connected        = pyqtSignal()
    disconnected     = pyqtSignal(int)
    connect_failed   = pyqtSignal(str)
    subscribe_refused = pyqtSignal(str, str)
    message_received = pyqtSignal(str, bytes)

    def __init__(self, client_id: str = "", read_only: bool = False,
                 topic_root: str = topics.DEFAULT_ROOT, daily_reset_utc: int = 8, parent=None):
        """read_only drops every publish: some brokers (AWS IoT among them)
        disconnect a client that publishes where its login may not.
        topic_root and daily_reset_utc are the [mqtt] settings the sync
        modules publish with."""
        super().__init__(parent)
        self._client_id = client_id
        self._read_only = read_only
        self.topic_root = topic_root
        self.daily_reset_utc = daily_reset_utc
        self._client: mqtt.Client | None = None
        self._lock = threading.Lock()
        # the broker refused the last connect: its reason stands over the
        # disconnect paho reports right after
        self._refused = False
        # (host, port, TLS context) of the broker, to explain a failed connect
        self._target: tuple | None = None
        self._last_failure = ""
        # the broker answered this connection's CONNECT (a drop before that
        # is a connect failure: TLS 1.3 rejects a missing client certificate
        # only after the handshake)
        self._acked = False
        # subscribe message id -> topic filter, to name a refused one
        self._pending_subs: dict[int, str] = {}
        # called with (topic, payload, retained) on paho's thread, for every
        # message (archive/recorder.py in MONITOR mode)
        self._taps: list = []


    def connect_to_broker(self, broker: MQTTSettings):
        """
        Create a new paho client and start connecting asynchronously to
        *broker* (an [mqtt] login from settings/).  Safe to call from the main
        thread.  If already connected, the old client is cleanly stopped first.
        """
        if not broker.configured:
            log.warning("MQTT: host not configured — skipping connection attempt")
            return
        host, port = broker.host, broker.port

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
                protocol=mqtt.MQTTv5,
            )
            c.on_connect    = self._on_connect
            c.on_disconnect = self._on_disconnect
            c.on_message    = self._on_message
            c.on_connect_fail = self._on_connect_fail
            c.on_subscribe  = self._on_subscribe


            try:
                log.info("MQTT: begin connect pipeline (host=%s port=%d tls=%s)", host, port, broker.tls)
                # checks the files first: missing, empty or mismatched raise
                ctx = broker.tls_context()
                self._target = (host, port, ctx)
                if ctx is not None:
                    c.tls_set_context(ctx)
                    c.tls_insecure_set(False)
                    log.info("MQTT: TLS context initialized (%s)", broker.tls)
                if broker.username:
                    c.username_pw_set(broker.username, broker.password or None)

                # paho will automatically retry after disconnect with exponential
                c.reconnect_delay_set(_RECONNECT_MIN, _RECONNECT_MAX)
                log.info("MQTT: calling connect_async")
                c.connect_async(host, port, keepalive=60)
                log.info("MQTT: starting loop thread")
                c.loop_start()
                self._client = c
                log.info("MQTT: connecting to %s:%d (client_id=%r)", host, port, self._client_id)
            except Exception:
                log.exception("MQTT: connect failed during setup")
                self.disconnected.emit(-1)

    def publish(self, topic: str, payload: str, qos: int = 1, expiry: int = 3600, retain: bool = False):
        """Publish a UTF-8 string payload.  No-op if not connected.

        expiry: MQTT v5 MessageExpiryInterval in seconds (default 1 hour).
        retain: if True the broker stores the last message and delivers it to
                new subscribers immediately on subscribe.
        """
        if self._read_only:
            log.debug("MQTT: read-only, not publishing to %s", topic)
            return
        with self._lock:
            client = self._client
            if client is None:
                return
            props = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
            props.MessageExpiryInterval = expiry
            client.publish(topic, payload.encode(), qos=qos, retain=retain, properties=props)

    def subscribe(self, topic: str, qos: int = 1):
        """Subscribe to a topic (wildcards supported).  No-op if not connected."""
        with self._lock:
            client = self._client
            if client is None:
                return
            result = client.subscribe(topic, qos=qos)
            mid = result[1] if isinstance(result, tuple) else None
            if mid is not None:
                self._pending_subs[mid] = topic
        log.debug("MQTT: subscribed to %s", topic)

    def add_tap(self, tap) -> None:
        """Hand every message to tap(topic, payload, retained) as it arrives,
        on paho's thread (no Qt queueing)."""
        self._taps.append(tap)

    def remove_tap(self, tap) -> None:
        if tap in self._taps:
            self._taps.remove(tap)

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


    def _on_connect(self, client, userdata, connect_flags, reason_code, properties):
        self._last_failure = ""
        self._acked = True
        if reason_code.is_failure:
            log.warning("MQTT: broker refused connection: %s", reason_code)
            self._refused = True
            self.disconnected.emit(int(reason_code.value))
        else:
            self._refused = False
            log.info("MQTT: connected (%s)", reason_code)
            self.connected.emit()

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        code = int(reason_code.value) if reason_code is not None else 0
        log.info("MQTT: disconnected (code=%d)", code)
        acked, self._acked = self._acked, False
        if self._refused:
            # the refusal's reason (bad password, not authorized) already went out
            self._refused = False
            return
        if not acked and code and self._target is not None:
            self._on_connect_fail(client, userdata)
            return
        self.disconnected.emit(code)

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        topic = self._pending_subs.pop(mid, "?")
        for code in reason_codes or ():
            if getattr(code, "is_failure", False):
                log.warning("MQTT: the broker refused the subscription to %s: %s", topic, code)
                self.subscribe_refused.emit(topic, str(code))

    def _on_connect_fail(self, client, userdata):
        """paho couldn't connect (it retries on its own): find out why."""
        if self._target is None:
            return
        reason = describe_connect_error(*self._target)
        if reason != self._last_failure:
            log.warning("MQTT: can't connect to %s:%d: %s", self._target[0], self._target[1], reason)
            self._last_failure = reason
        self.connect_failed.emit(reason)

    def _on_message(self, client, userdata, message):
        log.debug("MQTT: rx %s (%d bytes)", message.topic, len(message.payload))
        payload = bytes(message.payload)
        for tap in list(self._taps):
            try:
                tap(message.topic, payload, bool(message.retain))
            except Exception:
                log.exception("MQTT: tap failed on %s", message.topic)
        self.message_received.emit(message.topic, payload)

