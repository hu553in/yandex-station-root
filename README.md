# Yandex Station root

Get administrator access to a Yandex Station through ADB, the tool used to connect to its Linux
system. Keep that access after a reboot and turn off automatic firmware updates.

**All ideas come from [eQuasar](https://git.gay/iwakura/eQuasar). This is just a repack for my own
convenience.**

- **Tested device.** Station Mini 2, model `YNDX-00020`.
- **Tested firmware.** `0.369.1.46.4778262943.20261013.299`. Other models and builds are untested.
- **Known incompatibility.** The tested `.50` build rejects the certificate chain used here.

[Before you start](#1-check-the-station-and-network) · [Computer setup](#2-prepare-the-computer) ·
[Device settings](#3-save-the-device-settings) · [Get root](#4-enable-and-check-adb) ·
[Keep access](#5-keep-access-after-a-reboot) · [Recovery](#factory-reset-and-firmware-recovery) ·
[Troubleshooting](#troubleshooting) · [Commands](#other-commands)

## 1. Check the Station and network

This guide starts with a Station already added to your Yandex account in the **Дом с Алисой** phone
app. Connecting a new Station to ordinary internet can update it to an incompatible build. A new,
unconfigured Station whose firmware you cannot identify is outside the tested path.

Before changing DNS or resetting the Station:

1. Read the model code on its base. Check the full firmware version in its device information in
   **Дом с Алисой**.
2. Save the full Quasar **`device_id`** field exactly as shown in that information. The web app's
   device UUID is a different identifier.
3. Compare them with the tested combination above. For a different model or build, this guide does
   not establish compatibility. Do not continue as though it does.

**Stop if you cannot identify the device or its firmware.** A factory reset is not a guaranteed
rollback to a compatible build. For an already identified Mini 2 that needs activation after a
reset, prepare steps 2 and 3, then use [factory recovery](#factory-reset-and-firmware-recovery).

You also need:

- A Mac or Linux computer, a phone with **Дом с Алисой**, and your Yandex account.
- Access to the router's settings, usually through the address on its label or its app. It must let
  you change DNS for **only the Station**.
- The Station and computer on the same home network, with internet access and no client isolation.
- Incoming TCP ports **80 and 443** on the computer. The optional DNS helper also uses UDP and TCP
  **53**. Allow these from the Station in your firewall, and allow local network access in your VPN.

**If the router only changes DNS for the whole network, stop here.** Use a separate network or a
router that can isolate the Station's DNS. The helper redirects Yandex names for every device that
uses it, but the proxy accepts only the selected Station. Network-wide DNS can therefore break the
setup phone's Yandex requests even when other websites work.

## 2. Prepare the computer

Open **Terminal** on macOS, or a terminal on Linux. Run commands one block at a time. If a command
fails, fix that error before continuing. Skip the tool installation if those tools already work.

<details>
<summary>macOS: install the tools</summary>

Install [Homebrew](https://brew.sh/) if `brew --version` does not work:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the installer's **Next steps** to add `brew` to your shell. Accept the Command Line Tools
installation if prompted. Open a new terminal, then run:

```sh
brew install uv
brew install --cask android-platform-tools
```

</details>

<details>
<summary>Ubuntu 24.04 or newer: install the tools</summary>

```sh
sudo apt-get update
sudo apt-get install -y git make curl ca-certificates adb nano
curl -LsSf https://astral.sh/uv/install.sh | sh
. "$HOME/.local/bin/env"
```

The last two commands use the
[official uv installer](https://docs.astral.sh/uv/getting-started/installation/). For another Linux
distribution, install Git, make, curl, `adb`, uv, and a text editor with its package tools. Native
Windows setup is not covered here.

</details>

Download the project:

```sh
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/hu553in/yandex-station-root.git
cd yandex-station-root
make setup
make check
```

**Checkpoint:** `make check` ends with `Source syntax and firmware manifest verified`. These checks
do not contact the Station. uv installs the locked dependencies in `.venv` and downloads Python 3.13
if needed. You do not need to install Python packages globally.

Run all remaining `make` commands from this folder. In a new terminal, return with:

```sh
cd ~/Projects/yandex-station-root
```

## 3. Save the device settings

Open the router's list of connected devices. Find the Station's current IPv4 address and this
computer's LAN IPv4 address. Keep both addresses fixed during setup, using DHCP reservations if the
router supports them. Use the computer's home-network address, not a VPN or public address.

If the identified Station is already reset and offline, use its DHCP reservation or last-known
address for `STATION_IP`. If you have neither, stop before connecting it to Wi-Fi. Check the address
again during [factory recovery](#factory-reset-and-firmware-recovery).

Create the local settings file once:

```sh
cp config.example.mk config.mk
nano config.mk
```

Replace the example values with yours:

```make
STATION_IP := 192.168.1.50
STATION_DEVICE_ID := YOUR_STATION_DEVICE_ID
PROXY_IP := 192.168.1.10
STATION_PLATFORM := yandexmini_2
```

| Setting             | What to enter                                                         |
| ------------------- | --------------------------------------------------------------------- |
| `STATION_IP`        | The Station's address from the router.                                |
| `STATION_DEVICE_ID` | The full Quasar `device_id` saved in step 1. Replace the placeholder. |
| `PROXY_IP`          | This computer's address on the same network.                          |
| `STATION_PLATFORM`  | Keep `yandexmini_2` for the tested Mini 2.                            |

In nano, press **Ctrl+O**, **Enter**, then **Ctrl+X** to save and exit. Do not overwrite an existing
`config.mk` when returning to this guide. The file and `private/` are ignored by Git.

## 4. Enable and check ADB

### Start the proxy

In the project terminal, run:

```sh
make proxy
```

Enter your computer's administrator password if `sudo` asks. Nothing appears while you type it.
Allow incoming connections if macOS asks. Leave this terminal open while the proxy is listening. If
it reports that a port is already in use, see [troubleshooting](#troubleshooting).

The proxy changes the ADB setting only for matching device, platform, and API requests. It preserves
other configuration fields. **Updates are blocked by default**, with `{"hasUpdate":false}`. No
firmware ZIP or offer is needed for this path.

### Send only the Station's Yandex requests to the proxy

Choose the method your router supports:

<details open>
<summary>Custom DNS server for one device</summary>

Open a second terminal. Replace `192.168.1.10` below with the computer's `PROXY_IP` from
`config.mk`:

```sh
cd ~/Projects/yandex-station-root
make dns LISTEN_HOST=192.168.1.10
```

This binds the helper to the computer's LAN address. On Ubuntu, leave `systemd-resolved` running on
its loopback address so the computer keeps its own DNS.

Leave it running. In the router, set the Station's DNS server to the `PROXY_IP` from `config.mk`. Do
not give the Station a secondary server that bypasses this helper. Leave the phone and computer on
their normal DNS.

The helper sends the three names below to the proxy and returns no IPv6 addresses for them. Other
names use Google DNS over verified HTTPS. Keep internet access available.

</details>

<details>
<summary>Per-device DNS overrides already supported by the router</summary>

For **the Station only**, make these names resolve to `PROXY_IP`:

```text
quasar.yandex.net
quasar.yandex.ru
scbh.yandex.net
```

Return no `AAAA` records for these names, so IPv6 cannot bypass the proxy. This method does not need
`make dns`. The computer must still resolve the names to their real upstream addresses. Do not
disable the router's global DNS rebinding protection.

</details>

### Restart the Station and verify access

For a configured Station, unplug and reconnect its power. Keep the proxy and optional DNS helper
running while the Station starts. Wait for startup to finish, then run in another terminal:

```sh
cd ~/Projects/yandex-station-root
make connect
make status
```

**Checkpoint:** `make status` completes without an error and prints `Firmware`, `ADB`, `OTA`, and
the activation markers. This command requires root ADB. Before installation, `ADB native script` and
`OTA enabled in launcher configuration` are normal.

**Do not continue on `Connection refused`, a TLS error, or a failed status check.** Use
[troubleshooting](#troubleshooting). A `204` connectivity check or a log saying that an ADB response
was prepared does not prove that the Station applied it. If factory activation needs an update, use
the [recovery path](#factory-reset-and-firmware-recovery).

## 5. Keep access after a reboot

With root ADB verified, run:

```sh
make install && make reboot
```

Wait for the Station to finish starting, then run:

```sh
make status
```

**Checkpoint:** the output includes all four lines:

```text
ADB guard installed
OTA disabled in launcher configuration
activate_adb_quasmodrom present
activate_network_adb present
```

If the Station is still starting, wait for it to finish before repeating `make status`. If those
lines are missing, leave the helpers running and inspect the error before retrying the installation.

Now restore the Station's ordinary DNS in the router. Stop the proxy and optional DNS helper with
**Ctrl+C** in each terminal. Restart the Station so it picks up ordinary DNS. Wait for it to start,
then ask Alice a question to check normal operation. Run `make connect` and `make status` again to
check that root access still works without the helpers.

- `make install` runs `install-adb` and `disable-updates`. The ADB guard wraps the native activation
  script. Update disabling removes only the `updatesd` task from the current `launch.json`. The
  reboot applies the launcher change.
- Originals are saved under `/system/vendor/quasar/`. Keep these backups. The installer refuses
  ambiguous backups or unrelated edits and accepts earlier versions of the same ADB guard.
- File-structure checks do not authenticate firmware. `status` reports launcher configuration, not
  whether an updater from the current boot is still running.
- **Network ADB has no key authentication on the tested firmware.** Keep port 5555 inside a trusted
  LAN. Do not forward it from the internet.

**Root setup is complete.** To talk to ChatGPT on the Station, continue with
[ChatGPT Voice for Yandex Station](https://github.com/hu553in/yandex-station-chatgpt-voice).

## Factory reset and firmware recovery

Use this section only for the identified Mini 2. Other models and builds are untested. **A reset
clears Wi-Fi and account settings. It is not a guaranteed firmware downgrade.**

### Reset the Mini 2

Before resetting, save the device information and prepare steps 2 and 3 above. Stop the proxy with
Ctrl+C. Follow the
[official Mini 2 reset instructions](https://alice.yandex.ru/support/ru/station/settings/reset-mini2):

1. Disconnect power.
2. Hold the microphone-off button while reconnecting power.
3. Release it when the light turns yellow, after about five seconds.

**Leave Wi-Fi unconfigured.** Prepare the offer below before connecting the Station in the app. An
old boot can consume the offer if it is still online when you arm it.

### Offer the tested firmware once

The tested Mini 2 factory recovery needed one official update to `.46` before activation completed.
[firmware/update_manifest.json](firmware/update_manifest.json) already contains that offer. You do
not need to download a ZIP for this procedure.

1. Keep the Station powered off or without Wi-Fi.
   [Set up DNS for only the Station](#send-only-the-stations-yandex-requests-to-the-proxy) before
   continuing. If you use the DNS helper, leave it running in its terminal.
2. In the project terminal, run:

   ```sh
   make offer && make proxy OFFER_UPDATE=1
   ```

3. Once the proxy is listening, power on the Station or configure its Wi-Fi through **Дом с
   Алисой**. Keep the proxy, DNS, and internet available throughout activation and the update.
4. Check the router's device list. A reset can change the Station's MAC or IP. If the IP or MAC
   changed, stop the proxy, update `config.mk` and the Station's DNS rule, and follow the retry
   instructions below.
5. After activation, run `make connect` and `make status` from another terminal. Once root works,
   return to [step 5](#5-keep-access-after-a-reboot).

`make offer` creates `private/offer_receipt.json`, bound to the Station IP, `device_id`, platform,
and manifest. Only `OFFER_UPDATE=1` uses it. A valid update check consumes it **before delivery**,
even when the installed version is equal or newer. Delivery does not prove installation.

<details>
<summary>Retry a failed offer or change its settings</summary>

1. Stop the proxy. Keep the Station powered off or without Wi-Fi before arming another offer.
2. Inspect the receipt with `sudo cat private/offer_receipt.json`. Check its binding against
   `config.mk` and the current device information.
3. If you intend another attempt, archive the receipt:

   ```sh
   mv -i private/offer_receipt.json private/offer_receipt.previous.json
   ```

   If asked to overwrite a backup, answer **n** and choose an unused backup name instead. After
   archiving, run `make offer && make proxy OFFER_UPDATE=1`.

Restarting the proxy alone does not rearm an offer. Restart it after changing the manifest. If the
computer's `PROXY_IP` changed, update the Station's DNS rules and restart the DNS helper too.
Ordinary `make proxy` ignores receipts and manifests and blocks updates, as does
`make proxy OFFER_UPDATE=0`.

</details>

<details>
<summary>Where firmware metadata and ZIPs come from</summary>

| Source                                                                                                 | Contents                                                |
| ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------- |
| [Firmware archive](http://144.76.194.29:62891/)                                                        | Metadata and ZIPs grouped by platform and full version. |
| [Tested Mini 2 directory](http://144.76.194.29:62891/yandexmini_2/0.369.1.46.4778262943.20261013.299/) | Matching metadata and `update.zip`.                     |
| [Alternative Mini 2 ZIP](https://dmca.cuddles.rs/yandex/mini2/0.369.1.46.4778262943.20261013.299.zip)  | A community mirror of the tested version.               |

These community archives do not establish root compatibility. The proxy reads **metadata**, and the
Station downloads from the official Yandex HTTPS URLs in that metadata. The kit does not serve local
ZIPs or flash partitions.

For another model, changing a platform name or ZIP filename is insufficient. You need the whole
matching `metadata.json` as the manifest and the correct `STATION_PLATFORM`. Keep every field and
the original official HTTPS URLs. `make offer` validates the selected platform. Community mirror
URLs cannot replace the official URLs in the manifest. This is an untested path.

</details>

## Troubleshooting

Start with the **current Station IP** in the router. Compare it with `STATION_IP` and the bracketed
client IP on proxy connection and TLS error lines. `Selected Station` messages are already filtered
to that IP and do not print it.

<details>
<summary>No proxy requests, or the Station says there is no internet</summary>

Check the current IP, all three DNS names, internet access, firewall ports, and VPN local-network
access. Guest Wi-Fi or client isolation can prevent the Station from reaching the computer. Confirm
that only the Station uses the redirected DNS. Leave the phone on normal DNS.

A `Selected Station connectivity check` with `204` only proves that the proxy answered HTTP locally.
It does not verify HTTPS, upstream internet access, configuration changes, or ADB.

</details>

<details>
<summary>TLS handshake failed with unknown ca</summary>

If the bracketed IP is the Station's, the Station rejected the proxy certificate **before** the
configuration handler ran. Check the model and full firmware version against the compatibility note
at the top. Changing the phone's DNS does not repair the Station's certificate trust.

If the IP belongs to another device, these errors concern that device. Check whether network-wide
DNS redirected it to the proxy.

</details>

<details>
<summary>ADB says Connection refused</summary>

`Selected Station configuration prepared with ADB enabled` means the proxy prepared a response. It
does not prove that the Station received or applied it. Check its current IP, the DNS rules,
internet access, and proxy logs, then run `make connect` and `make status`. If activation stalled
with updates blocked, read [factory recovery](#factory-reset-and-firmware-recovery). Do not treat a
`204` response as proof that port 5555 is listening.

If `/get_sync_info` returns `200` but the configuration message above is missing, recheck
`STATION_DEVICE_ID` and `STATION_PLATFORM` in `config.mk`. Use the full Quasar `device_id`, not the
web app's UUID. If you use a firmware offer, follow
[its retry procedure](#offer-the-tested-firmware-once). Otherwise, restart the proxy, then restart
the Station.

</details>

<details>
<summary>A port is already in use, or a tool is not found</summary>

For a DNS port conflict on Ubuntu, keep `systemd-resolved` running. Bind the helper to `PROXY_IP`
with `make dns LISTEN_HOST=192.168.1.10`, replacing the example with the computer's actual address.
For another service on ports 80 or 443, identify that service before stopping it. Do not change the
Station-facing ports without also routing its traffic to them. For `command not found`, return to
[computer setup](#2-prepare-the-computer). Open a new terminal after installing Homebrew or uv and
follow the installer's PATH instructions.

</details>

<details>
<summary>An interrupted installation leaves a lock or backup error</summary>

Confirm that no installer is running and inspect the current state with `make status`. For ambiguous
backups or unrelated edits, keep the files and investigate instead of bypassing the check. If only
an empty stale lock remains, replace the example IP with `STATION_IP`, then run:

```sh
adb -s 192.168.1.50:5555 shell rmdir /data/local/tmp/station-root.lock
```

Retry the interrupted command only after checking the previous outcome. Keep the original backups.

</details>

## Other commands

| Command       | Action                                                                      |
| ------------- | --------------------------------------------------------------------------- |
| `make help`   | List all commands.                                                          |
| `make status` | Inspect root access, firmware, the ADB guard, and update launcher settings. |
| `make check`  | Check source syntax and the local manifest without contacting a Station.    |
| `make clean`  | Remove `.venv` and Python caches. Keep settings and private files.          |

<details>
<summary>Restore the original ADB script and automatic updates</summary>

```sh
make restore-updates && make restore-adb && make reboot
```

Restoration uses saved originals and refuses unrelated edits. Activation markers remain after
restoring the ADB script. A later cloud configuration can deactivate ADB again.

</details>

<details>
<summary>Tool overrides, certificates, and local files</summary>

- Override `UV`, `ADB`, or `SUDO` when needed. For unprivileged local checks, set `SUDO=` and ports
  above 1024 with `HTTP_PORT`, `HTTPS_PORT`, and `DNS_PORT`. `LISTEN_HOST` defaults to `0.0.0.0`.
- Add `DNS_DOH=0` to the DNS-helper command to forward other queries over TCP instead of HTTPS.
  `DNS_UPSTREAM` defaults to `8.8.4.4:53`.
- `make cert` issues a 48-hour certificate. `make proxy` renews it, keeping the key. The bundled
  `certs/issuer.*` files are public compromised CA material from
  [Fluepke's disclosure](https://github.com/Fluepke/emudrahahahahaha). **Do not import that CA into
  the computer's or phone's trust store.**
- `private/enable_state.json` records configuration-change metadata without authorization headers or
  the configuration body. `pyproject.toml` declares dependencies and `uv.lock` pins them.

</details>
