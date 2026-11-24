# STORM Headless Publisher

Publishes a vehicle's instrument-logger observations to the STORM MQTT broker
without running the STORM GUI. Built for Raspberry Pis (or any small Linux
box) that handle data ingestion on a vehicle.

It does exactly what STORM's VEHICLE mode does with an obs file directory:
tails today's logger file (picked by `[obs_columns]` `file`, a
`*YYYYMMDD.csv` by default), and every 5 s publishes the latest row to
`<topic_root>/vehicles/{vehicle_id}` (`storm/vehicles/...` by default) with
the same JSON payload. Every STORM client sees it like any other vehicle. The
topic and payload are described in `docs/wire-format.md` in the STORM
repository.

The only dependency is `paho-mqtt`. No Qt, no conda.

## Important: one publisher per vehicle

If a laptop is riding in the same vehicle, run STORM on it in **MONITOR**
mode. MONITOR shows the vehicle from the broker, including its own ID, and
doesn't publish. If the laptop ran VEHICLE mode with the same vehicle ID,
both machines would publish the same vehicle.

The Pi connects with MQTT client ID `<vehicle_id>-pi` by default, so it never
knocks a laptop's connection off the broker.

## Install on a Pi

Requires Python 3.11+ (Raspberry Pi OS Bookworm ships 3.11), `git` and
`python3-venv`:

```bash
sudo apt install -y git python3-venv
```

Then run the installer as the user the service should run as (not root):

```bash
curl -fsSL https://raw.githubusercontent.com/bobbysaba/storm/main/headless/install.sh | bash -s -- --vehicle-id probe1 --data-dir /home/storm/mesonet --icon-type mesonet --host mqtt.myteam.org
```

`--host` is your team's broker, the same one as `[mqtt] host` in the GUI's
`storm.toml`. Add `--tls mutual` for a broker that wants client certificates
(AWS IoT Core), or `--tls none --port 1883` for one without TLS. You can
leave `--host` off and set it in the config later. If your team's
`storm.toml` sets a `topic_root`, pass the same one with `--topic-root`.

For a setup STORM has a profile for, `--profile NAME` takes the broker from
the repository's `profiles/NAME.toml` instead, so NSSL Pis use:

```bash
curl -fsSL https://raw.githubusercontent.com/bobbysaba/storm/main/headless/install.sh | bash -s -- --vehicle-id probe1 --data-dir /home/storm/mesonet --icon-type mesonet --profile nssl
```

`--host`, `--port`, `--tls` and `--topic-root` given alongside it still win. The profile's
certificate files keep their names but go in `certs/`. A profile with more
than one logger layout (NSSL's has `fofs` and `gps_ka`) uses the first unless
`--layout NAME` picks another, so a GPS Ka truck adds `--layout gps_ka`.

A team whose setup isn't shipped (most are private) passes its own settings
file instead, such as the `storm.toml` its STORM installs use, copied to the
Pi:

```bash
bash install.sh --vehicle-id truck1 --data-dir /home/storm/obs --profile-file ~/team-storm.toml
```

The file's `[mqtt]` table fills in the broker, and its `[obs_columns]`
table (named layouts and `[obs_columns.units]` included) is copied into the
config, for either flag. Other tables are ignored.

It sets everything up in `~/storm-headless/`:

- `venv/`: a virtualenv with only `storm-headless` and `paho-mqtt`. It
  installs the `headless/` folder, not the rest of STORM.
- `storm-headless.toml`: the config, written from your options.
- `certs/`: where any certificates go (empty to start).
- A systemd service, `storm-headless`. It's installed, but only enabled and
  started once the broker settings pass the check.

Run `install.sh --help` to see every option, including `--ref` to install
from a branch and `--local` to install from a checkout.

### Before the broker is set up

Check that the Pi parses the logger file correctly without touching MQTT:

```bash
~/storm-headless/venv/bin/storm-headless -c ~/storm-headless/storm-headless.toml --dry-run
```

You should see one `dry run: storm/vehicles/...` line per publish (or your
`topic_root` in place of `storm`), holding
the JSON payload. Press Ctrl-C to stop.

### Once the broker is set up

1. Check `[mqtt]` in `~/storm-headless/storm-headless.toml`. It has the same
   keys as the GUI's `storm.toml` (see `docs/configuration.md` in the STORM
   repository), plus `client_id`. Put a broker password in `password`, or in
   the service's environment as `STORM_MQTT_PASSWORD`.
   With `--tls mutual`, copy the client certificates into
   `~/storm-headless/certs/` as `storm.pem` (CA), `storm.pem.crt` and
   `storm-private.pem.key` (or the names a `--profile` gave), or point
   `ca_cert`, `cert_file` and `key_file` at other names. For NSSL
   (`--profile nssl`) they are `AmazonRootCA1.pem`, shipped in STORM's
   `providers/nssl/certs/`, and the two team files from
   `providers/nssl/certs/team/`.
