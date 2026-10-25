# STORM Headless Publisher

Publishes a vehicle's instrument-logger observations to the STORM MQTT broker
without running the STORM GUI. Built for Raspberry Pis (or any small Linux
box) that handle data ingestion on a vehicle.

It does exactly what STORM's VEHICLE mode does with an obs file directory:
tails today's `YYYYMMDD.txt`, and every 5 s publishes the latest row to
`storm/vehicles/{vehicle_id}` with the same JSON payload. Every STORM client
sees it like any other vehicle.

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
curl -fsSL https://raw.githubusercontent.com/bobbysaba/storm/main/headless/install.sh | bash -s -- --vehicle-id probe1 --data-dir /home/storm/mesonet --icon-type mesonet
```

It sets everything up in `~/storm-headless/`:

- `venv/`: a virtualenv with only `storm-headless` and `paho-mqtt`. It
  installs the `headless/` folder, not the rest of STORM.
- `storm-headless.toml`: the config, written from your options.
- `aws/`: where the certificates go (empty to start).
- A systemd service, `storm-headless`. It's installed, but only enabled and
  started once the certificates pass the check.

Run `install.sh --help` to see every option, including `--ref` to install
from a branch and `--local` to install from a checkout.

### Before the certificates are available

Check that the Pi parses the logger file correctly without touching MQTT:

```bash
~/storm-headless/venv/bin/storm-headless -c ~/storm-headless/storm-headless.toml --dry-run
```

You should see one `dry run: storm/vehicles/...` line per publish, holding
the JSON payload. Press Ctrl-C to stop.

### Once the certificates are available

1. Copy the shared AWS IoT certs into `~/storm-headless/aws/`. These are the
   same three files as STORM's `aws/` folder: `storm.pem`, `storm.pem.crt`,
   and `storm-private.pem.key`.
2. Check the setup:

   ```bash
   ~/storm-headless/venv/bin/storm-headless -c ~/storm-headless/storm-headless.toml --check
   ```

   It should end with `ready`. A `warn` about no file for today only means
   the logger hasn't written anything yet today.
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
| `gps_mode` | (none) | `false` (FOFS columns) |
| `poll_s` / `publish_s` | (none) | `1` / `5` |
| `[mqtt] client_id` | `--client-id` | `<vehicle_id>-pi` |
| `[mqtt] cert_dir` | (none) | `aws` (relative to the config file) |

Use `--log-level DEBUG` to log every publish, `--check` to validate the setup and exit, and `--dry-run` to log payloads without connecting.

## For developers

The STORM GUI imports `storm_headless.obs_tail` (file tailing and row
parsing) and `storm_headless.vehicle_payload` (the MQTT wire format) from
this folder. That way the GUI and the Pi can't drift apart. Keep this package
free of Qt and of imports from the rest of the repo.
