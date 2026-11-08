.DEFAULT_GOAL := help
.NOTPARALLEL:

UV ?= uv
ADB ?= adb
SUDO ?= sudo
LISTEN_HOST ?= 0.0.0.0
HTTP_PORT ?= 80
HTTPS_PORT ?= 443
DNS_PORT ?= 53
DNS_DOH ?= 1
DNS_UPSTREAM ?= 8.8.4.4:53
OFFER_UPDATE ?= 0

-include config.mk

VENV := .venv/bin
HOSTS := quasar.yandex.net quasar.yandex.ru scbh.yandex.net
SOURCE_IP_RE = $(subst .,\.,$(STATION_IP))
REMOTE := $(STATION_IP):5555
export STATION_IP STATION_DEVICE_ID STATION_PLATFORM PROXY_IP

OFFER_OPTIONS :=
ifeq ($(OFFER_UPDATE),1)
OFFER_OPTIONS := --set 'station_offer_manifest=$(CURDIR)/firmware/update_manifest.json' \
  --set 'station_offer_receipt=$(CURDIR)/private/offer_receipt.json'
endif

.PHONY: help setup cert offer proxy dns connect status install install-adb disable-updates restore-adb restore-updates reboot check clean check-station check-config

help:
	@printf '%s\n' \
	  'setup            Install Python dependencies in .venv' \
	  'cert             Issue or renew the local 48-hour TLS certificate' \
	  'offer            Arm one firmware offer from the bundled manifest' \
	  'proxy            Run the HTTP/HTTPS proxy without firmware offers by default' \
	  '                 OFFER_UPDATE=1 enables the armed bundled Mini 2 offer' \
	  'dns              Run DNS overrides with normal forwarding' \
	  'connect          Connect ADB to the selected Station' \
	  'status           Check root, firmware, ADB guard, OTA and running updater' \
	  'install          Install the ADB guard and disable OTA' \
	  'install-adb      Install the persistent ADB guard' \
	  'disable-updates  Disable the native OTA launcher task' \
	  'restore-adb      Restore the native ADB script, keeping activation markers' \
	  'restore-updates  Restore the native OTA launcher task' \
	  'reboot           Reboot the selected Station' \
	  'check            Check source syntax and the firmware manifest' \
	  'clean            Remove the virtual environment and Python caches'

setup:
	$(UV) sync --locked

check-station: setup
	@test -n "$(STATION_IP)" || { echo 'Set STATION_IP in config.mk. Start with config.example.mk.' >&2; exit 1; }
	@$(VENV)/python -c 'import argparse, ipaddress; p = argparse.ArgumentParser(); p.add_argument("STATION_IP", type=ipaddress.IPv4Address); p.parse_args()' "$(STATION_IP)"

check-config: check-station
	@test -n "$(PROXY_IP)" || { echo 'Set PROXY_IP in config.mk to the computer LAN address.' >&2; exit 1; }
	@case "$(STATION_DEVICE_ID)" in ''|YOUR_STATION_DEVICE_ID) echo 'Set STATION_DEVICE_ID in config.mk to the Station device_id.' >&2; exit 1 ;; esac
	@test -n "$(STATION_PLATFORM)" || { echo 'Set STATION_PLATFORM in config.mk to the Quasar platform.' >&2; exit 1; }
	@$(VENV)/python -c 'import argparse, ipaddress; p = argparse.ArgumentParser(); p.add_argument("PROXY_IP", type=ipaddress.IPv4Address); p.parse_args()' "$(PROXY_IP)"

cert: setup
	$(VENV)/python issue_certificate.py

offer: setup check-config
	$(VENV)/python firmware_offer.py --manifest firmware/update_manifest.json \
	  --receipt private/offer_receipt.json --target "$(STATION_IP)" \
	  --device-id "$(STATION_DEVICE_ID)" --platform "$(STATION_PLATFORM)"

proxy: cert check-config
	$(SUDO) $(VENV)/mitmdump \
	  --set 'confdir=$(CURDIR)/private/mitmproxy' \
	  --listen-host "$(LISTEN_HOST)" \
	  --mode 'reverse:https://quasar.yandex.net@$(HTTPS_PORT)' \
	  --mode 'reverse:http://quasar.yandex.net@$(HTTP_PORT)' \
	  $(foreach host,$(HOSTS),--certs '$(host)=$(CURDIR)/private/server.pem') \
	  --scripts '$(CURDIR)/config_proxy.py' \
	  --set keep_host_header=true --set connection_strategy=lazy \
	  --set upstream_cert=false --set ssl_insecure=false --set http2=false \
	  --set flow_detail=0 --set body_size_limit=10m \
	  --set 'block_list=|!~src ^$(SOURCE_IP_RE):|444' \
	  --set 'station_target=$(STATION_IP)' \
	  --set 'station_device_id=$(STATION_DEVICE_ID)' --set 'station_platform=$(STATION_PLATFORM)' \
	  --set 'station_state=$(CURDIR)/private/enable_state.json' $(OFFER_OPTIONS)

dns: setup check-config
	$(SUDO) env STATION_DNS_DOH="$(DNS_DOH)" $(VENV)/python dns_proxy.py \
	  --address "$(LISTEN_HOST)" --port "$(DNS_PORT)" --tcp --upstream "$(DNS_UPSTREAM)" \
	  $(foreach host,$(HOSTS),--intercept '$(host). 5 IN A $(PROXY_IP)') \
	  --all-qtypes --ttl 5 --timeout 5 --log error

connect: check-station
	"$(ADB)" connect "$(REMOTE)"

install:
	$(MAKE) install-adb
	$(MAKE) disable-updates

status install-adb disable-updates restore-adb restore-updates: connect
	$(VENV)/python persistence.py --adb "$(ADB)" --serial "$(REMOTE)" $@

reboot: connect
	"$(ADB)" -s "$(REMOTE)" reboot

check: setup
	$(VENV)/python -c 'import ast; from pathlib import Path; [ast.parse(p.read_text(), filename=str(p)) for p in Path(".").glob("*.py")]'
	$(VENV)/python -c 'from pathlib import Path; import json, subprocess; from urllib.parse import urlsplit; from firmware_offer import validate_manifest; from persistence import SNAPSHOT, UPDATER_PROBE, WRAPPER; [subprocess.run(["sh", "-n"], input=s.encode(), check=True) for s in (SNAPSHOT, UPDATER_PROBE, WRAPPER.format(backup="/system/vendor/quasar/activate_adb.root_original.sh"))]; content = Path("firmware/update_manifest.json").read_bytes(); platform = urlsplit(json.loads(content)["downloadUrl"]).path.split("/")[1]; validate_manifest(content, platform); print("Source syntax and firmware manifest verified")'
	PYTHONDONTWRITEBYTECODE=1 $(VENV)/python -m unittest discover -p 'test_*.py'

clean:
	rm -rf .venv __pycache__
