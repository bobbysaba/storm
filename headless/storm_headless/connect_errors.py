"""Why a broker connection fails, in words a user can act on: shared by
the STORM GUI (network/mqtt_client.py), check_broker.py and the headless
publisher (its log and --check). Standard library and paho only."""
from __future__ import annotations

import socket
import ssl
import threading

import paho.mqtt.client as mqtt

from .mqtt_settings import MQTTSettings

# reasons that may only mean the network is down right now, not a setting
_MAYBE_OFFLINE = ("can't find the host", "no answer from", "nothing is listening", "can't reach")


def describe_connect_error(host: str, port: int, ctx: ssl.SSLContext | None,
                           timeout: float = 5.0) -> str:
    """Why a connection to the broker fails, in words a user can act on:
    connects once more, with the same TLS context, to see what goes wrong."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except ConnectionRefusedError:
        return f"nothing is listening on {host}:{port} (wrong port, or the broker isn't running)"
    except socket.gaierror:
        return f"can't find the host {host!r}"
    except TimeoutError:
        return f"no answer from {host}:{port} (a firewall, or the wrong host)"
    except OSError as e:
        return f"can't reach {host}:{port}: {e.strerror or e}"
    try:
        if ctx is None:
            return f"connected to {host}:{port}, but the MQTT handshake failed (does the broker want TLS?)"
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            # TLS 1.3 sends "certificate required" after the handshake
            tls.settimeout(1.5)
            try:
                tls.recv(1)
            except TimeoutError:
                pass
    except ssl.SSLCertVerificationError as e:
        detail = str(e.verify_message or e)
        if "key usage" in detail or "basic constraints" in detail.lower():
            return ("the CA certificate (ca_cert) isn't marked as a CA; "
                    "Python 3.13 and later need basicConstraints and keyUsage (docs/broker.md)")
        if "mismatch" in detail.lower():
            return f"the broker's certificate isn't for {host!r}: use the name on the certificate as host"
        return ("the broker's certificate isn't trusted: set ca_cert in [mqtt] to the CA "
                f"that signed it ({detail})")
    except ssl.SSLError as e:
        if "CERTIFICATE_REQUIRED" in str(e) or "HANDSHAKE_FAILURE" in str(e):
            return ('the broker wants a client certificate: tls = "mutual" with cert_file and '
                    "key_file in [mqtt]")
        if "WRONG_VERSION_NUMBER" in str(e) or "record layer" in str(e).lower():
            return f"{host}:{port} doesn't speak TLS: set tls = \"none\", or use the broker's TLS port"
        return f"TLS failed: {e.reason or e}"
    except OSError as e:
        return f"the broker closed the connection during TLS: {e.strerror or e}"
    finally:
        sock.close()
    return f"connected to {host}:{port}, but the MQTT handshake failed"


def try_broker(broker: MQTTSettings, client_id: str, timeout: float = 10.0) -> tuple[str, str]:
    """Connect once with *broker*'s login and hang up: ("ok", ...), ("fail",
    why) for a setting to fix (a refused login, TLS), or ("offline", why)
    when the broker can't be reached at all."""
    ctx = broker.tls_context()
    c = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id=client_id, protocol=mqtt.MQTTv5)
    if ctx is not None:
        c.tls_set_context(ctx)
    if broker.username:
        c.username_pw_set(broker.username, broker.password or None)
    answered, result = threading.Event(), []

    def on_connect(client, userdata, flags, reason_code, properties):
        result.append(reason_code)
        answered.set()
    c.on_connect = on_connect
    c.on_disconnect = lambda *a: answered.set()
    try:
        c.connect(broker.host, broker.port, keepalive=30)
        c.loop_start()
        answered.wait(timeout)
    except OSError:
        pass
    finally:
        c.loop_stop()
        try:
            c.disconnect()
        except Exception:
            pass
    if result and not result[0].is_failure:
        return "ok", f"connected to {broker.host}:{broker.port}"
    if result:
        return "fail", f"the broker refused the login: {result[0]}"
    why = describe_connect_error(broker.host, broker.port, ctx)
    return ("offline" if why.startswith(_MAYBE_OFFLINE) else "fail"), why
