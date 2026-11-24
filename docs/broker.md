# Running a broker

STORM's vehicles, annotations, drawings, cones and scan sectors go through an
MQTT broker your team runs. This page sets one up: on your own laptop to try
it, on your own Mosquitto server, or as a hosted service. Pointing STORM at it is in
[configuration.md](configuration.md#mqtt-the-broker).

What STORM needs from a broker:

- **MQTT v5**, for message expiry: shared items clear at the daily reset
  (`daily_reset_utc`).
- **QoS 1**: STORM publishes everything at QoS 1. Some minimal brokers (the
  Python `mqttools` one, for instance) take QoS 0 only and drop the
  connection on STORM's first message.
- **Retained messages, sent on wildcard subscriptions**, so a laptop that
  connects late gets the day's annotations, drawings, cones and scan sectors
  at once. STORM subscribes to `<topic_root>/<kind>/+`.
- **TLS and logins**: one login that can publish, and ideally a read-only
  one for VIEWER.

Traffic is light. Each vehicle publishes its position and observations every
5 seconds (a few hundred bytes); annotations and drawings only change when
someone edits them. A team of 20 vehicles comes to about 1 kB/s.

### What has been tested

Before 3.0 the setup on this page was run end to end on one Mac against a
local **HiveMQ** 4.57 broker (the one-laptop walkthrough below): plain
connections, TLS with logins, client certificates (mutual TLS),
`scripts/check_broker.py`, VEHICLE to VIEWER, a late-joining VIEWER, the
Raspberry Pi publisher, the recorder replayed in ARCHIVE, a broker restart,
drawings, scan sectors, edits and deletes, two teams kept apart by
`topic_root`, and the messages for a wrong login, a refused subscription
and each TLS problem. All of them passed.

Not yet tested by us: Mosquitto (the server steps under Option 1), hosted
brokers (HiveMQ Cloud, EMQX, AWS IoT Core; NSSL's AWS IoT broker ran 2.x
in the field but hasn't been tried with 3.0 yet), a
broker on another machine or across the internet, a real Raspberry Pi, and
Windows or Linux as the broker's host. Any broker that meets the
requirements at the top of this page should work with the right settings
(MQTT v5, QoS 1, retained messages on wildcard subscriptions, TLS and
logins), but HiveMQ is the only one we have run STORM against. If you hit a problem, `check_broker.py`
and [When it doesn't connect](#when-it-doesnt-connect) are the place to
start, and a report helps the next team.

## Trying it on one laptop first

Before setting up a server, you can run a broker on your own laptop and
watch two STORM windows share a vehicle, an annotation and a cone. HiveMQ's
self-managed broker needs only Java, which STORM's `tiles` environment
already has, so this works on a laptop without Homebrew or Docker. (Its
free trial allows 25 connections, plenty for this.)

1. Download HiveMQ from [hivemq.com](https://www.hivemq.com/download/)
   ("Install locally", a zip) and unzip it somewhere outside the STORM
   folder.
2. In the unzipped folder, edit `conf/config.xml` so the broker and its
   Control Center only listen on this laptop:

   ```xml
   <?xml version="1.0" encoding="UTF-8" ?>
   <hivemq xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
           xsi:noNamespaceSchemaLocation="config.xsd">
       <listeners>
           <tcp-listener>
               <port>1883</port>
               <bind-address>127.0.0.1</bind-address>
           </tcp-listener>
       </listeners>
       <control-center>
           <listeners>
               <http>
                   <port>8080</port>
                   <bind-address>127.0.0.1</bind-address>
               </http>
           </listeners>
       </control-center>
   </hivemq>
   ```

3. Start it with Java 21 from the `tiles` environment
   (`conda env create -f envs/tiles.yml` if you don't have it). On macOS or
   Linux:

   ```bash
   conda activate tiles
   export JAVA_HOME="$CONDA_PREFIX/lib/jvm" PATH="$CONDA_PREFIX/lib/jvm/bin:$PATH"
   HIVEMQ_JMX_ENABLED=false ./bin/run.sh
   ```

   `HIVEMQ_JMX_ENABLED=false` matters: otherwise the start script opens an
   unauthenticated management port (9010) to your whole network. It's ready
   when it logs `Started HiveMQ`. On Windows, use `bin\run.bat`.
4. Point STORM at it in `storm.toml` (no login: the trial accepts anyone
   who can reach `127.0.0.1`):

   ```toml
   [mqtt]
   host = "localhost"
   port = 1883
   tls = "none"
   ```

5. `python scripts/check_broker.py`: every check should pass.
6. Start STORM twice. Pick **VEHICLE** in one, with a vehicle ID and a
   logger folder whose file is being written (a vehicle shows up on other
   laptops with its next position, every 5 seconds while it logs); place an
   annotation and a storm cone. Pick **VIEWER** in the other: it shows the
   vehicle, and the annotation and cone even though they were placed before
   it started.

This setup has no logins or TLS, so never open it beyond `127.0.0.1`. For
the team, set up a real broker below, or HiveMQ, EMQX or Mosquitto with
logins and TLS.

## Option 1: Mosquitto on a small server

Any always-on Linux machine with a public address works; the smallest cloud
VM (1 vCPU, 1 GB) is plenty. These steps are for Debian or Ubuntu with a DNS
name, `mqtt.myteam.org` here, pointing at the server.

### 1. Install

Mosquitto 2.0 or newer:

```bash
sudo apt install mosquitto mosquitto-clients certbot
mosquitto -h | head -1
```

Open the MQTT port, and port 80 for Let's Encrypt:

```bash
sudo ufw allow 8883/tcp
sudo ufw allow 80/tcp
```

### 2. A TLS certificate

A free Let's Encrypt certificate means STORM needs no `ca_cert`: laptops
already trust it.

```bash
sudo certbot certonly --standalone -d mqtt.myteam.org
```

Mosquitto can't read certbot's folder, so copy the files where it can, now
and after every renewal, with a deploy hook. Create
`/etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh`:

```sh
#!/bin/sh
# copy the renewed certificate for Mosquitto and restart it
set -e
DOMAIN=mqtt.myteam.org
install -d -m 750 -o root -g mosquitto /etc/mosquitto/certs
install -m 640 -o root -g mosquitto /etc/letsencrypt/live/$DOMAIN/fullchain.pem /etc/mosquitto/certs/
install -m 640 -o root -g mosquitto /etc/letsencrypt/live/$DOMAIN/privkey.pem /etc/mosquitto/certs/
systemctl restart mosquitto
```

```bash
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh
sudo /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh
```

Restarting drops connected laptops for a moment; STORM reconnects on its own.

### 3. Logins

One login for the team (publishes) and one for VIEWER (read-only):

```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd storm
sudo mosquitto_passwd /etc/mosquitto/passwd viewer
```

Each asks for a password. Then the access list, `/etc/mosquitto/acl`:

```
# the team login: everything under the topic root
user storm
topic readwrite storm/#

# the viewer login: read only
user viewer
topic read storm/#
```

The topic root here is `storm`, STORM's default; use your `topic_root` if
you changed it. Several teams can share one broker with a root each
(`teamA/#`, `teamB/#`) and their own logins.

```bash
sudo chown mosquitto: /etc/mosquitto/passwd /etc/mosquitto/acl
sudo chmod 600 /etc/mosquitto/passwd /etc/mosquitto/acl
```

### 4. Configure Mosquitto

Create `/etc/mosquitto/conf.d/storm.conf`:

```
# STORM clients: TLS on 8883
listener 8883
certfile /etc/mosquitto/certs/fullchain.pem
keyfile /etc/mosquitto/certs/privkey.pem

# logins required; what each may do is in the access list
allow_anonymous false
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/acl
```

Retained items must survive a restart, or a reboot mid-day loses the day's
annotations. Debian's and Ubuntu's `/etc/mosquitto/mosquitto.conf` already
has `persistence true` and a `persistence_location`; check that it does.

```bash
sudo systemctl restart mosquitto
sudo systemctl status mosquitto --no-pager
```

If it didn't start, `sudo journalctl -u mosquitto -n 50` says why (usually a
path or a file permission).

### 5. Point STORM at it

`storm.toml`:

```toml
[mqtt]
host = "mqtt.myteam.org"
username = "storm"

[mqtt.viewer]
username = "viewer"
```

`.env`:

```bash
STORM_MQTT_PASSWORD=...
STORM_MQTT_VIEWER_PASSWORD=...
```

TLS (`tls = "server"`) and port 8883 are the defaults. Then check it:

```bash
python scripts/check_broker.py
```

Every check should pass.

### Other setups

- **A certificate of your own** (a private CA): set `ca_cert` in `[mqtt]`
  to the CA file, and give it to every laptop with `storm.toml`. The CA
  certificate must be marked as a CA: Python 3.13 and later (STORM runs on
  3.14) refuse one that isn't, and a plain `openssl req -x509` doesn't mark
  it. This makes a CA for a year and a broker certificate for
  `mqtt.myteam.org` that STORM accepts:

  ```bash
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -keyout ca.key -out ca.pem \
    -subj "/CN=My team MQTT CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign"
  openssl req -newkey rsa:2048 -nodes -keyout broker.key -out broker.csr -subj "/CN=mqtt.myteam.org"
  printf 'subjectAltName=DNS:mqtt.myteam.org\nextendedKeyUsage=serverAuth\n' > broker.ext
  openssl x509 -req -in broker.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 365 \
    -extfile broker.ext -out broker.pem
  ```

  `broker.pem` and `broker.key` go on the broker; `ca.pem` goes to the
  laptops; keep `ca.key` somewhere safe and off the broker.
- **Client certificates instead of passwords**: add `cafile`,
  `require_certificate true` and `use_identity_as_username true` to the
  listener, issue each laptop a certificate from your CA, and set
  `tls = "mutual"` with `cert_file` and `key_file` in `storm.toml`. The ACL's
  user names are then the certificates' common names.
- **Testing on one machine** with Mosquitto: `listener 1883 localhost` and
  `allow_anonymous true`, with `host = "localhost"`, `tls = "none"` in
  `storm.toml`. Never expose that to a network. Without Mosquitto, see
  [Trying it on one laptop first](#trying-it-on-one-laptop-first).

### The recorder

The broker's machine is always on, so it's a natural home for the recorder
that saves the day's traffic for ARCHIVE (`python -m archive.recorder` with
the viewer login). It needs a STORM checkout there. See
[archive-data.md](archive-data.md).

## Option 2: a hosted broker

If you'd rather not run a server, hosted MQTT services work too. Most have a
free tier sized for a small team; check its current limits on connections
and messages.

| Service | Notes |
|---------|-------|
| [HiveMQ Cloud](https://www.hivemq.com/products/mqtt-cloud-broker/) | MQTT v5, TLS, username and password logins with per-login permissions |
| [EMQX Cloud](https://www.emqx.com/en/cloud) | MQTT v5, TLS, username and password logins, access rules |
| [AWS IoT Core](https://aws.amazon.com/iot-core/) | Client certificates (`tls = "mutual"`). Doesn't send retained messages on wildcard subscriptions, so late joiners miss the day's annotations, drawings and cones (scan sectors still arrive). NSSL uses it, with `[mqtt] shared_items_url` (its API's `current.json`) to fill the gap ([nssl.md](nssl.md)) |

Whichever you pick: create a publishing login and a read-only one limited to
`<topic_root>/#`, put the host and logins in `storm.toml` and `.env`, and run
`python scripts/check_broker.py`. It says plainly if the service doesn't
send retained messages on wildcards or doesn't honour expiry.

## Who can see what

- **Anyone with a login sees every vehicle's live position** and its
  observations, plus every annotation, drawing, cone and scan sector. That
  includes the viewer login, so hand it only to people who may see where
  your vehicles are.
- **The broker enforces it, not STORM.** STORM's code is public, so
  `read_only` and `[mqtt.viewer]` are conveniences. Only the broker's access
  list (or certificate policy) stops a login from publishing.
- **The broker doesn't keep vehicle positions**: they aren't retained, so a
  laptop sees a vehicle from its next position on (every 5 seconds while
  it's logging). Shared items are kept until the daily reset, on a broker
  that honours MQTT v5 expiry. Anything that was recording meanwhile (the
  recorder, a broker plugin) keeps its own copy.
- **Recordings you put on the web are public** to anyone with the address.
  An `http(s)` root under `[archive] history` or `vehicle_obs` has no login,
  so it's only for data you're happy to share (NSSL shares its own on
  purpose). Keep private days in a folder instead.

## When it doesn't connect

The status pill says **MQTT OFFLINE** and the status bar gives a reason;
STORM keeps retrying on its own. `python scripts/check_broker.py` gives the
same reasons with a fix for each.
*View > Error Log* (Ctrl+E) has the details.

| What you see | Usually | Fix |
|--------------|---------|-----|
| **NO BROKER** | No `storm.toml`, or no `host` in `[mqtt]` | Copy the team's `storm.toml` into the STORM folder (or set `STORM_CONFIG`) |
| LAUNCH won't start VEHICLE or MONITOR and lists files | A certificate or key named in `storm.toml` isn't on this laptop | Put the files at the paths `storm.toml` gives (`certs/` for your own) |
| `bad username/password` (134) | Wrong or missing password | Check `STORM_MQTT_PASSWORD` in `.env` (and `STORM_MQTT_VIEWER_PASSWORD` for VIEWER); `.env` must be in the STORM folder |
| `not authorized` (135) | A wrong password on some brokers (HiveMQ), or the login may not use these topics, or `topic_root` doesn't match the access list | Check the password as above; make the ACL cover `<topic_root>/#` for that login; check every laptop has the same `topic_root` |
| `setup error (cert/key/path)` | A certificate file is unreadable or not a certificate | Check the paths and that the files aren't empty or swapped |
| `the broker's certificate isn't trusted` | A private CA without `ca_cert`, or the wrong CA file | Set `ca_cert` in `[mqtt]` to the CA that signed the broker's certificate |
| `the CA certificate (ca_cert) isn't marked as a CA` | The CA was made without `basicConstraints` and `keyUsage` | Remake it as in [Other setups](#other-setups) and re-sign the broker's certificate |
| `the broker's certificate isn't for '...'` | `host` isn't the name on the certificate | Use the certificate's name as `host` (or reissue it with that name) |
| `the broker wants a client certificate` | The broker requires client certificates | `tls = "mutual"` with `cert_file` and `key_file` |
| `doesn't speak TLS` | `tls = "server"` against a plain port | `tls = "none"` for that port, or use the broker's TLS port (8883) |
| `nothing is listening on ...` | Wrong port, or the broker isn't running | Check `port` and the broker |
| `connection dropped`, then back to **MQTT OK** | The broker restarted or the network blipped | Nothing: STORM reconnects within seconds and retained items are still there (with Mosquitto's `persistence true`) |
| `no answer from ...` | The port is blocked (firewall, campus network) or the host name is wrong | `nc -vz mqtt.myteam.org 8883` from the laptop; open 8883 on the server |
| Connects, then two laptops keep dropping each other | Both use the same vehicle ID, so the same client ID; a broker allows one connection per client ID | Give every laptop and Pi its own vehicle ID |
| Connected, but other vehicles never appear | Different `topic_root`s, or the login may publish but not subscribe. When the broker refuses the subscriptions, the status bar says `the broker won't send <topic> (not authorized)` | Same `topic_root` everywhere; the ACL needs `readwrite` for the team login |
| Connects, then drops as soon as anything is published | The broker only takes QoS 0 | Use Mosquitto, EMQX, HiveMQ or AWS IoT Core; `check_broker.py` says so |
| Late laptops miss annotations from before they joined | The broker doesn't send retained messages on wildcard subscriptions (AWS IoT) | Use a broker that does; `check_broker.py` tells you |
| Yesterday's annotations are still there | The broker ignores MQTT v5 expiry, or `daily_reset_utc` falls inside your field day | Use Mosquitto 2.x or another v5 broker; move `daily_reset_utc` |