2. Check the setup:

   ```bash
   ~/storm-headless/venv/bin/storm-headless -c ~/storm-headless/storm-headless.toml --check
   ```

   It should end with `ready`. It connects to the broker once with the
   login, so a wrong password, an untrusted certificate or a missing client
   certificate fails here with the reason and the fix. A `warn` that the
   broker isn't reachable only means this network can't get to it right
   now (a Pi set up away from the field network); a `warn` about no file
   for today only means the logger hasn't written anything yet today.
3. Start the service, and keep it starting on boot:

   ```bash
   sudo systemctl enable --now storm-headless
   ```

4. Watch the logs:

   ```bash
   journalctl -u storm-headless -f
   ```

   Look for `MQTT: connected`, then a `status:` line every minute showing
   `mqtt=up` and a rising `published=` count.

The service restarts automatically if it exits. Losing the network doesn't
stop it: paho keeps reconnecting, even if the broker was down when the Pi
booted. While the connection is down, only the latest observation is held,
and it's sent on reconnect.

## Update

Re-run the same `curl ... | bash` command. It upgrades the package, keeps
your config, and restarts the service if it's running. You can leave off the
`--vehicle-id` and `--data-dir` options once a config exists.

## Options

| Setting | CLI flag | Default |
|---|---|---|
| `vehicle_id` | `--vehicle-id` | required |
| `data_dir` | `--data-dir` | required |
| `icon_type` | `--icon-type` | `car` |
| `layout` | `--layout` (install only) | the first `[obs_columns.<name>]` layout; picks one when there are several (`gps_mode` is gone) |
| `[obs_columns]` | (none) | a plain `*YYYYMMDD.csv` with an ISO 8601 `time` column; the same keys as the GUI's `storm.toml`, named layouts and `[obs_columns.units]` included ([docs/configuration.md](../docs/configuration.md#obs_columns-vehicle-logger-columns)) |
| `poll_s` / `publish_s` | (none) | `1` / `5` |
| (profile) | `--profile` or `--profile-file` (install only) | none; fills host, port, tls, topic_root and certificate names, and copies `[obs_columns]` |
| `[mqtt] host` | `--host` (install only) | required to publish |
| `[mqtt] port` | `--port` (install only) | `8883`, or `1883` with `tls = "none"` |
| `[mqtt] tls` | `--tls` (install only) | `server` |
| `[mqtt] ca_cert`, `cert_file`, `key_file` | (none) | relative to the config file |
| `[mqtt] username`, `password` | (none) | none; `STORM_MQTT_PASSWORD` wins over `password` |
| `[mqtt] client_id_prefix` | (none) | `""` |
| `[mqtt] topic_root` | `--topic-root` (install only) | `storm`; must match the team's STORM setting |
| `[mqtt] client_id` | `--client-id` | `<client_id_prefix><vehicle_id>-pi` |

Use `--log-level DEBUG` to log every publish, `--check` to validate the setup (and try the broker once) and exit, and `--dry-run` to log payloads without connecting.

If the vehicle never shows up in STORM, the service log
(`journalctl -u storm-headless -n 50`) says why:

- `can't connect to ...: <reason>`: the broker can't be reached or TLS
  failed; the reason says what to change (the same reasons STORM's status
  bar gives, listed in the STORM repository's `docs/broker.md`).
- `broker refused the login`: the username or password is wrong, or the
  login may not use the topics.
- `... has no 'lat' or 'lon' column`: today's logger file doesn't have the
  columns `[obs_columns]` names (or has no header row), so no row can be
  read.

## For developers

The STORM GUI imports `storm_headless.obs_tail` (file tailing and row
parsing), `storm_headless.vehicle_payload` (the MQTT wire format) and
`storm_headless.mqtt_settings` (the `[mqtt]` table) and
`storm_headless.connect_errors` (why a connection fails) from this folder. That way the GUI and the Pi can't drift apart. Keep this package
free of Qt and of imports from the rest of the repo.
