"""Offer a station firmware manifest once through the normal updater."""

import argparse
import fcntl
import ipaddress
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

NO_UPDATE = b'{"hasUpdate":false}'


def version_parts(value):
    factory = re.fullmatch(
        r"([0-9]{1,12}(?:\.[0-9]{1,12}){5})\.factory-[0-9]{1,3}\.FACTORY\.factory", value
    )
    if factory:
        return (*map(int, factory[1].split(".")), 0)
    if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,12}){6}", value):
        raise ValueError("Unrecognized firmware version")
    return tuple(map(int, value.split(".")))


def validate_manifest(content, platform):
    payload = json.loads(content)
    if not isinstance(payload, dict) or payload.get("hasUpdate") is not True:
        raise ValueError("Expected a firmware manifest with hasUpdate=true")
    if not isinstance(payload.get("version"), str):
        raise TypeError("Expected a firmware version string")
    version_parts(payload["version"])
    if not re.fullmatch(r"[a-z0-9_]+", platform):
        raise ValueError("Set the station's Quasar platform")
    for field in ("downloadUrl", "fallbackUrl"):
        if not isinstance(payload.get(field), str):
            raise TypeError(f"Expected {field} in the firmware manifest")
        url = urlsplit(payload[field])
        if (
            url.scheme != "https"
            or not url.hostname
            or not (
                url.hostname == "quasar.s3.yandex.net"
                or url.hostname.endswith(".svc.cdn.yandex.net")
            )
            or url.username
            or url.password
            or url.port not in (None, 443)
            or url.fragment
            or not url.path.startswith(f"/{platform}/ota/")
            or not url.path.endswith("/" + payload["version"] + ".zip")
        ):
            raise ValueError(
                f"Expected an official HTTPS {field} for platform {platform} "
                f"and version {payload['version']}"
            )
    if type(payload.get("crc32")) is not int or not 0 <= payload["crc32"] <= 0xFFFFFFFF:
        raise ValueError("Expected the updater's crc32 field")
    if not isinstance(payload.get("updateId"), str) or not payload["updateId"]:
        raise ValueError("Expected updateId in the firmware manifest")
    if type(payload.get("critical")) is not bool:
        raise ValueError("Expected a boolean critical field")
    if "fallbackIp" in payload:
        ipaddress.IPv4Address(payload["fallbackIp"])
    return payload


def write_receipt(path, value):
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class FirmwareOffer:
    def __init__(self, manifest, receipt, target, device_id, platform):
        self.content = Path(manifest).read_bytes()
        payload = validate_manifest(self.content, platform)
        self.version = payload["version"]
        self.path = Path(receipt)
        self.binding = {
            "target": target,
            "device_id": device_id,
            "platform": platform,
            "manifest": payload,
        }

    def check(self, version):
        try:
            observed = version_parts(version) if version else None
        except ValueError:
            return NO_UPDATE, "invalid_version"
        try:
            fd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                receipt = json.loads(self.path.read_text())
                if (
                    not isinstance(receipt, dict)
                    or receipt.get("binding") != self.binding
                    or receipt.get("phase") not in ("armed", "spent")
                ):
                    return NO_UPDATE, "invalid_receipt"
                if receipt["phase"] == "spent":
                    return NO_UPDATE, "already_spent"
                offered = observed is None or observed < version_parts(self.version)
                reason = "offer_reserved" if offered else "already_current_or_newer"
                receipt.update(phase="spent", reason=reason, at=datetime.now(UTC).isoformat())
                write_receipt(self.path, receipt)
                return (self.content if offered else NO_UPDATE), reason
        except (OSError, ValueError, TypeError):
            return NO_UPDATE, "receipt_unavailable"

    def arm(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump({"binding": self.binding, "phase": "armed"}, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--target", required=True, type=ipaddress.IPv4Address)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--platform", required=True)
    args = parser.parse_args()
    if not args.device_id.strip():
        parser.error("--device-id must be the selected station's device_id")
    try:
        FirmwareOffer(
            args.manifest, args.receipt, str(args.target), args.device_id, args.platform
        ).arm()
    except FileExistsError:
        parser.exit(
            2,
            "Receipt already exists. Inspect it before deliberately archiving "
            "it for a new attempt.\n",
        )
    except (OSError, ValueError, TypeError) as error:
        parser.exit(
            2,
            f"Cannot arm firmware offer: {error}. "
            "Check the manifest, platform and receipt paths.\n",
        )
    print("Firmware offer armed. Restarting the proxy will not rearm it.")
