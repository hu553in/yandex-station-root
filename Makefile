.DEFAULT_GOAL := help
.NOTPARALLEL:

SHELL := /bin/bash -euo pipefail

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

ACTIONLINT := bunx github-actionlint
TAPLO := bunx @taplo/cli

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

.PHONY: help
help:
	@printf '%s\n' \
	  'install-deps            Install locked Python dependencies and developer tools' \
	  'install-runtime-deps    Install only the Python dependencies needed by Station tools' \
	  'cert                    Issue or renew the local 48-hour TLS certificate' \
	  'offer                   Arm one firmware offer from the bundled manifest' \
	  'proxy                   Run the HTTP/HTTPS proxy without firmware offers by default' \
	  '                        OFFER_UPDATE=1 enables the armed bundled Mini 2 offer' \
	  'dns                     Run DNS overrides with normal forwarding' \
	  'connect                 Connect ADB to the selected Station' \
	  'status                  Check root, firmware, ADB guard, OTA and running updater' \
	  'install                 Install the ADB guard and disable OTA' \
	  'install-adb             Install the persistent ADB guard' \
	  'disable-updates         Disable the native OTA launcher task' \
	  'restore-adb             Restore the native ADB script, keeping activation markers' \
	  'restore-updates         Restore the native OTA launcher task' \
	  'reboot                  Reboot the selected Station' \
	  'check-source            Check source syntax, embedded shell and firmware manifest' \
	  'test                    Run host tests without contacting a Station' \
	  'lint                    Check formatting and Python lint' \
	  'lint-fix                Apply supported formatting and lint fixes' \
	  'check                   Run all developer checks without contacting a Station' \
	  'check-fix               Apply supported fixes, then run all checks' \
	  'clean                   Remove the virtual environment and Python caches'

.PHONY: install-deps
install-deps:
	$(UV) sync --all-groups --locked

.PHONY: install-runtime-deps
install-runtime-deps:
	$(UV) sync --locked --no-dev --inexact

.PHONY: lint
lint:
	bun fmt --check
	$(TAPLO) fmt --check
	$(UV) run --locked ruff check
	$(UV) run --locked ruff format --check

.PHONY: lint-fix
lint-fix:
	bun fmt
	$(TAPLO) fmt
	$(UV) run --locked ruff check --fix
	$(UV) run --locked ruff format

.PHONY: check-git
check-git:
	$(UV) run --locked prek validate-config prek.toml

.PHONY: check-types
check-types:
	$(UV) run --locked ty check .

.PHONY: check-deps
check-deps:
	$(UV) lock --check
	$(UV) run --locked deptry .

.PHONY: check-vulns
check-vulns:
	$(UV) run --locked pysentry-rs .

.PHONY: check-unused
check-unused:
	$(UV) run --locked vulture

.PHONY: check-security
check-security:
	git ls-files --cached --others --exclude-standard -z -- '*.py' | xargs -0 $(UV) run --locked bandit -c pyproject.toml

.PHONY: check-renovate
check-renovate:
	bunx --package renovate renovate-config-validator --strict --no-global renovate.json

.PHONY: check-workflows
check-workflows:
	$(ACTIONLINT)

.PHONY: check-station
check-station: install-runtime-deps
	@test -n "$(STATION_IP)" || { echo 'Set STATION_IP in config.mk. Start with config.example.mk.' >&2; exit 1; }
	@$(VENV)/python -c 'import argparse, ipaddress; p = argparse.ArgumentParser(); p.add_argument("STATION_IP", type=ipaddress.IPv4Address); p.parse_args()' "$(STATION_IP)"

.PHONY: check-config
check-config: check-station
	@test -n "$(PROXY_IP)" || { echo 'Set PROXY_IP in config.mk to the computer LAN address.' >&2; exit 1; }
	@case "$(STATION_DEVICE_ID)" in ''|YOUR_STATION_DEVICE_ID) echo 'Set STATION_DEVICE_ID in config.mk to the Station device_id.' >&2; exit 1 ;; esac
	@test -n "$(STATION_PLATFORM)" || { echo 'Set STATION_PLATFORM in config.mk to the Quasar platform.' >&2; exit 1; }
	@$(VENV)/python -c 'import argparse, ipaddress; p = argparse.ArgumentParser(); p.add_argument("PROXY_IP", type=ipaddress.IPv4Address); p.parse_args()' "$(PROXY_IP)"

.PHONY: cert
cert: install-runtime-deps
	$(VENV)/python issue_certificate.py

.PHONY: offer
offer: install-runtime-deps check-config
	$(VENV)/python firmware_offer.py --manifest firmware/update_manifest.json \
	  --receipt private/offer_receipt.json --target "$(STATION_IP)" \
	  --device-id "$(STATION_DEVICE_ID)" --platform "$(STATION_PLATFORM)"

.PHONY: proxy
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

.PHONY: dns
dns: install-runtime-deps check-config
	$(SUDO) env STATION_DNS_DOH="$(DNS_DOH)" $(VENV)/python dns_proxy.py \
	  --address "$(LISTEN_HOST)" --port "$(DNS_PORT)" --tcp --upstream "$(DNS_UPSTREAM)" \
	  $(foreach host,$(HOSTS),--intercept '$(host). 5 IN A $(PROXY_IP)') \
	  --all-qtypes --ttl 5 --timeout 5 --log error

.PHONY: connect
connect: check-station
	"$(ADB)" connect "$(REMOTE)"

.PHONY: install
install:
	$(MAKE) install-adb
	$(MAKE) disable-updates

.PHONY: status install-adb disable-updates restore-adb restore-updates
status install-adb disable-updates restore-adb restore-updates: connect
	$(VENV)/python persistence.py --adb "$(ADB)" --serial "$(REMOTE)" $@

.PHONY: reboot
reboot: connect
	"$(ADB)" -s "$(REMOTE)" reboot

.PHONY: check-source
check-source: install-runtime-deps
	$(UV) run --locked --no-dev python check_source.py

.PHONY: test
test:
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --locked --no-dev python -m unittest discover -p 'test_*.py'

.PHONY: check
check: lint check-git check-types check-deps check-vulns check-unused check-security check-source test check-renovate check-workflows

.PHONY: check-fix
check-fix: lint-fix
	$(MAKE) check

.PHONY: clean
clean:
	rm -rf .venv __pycache__
