import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

root = Path(__file__).resolve().parent
private = root / "private"
path = private / "server.pem"
issuer = x509.load_pem_x509_certificate((root / "certs/issuer.pem").read_bytes())
# The bundled issuer has a P-384 key; this script creates RSA leaf keys.
issuer_key = cast(
    "ec.EllipticCurvePrivateKey",
    serialization.load_pem_private_key((root / "certs/issuer.key").read_bytes(), password=None),
)
issuer_public_key = cast("ec.EllipticCurvePublicKey", issuer.public_key())
if issuer_key.public_key().public_numbers() != issuer_public_key.public_numbers():
    raise SystemExit("CA key does not match certs/issuer.pem. Restore the bundled CA files.")
if path.exists():
    leaf_key = cast(
        "rsa.RSAPrivateKey", serialization.load_pem_private_key(path.read_bytes(), password=None)
    )
else:
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

now = datetime.now(UTC)
expires = min(now + timedelta(hours=48), issuer.not_valid_after_utc)
if not issuer.not_valid_before_utc <= now < expires:
    raise SystemExit("CA certificate is outside its validity period. Check the system clock.")
hosts = ("quasar.yandex.net", "quasar.yandex.ru", "scbh.yandex.net")
builder = (
    x509.CertificateBuilder()
    .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
    .issuer_name(issuer.subject)
    .public_key(leaf_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(max(now - timedelta(minutes=5), issuer.not_valid_before_utc))
    .not_valid_after(expires)
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(
        x509.KeyUsage(
            digital_signature=True,
            content_commitment=False,
            key_encipherment=True,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=False,
            crl_sign=False,
            encipher_only=False,
            decipher_only=False,
        ),
        critical=True,
    )
    .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    .add_extension(
        x509.SubjectAlternativeName([x509.DNSName(host) for host in hosts]), critical=False
    )
    .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
    .add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_public_key), critical=False
    )
)
certificate = builder.sign(issuer_key, cast("hashes.SHA256", issuer.signature_hash_algorithm))
certificate.verify_directly_issued_by(issuer)
content = (
    leaf_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    + certificate.public_bytes(serialization.Encoding.PEM)
    + issuer.public_bytes(serialization.Encoding.PEM)
)
private.mkdir(mode=0o700, parents=True, exist_ok=True)
fd, temporary_name = tempfile.mkstemp(dir=private, prefix=".server.")
temporary = Path(temporary_name)
try:
    with os.fdopen(fd, "wb") as output:
        output.write(content)
    temporary.replace(path)
finally:
    temporary.unlink(missing_ok=True)
print("Certificate valid until " + expires.isoformat())
