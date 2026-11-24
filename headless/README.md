# STORM Headless Publisher

Publishes a vehicle's instrument-logger observations to your team's MQTT broker
without running the STORM GUI. Built for Raspberry Pis (or any small Linux box)
that handle data ingestion on a vehicle. It does what STORM's VEHICLE mode does
with a logger folder, and every STORM client sees the vehicle like any other.
The only dependency is `paho-mqtt`. No Qt, no conda.

Install it on a Pi (Python 3.11+, `git` and `python3-venv`), as the user the
service should run as (not root):

```bash
curl -fsSL https://raw.githubusercontent.com/bobbysaba/storm/main/headless/install.sh | bash -s -- --vehicle-id probe1 --data-dir /home/storm/mesonet --icon-type mesonet --host mqtt.myteam.org
```

`install.sh --help` lists every option. The full guide covers the broker and
TLS settings, taking them from your team's `storm.toml` (`--profile-file`),
testing the logger file without a broker (`--dry-run`), checking the setup
(`--check`), running it as a service, updating, and troubleshooting:
<https://bobbysaba.com/docs/storm/guides/headless-pi/>.

## For developers

The STORM GUI imports `storm_headless.obs_tail` (file tailing and row
parsing), `storm_headless.vehicle_payload` (the MQTT wire format) and
`storm_headless.mqtt_settings` (the `[mqtt]` table) and
`storm_headless.connect_errors` (why a connection fails) from this folder. That
way the GUI and the Pi can't drift apart. Keep this package free of Qt and of
imports from the rest of the repo.
