# Yandex Station root

Get root ADB access through the Station's cloud configuration, keep ADB active after reboot, and
disable automatic firmware updates.

**Credits: All ideas in this project were taken from [eQuasar](https://git.gay/iwakura/eQuasar).
This is just a repack for my own convenience.**

**Tested only on Station Mini 2, YNDX-00020, firmware `0.369.1.46.4778262943.20261013.299`.** Other
models and builds are untested. The tested `.50` build rejects the CA chain used here.

[Setup](#setup) · [Get root](#get-root) · [Keep access](#keep-access) ·
[Firmware](#firmware-and-factory-recovery) · [Restore](#restore) ·
[Troubleshooting](#troubleshooting)

## Setup

You need:

- [uv](https://docs.astral.sh/uv/getting-started/installation/), make, a POSIX shell, and Android
  platform-tools (`adb`).
- A router that can override DNS or assign a custom DNS server for one device.
- A computer reachable by the Station on TCP ports 80 and 443. The optional DNS helper also needs
  UDP and TCP port 53.

Run these commands from the repository root:

```sh
make setup
cp config.example.mk config.mk
```

`make setup` runs `uv sync --locked` as your current user to install dependencies in `.venv`. uv
downloads Python 3.13 automatically if needed. `pyproject.toml` declares the Python version and
dependencies, and `uv.lock` records the resolved versions.

Edit `config.mk`:

| Setting             | Value                                                               |
| ------------------- | ------------------------------------------------------------------- |
| `STATION_IP`        | The Station's current IPv4 address                                  |
| `STATION_DEVICE_ID` | Its Quasar `device_id` from the smart-home app's device information |
| `STATION_PLATFORM`  | Its Quasar platform, `yandexmini_2` for Mini 2                      |
| `PROXY_IP`          | This computer's LAN IPv4 address                                    |

Use the Quasar `device_id`, not the web app's device UUID. Settings and files under `private/` are
ignored by Git.

Check the model code on the base and the full firmware version in the smart-home app's device
information.

## Get root

### 1. Start the proxy

```sh
make proxy
```

Leave it running. It accepts connections only from `STATION_IP` and patches the ADB setting only for
matching `device_id`, platform, and API requests. Other configuration fields stay intact.

By default, update checks return `{"hasUpdate":false}`. The proxy ignores existing offer receipts
and does not load a firmware manifest. Use `OFFER_UPDATE=0` to select this behavior explicitly.

### 2. Redirect the Station's DNS

Point these names to `PROXY_IP` for this Station only:

```text
quasar.yandex.net
quasar.yandex.ru
scbh.yandex.net
```

Return no AAAA records for these names. The proxy computer must resolve their real upstream
addresses.

If the router needs a custom DNS server, run `make dns` in another terminal and assign `PROXY_IP` as
the Station's DNS server. Prevent the Station from bypassing it. Other queries go to Google DNS over
verified HTTPS.

Keep the Station's internet access. Leave other devices' DNS and the router's global DNS rebinding
protection unchanged.

If the router only supports network-wide DNS settings, use a separate network or a router that can
isolate the Station's DNS. This DNS helper redirects the three names for every client that uses it,
but the proxy accepts only `STATION_IP`. Assigning the helper to the whole LAN can break Yandex
requests from the setup phone and other devices, even while other internet services work.

### 3. Connect over ADB

Restart the Station, then run:

```sh
make connect
make status
```

`status` prints the firmware, ADB guard, OTA launcher configuration, and activation markers. For a
factory-reset device, see [Firmware and factory recovery](#firmware-and-factory-recovery). Confirm
root ADB access before continuing. A proxy log about a prepared configuration does not prove that
the Station received or applied it.

## Keep access

Once root ADB works, run:

```sh
make install
make reboot
make status
```

- `install-adb` wraps the native activation script, keeps network ADB active, and creates boot
  markers.
- `disable-updates` removes only the `updatesd` task from the Station's current `launch.json`.
- `install` runs both commands. Reboot applies the launcher change.

The installer saves originals under `/system/vendor/quasar/` and checks file structure before
writing. It preserves existing backups, accepts earlier installations of the same guard, and refuses
ambiguous backups or unrelated edits. These checks do not authenticate the firmware. Keep the
original-file backups.

After confirming ADB access following reboot, restore ordinary DNS and stop both helpers with
Ctrl+C. Network ADB on the tested firmware has no key authentication. Keep port 5555 inside a
trusted LAN.

## Firmware and factory recovery

### Find the firmware and ZIP

| Source                                                                                                | Contents                                                         |
| ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| [Firmware archive](http://144.76.194.29:62891/)                                                       | Firmware metadata and ZIPs grouped by platform and full version. |
| [Tested Mini 2 version](http://144.76.194.29:62891/yandexmini_2/0.369.1.46.4778262943.20261013.299/)  | Metadata matching the bundled manifest, plus `update.zip`.       |
| [Alternative Mini 2 ZIP](https://dmca.cuddles.rs/yandex/mini2/0.369.1.46.4778262943.20261013.299.zip) | A community mirror of that version.                              |

These are community sources. Choose the exact platform and a known compatible build. An archived
package does not establish that this root method works on it.

The proxy reads **update metadata, not a ZIP**.
[firmware/update_manifest.json](firmware/update_manifest.json) already contains the tested Mini 2
offer. The Station downloads the ZIP from the official Yandex URLs in that file. This kit neither
serves a local ZIP nor flashes partitions.

For another model, set `STATION_PLATFORM` and replace the whole manifest with that platform's
`metadata.json`. Keep all fields and the original official HTTPS URLs. Changing the platform or ZIP
filename alone is not enough. `make offer` validates the metadata against the selected platform.
Community mirror URLs cannot replace the official URLs in the manifest.

### Reset the tested Mini 2

A full reset clears Wi-Fi and account settings:

1. Disconnect power.
2. Hold the microphone-off button while reconnecting power.
3. Release the button when the light turns yellow, after about five seconds.

Leave Wi-Fi unconfigured until the firmware offer below is running.

### Offer one update

The tested Mini 2 factory recovery needed one official update to `.46` before activation completed.
Stop the current proxy with Ctrl+C. Before arming the offer, keep the Station powered off or leave
it without Wi-Fi after a full reset. An old boot can otherwise consume the offer before recovery.

Keep the DNS overrides active and leave the optional DNS helper running. Run:

```sh
make offer
make proxy OFFER_UPDATE=1
```

`make offer` creates `private/offer_receipt.json`. Only `OFFER_UPDATE=1` uses it. Once the proxy is
listening, start the Station or configure Wi-Fi in the app after a reset. Keep the proxy, DNS, and
internet access available throughout activation and the update.

The Station may change its MAC or IP after a reset. Check the router's device list when it joins
Wi-Fi. If needed, update `config.mk` and the device-specific DNS rule, then restart the proxy and
rearm the offer as described below before retrying activation.

The receipt binds the Station IP, `device_id`, platform, and manifest. A valid check consumes it
before delivery, even if the Station already has that version or newer. Restarting the proxy does
not rearm it. Delivery does not prove installation.

If setup fails or you change those settings, stop the proxy and inspect the receipt. Deliberately
archive it before running `make offer` again. Restart the proxy to load a changed manifest.

## Restore

```sh
make restore-updates
make restore-adb
make reboot
```

Restore uses saved originals and refuses to overwrite unrelated edits. Restoring the ADB script
keeps its activation markers. A later cloud configuration can deactivate ADB again.

## Troubleshooting

Compare the bracketed client IP on connection and TLS error lines with the Station's current
`STATION_IP`. `Selected Station` lines are already filtered to that IP and do not print it.

| Symptom                                                           | Meaning and next check                                                                                                                                                             |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Selected Station connectivity check` with `204`                  | The proxy answered the HTTP connectivity check locally. This does not verify HTTPS, upstream internet access, configuration changes, or ADB.                                       |
| `Client TLS handshake failed` with `unknown ca` from `STATION_IP` | The Station rejected the proxy certificate before the HTTP configuration handler ran. Check firmware compatibility. Changing the phone's DNS does not fix the Station's TLS trust. |
| TLS errors from another IP                                        | These concern another device. Check whether network-wide DNS also redirected it to the proxy.                                                                                      |
| `Selected Station configuration prepared with ADB enabled`        | The proxy prepared the configuration response. Run `make connect` and `make status` to verify root ADB on the Station.                                                             |
| ADB `Connection refused`                                          | Root ADB access is not established. Check the current Station IP and proxy logs. A `204` or prepared response is not proof that port 5555 is listening.                            |

- If ADB does not connect, check the Station's current IP, the three DNS overrides, internet access,
  and proxy output. Check the firmware against the compatibility note at the top.
- If activation stalls with updates blocked, the tested Mini 2 recovery required the explicit `.46`
  offer above. Other models are untested.
- `status` reports launcher configuration, not whether an updater from the current boot is still
  running.
- If an interrupted installer leaves `/data/local/tmp/station-root.lock`, confirm that no installer
  is running before removing that directory over ADB and retrying. Preserve backups.

## Command notes

- `make help` lists all targets. Proxy and DNS helpers run in the foreground.
- `make check` checks source syntax and the on-disk firmware manifest without contacting a Station.
- `make clean` removes `.venv` and Python caches, preserving settings and private files.
- Override `UV`, `ADB`, or `SUDO` as needed. For unprivileged local runs, use `SUDO=` and ports
  above 1024 through `HTTP_PORT`, `HTTPS_PORT`, and `DNS_PORT`. `LISTEN_HOST` defaults to `0.0.0.0`.
- `make dns DNS_DOH=0` forwards other queries over TCP to `DNS_UPSTREAM`, default `8.8.4.4:53`.
- `make cert` issues a 48-hour TLS certificate. `make proxy` renews it automatically, retaining the
  key. The bundled `certs/issuer.*` files are the publicly disclosed compromised CA material from
  [Fluepke's disclosure](https://github.com/Fluepke/emudrahahahahaha). Do not add this CA to the
  computer's trust store.
- `private/enable_state.json` records configuration changes as metadata without saving authorization
  headers or the configuration body.
