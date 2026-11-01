import ipaddress
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from mitmproxy import ctx, exceptions, http

from firmware_offer import NO_UPDATE, FirmwareOffer

logger = logging.getLogger(__name__)
HOSTS = frozenset(("quasar.yandex.net", "quasar.yandex.ru", "scbh.yandex.net"))


def exact_float(token: str) -> float:
    value = float(token)
    if Decimal(token) != Decimal(str(value)) or not Decimal(token).is_finite():
        raise ValueError("Configuration contains a lossy JSON number")
    return value


def patch_config(content: bytes) -> bytes:
    payload = json.loads(content, parse_float=exact_float, parse_constant=exact_float)
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ValueError("Expected a successful configuration response")
    config = payload.get("config")
    if not isinstance(config, dict) or not isinstance(
        config.get("system_config"), dict
    ):
        raise TypeError("Expected system_config")
    system = config["system_config"]
    if "adbd" in system and not isinstance(system["adbd"], dict):
        raise TypeError("Expected an adbd object")
    adbd = system.setdefault("adbd", {})
    adbd.update(remoteActivation=True, network=True)
    if "forceDisable" in adbd:
        adbd["forceDisable"] = False
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


class StationConfig:
    def __init__(self):
        self.offer = None

    def load(self, loader):
        loader.add_option("station_target", str, "", "Selected Station's IPv4 address")
        loader.add_option("station_device_id", str, "", "Selected Station's device_id")
        loader.add_option("station_platform", str, "", "Selected Station's platform")
        loader.add_option(
            "station_state", str, "", "Private path for redacted result metadata"
        )
        loader.add_option("station_offer_manifest", str, "", "Firmware manifest path")
        loader.add_option(
            "station_offer_receipt", str, "", "Existing private one-shot offer receipt"
        )

    def configure(self, _updated):
        if not all(
            (
                ctx.options.station_target,
                ctx.options.station_device_id,
                ctx.options.station_platform,
                ctx.options.station_state,
            )
        ):
            raise exceptions.OptionsError(
                "Set station_target, station_device_id, station_platform and station_state before changing ADB"
            )
        try:
            ipaddress.IPv4Address(ctx.options.station_target)
        except ValueError as error:
            raise exceptions.OptionsError("Invalid station_target") from error
        offer_paths = (
            ctx.options.station_offer_manifest,
            ctx.options.station_offer_receipt,
        )
        if bool(offer_paths[0]) != bool(offer_paths[1]):
            raise exceptions.OptionsError(
                "Firmware offer requires manifest and receipt"
            )
        try:
            self.offer = (
                FirmwareOffer(
                    *offer_paths,
                    ctx.options.station_target,
                    ctx.options.station_device_id,
                    ctx.options.station_platform,
                )
                if offer_paths[0]
                else None
            )
        except (OSError, ValueError, TypeError) as error:
            raise exceptions.OptionsError(
                "Cannot load firmware manifest; check its platform, version and download URLs"
            ) from error
        logger.info(
            "Station proxy configured: target=%s, offer=%s",
            ctx.options.station_target,
            self.offer is not None,
        )

    def selected_source(self, flow):
        return (
            bool(flow.client_conn.peername)
            and flow.client_conn.peername[0] == ctx.options.station_target
        )

    def selected_api(self, flow, path):
        request = flow.request
        return (
            self.selected_source(flow)
            and request.host in HOSTS
            and request.method == "GET"
            and urlsplit(request.path).path == path
            and request.query.get_all("device_id") == [ctx.options.station_device_id]
            and request.query.get_all("platform") == [ctx.options.station_platform]
        )

    def request(self, flow):
        request = flow.request
        try:
            authority = urlsplit("//" + (request.host_header or ""))
            valid_authority = (
                authority.hostname in HOSTS
                and len(request.headers.get_all("Host")) == 1
                and authority.port in (None, 80 if request.scheme == "http" else 443)
                and not authority.username
                and not authority.password
                and not authority.path
                and not authority.query
                and not authority.fragment
                and (
                    not flow.client_conn.sni
                    or flow.client_conn.sni == authority.hostname
                )
            )
        except ValueError:
            valid_authority = False
        if not valid_authority:
            flow.response = http.Response.make(421, b"Unexpected authority")
            return
        # Reverse mode prepopulates the default upstream, not the original Host.
        request.host = authority.hostname
        path = urlsplit(request.path).path
        if self.selected_source(flow) and path == "/generate_204":
            flow.response = http.Response.make(204, b"", {"Cache-Control": "no-store"})
            logger.info(
                "Selected Station connectivity check, host=%s status=204",
                request.host,
            )
            return
        if (
            self.offer is None
            and self.selected_source(flow)
            and path == "/check_updates"
        ):
            flow.response = http.Response.make(
                200,
                NO_UPDATE,
                {"Content-Type": "application/json", "Cache-Control": "no-store"},
            )
            logger.info("Selected Station update check blocked, status=200")
            return
        if request.scheme == "http":
            flow.response = http.Response.make(
                307,
                b"",
                {
                    "Location": "https://" + request.host + request.path,
                    "Cache-Control": "no-store",
                },
            )
            return
        request.port = 443
        if (
            self.offer
            and self.selected_source(flow)
            and request.host in HOSTS
            and urlsplit(request.path).path == "/check_updates"
        ):
            content, reason = NO_UPDATE, "invalid_selectors"
            version = request.query.get("version", "")
            if (
                self.selected_api(flow, "/check_updates")
                and len(request.query.get_all("version")) <= 1
            ):
                content, reason = self.offer.check(version)
            flow.response = http.Response.make(
                200,
                content,
                {
                    "Content-Type": "application/json",
                    "Cache-Control": "no-store",
                },
            )
            shown_version = (
                version
                if 0 < len(version) <= 128
                and all(
                    character
                    in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ._-+"
                    for character in version
                )
                else "unavailable"
            )
            logger.info(
                "Selected Station update response prepared, host=%s status=200 reason=%s firmware=%s",
                request.host,
                reason,
                shown_version,
            )
            return

    def response(self, flow):
        request = flow.request
        if self.selected_source(flow) and request.host in HOSTS and flow.response:
            path = urlsplit(request.path).path
            if path in ("/check_updates", "/get_sync_info", "/generate_204"):
                logger.info(
                    "Selected Station API response, host=%s path=%s status=%s",
                    request.host,
                    path,
                    flow.response.status_code,
                )
        if (
            not self.selected_api(flow, "/get_sync_info")
            or flow.response is None
            or flow.response.status_code != 200
            or flow.response.content is None
        ):
            return
        before = flow.response.content
        try:
            after = patch_config(before)
        except (ValueError, TypeError):
            logger.warning(
                "Selected Station configuration was not modified: unexpected JSON shape"
            )
            return
        try:
            state = {
                "at": datetime.now(UTC).isoformat(),
                "mode": "enable",
                "status": 200,
                "modified": after != before,
            }
            path = Path(ctx.options.station_state)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=path.name + ".", dir=path.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "w") as output:
                    os.fchmod(output.fileno(), 0o600)
                    json.dump(state, output, indent=2)
                    output.write("\n")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as error:
            logger.error("Cannot write Station result metadata: %s", error)
            flow.response = http.Response.make(
                502,
                b"Cannot record Station configuration result",
                {"Cache-Control": "no-store"},
            )
            return
        flow.response.content = after
        for header in ("etag", "content-md5"):
            flow.response.headers.pop(header, None)
        logger.info("Selected Station configuration prepared with ADB enabled")


addons = [StationConfig()]
