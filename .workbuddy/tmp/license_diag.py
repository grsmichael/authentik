"""容器内诊断：定位自签许可证校验失败的具体环节"""
import base64
import json

import jwt
from cryptography.x509 import load_der_x509_certificate, load_pem_x509_certificate

AUD = "enterprise.goauthentik.io/license/6282f10e-ff93-4ff1-8d2b-456d084cd411"

root = load_pem_x509_certificate(open("authentik/enterprise/public.pem", "rb").read())
print("1) 读到的 Root CA:", root.subject.rfc4514_string())

raw = open("/license.jwt", "r", encoding="utf-8").read().strip()
segs = raw.split(".")


def u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


x5c = json.loads(u(segs[0]))["x5c"]
our = load_der_x509_certificate(u(x5c[0]))
inter = load_der_x509_certificate(u(x5c[1]))
print("2) x5c[0] 叶子:", our.subject.rfc4514_string())
try:
    our.verify_directly_issued_by(inter)
    print("   leaf<-inter OK")
except Exception as exc:  # noqa: BLE001
    print("   leaf<-inter FAIL", type(exc).__name__, exc)
try:
    inter.verify_directly_issued_by(root)
    print("   inter<-root OK")
except Exception as exc:  # noqa: BLE001
    print("   inter<-root FAIL", type(exc).__name__, exc)

for alg in ("ES384", "ES512"):
    try:
        body = jwt.decode(raw, our.public_key(), algorithms=[alg], audience=AUD)
        print(f"3) pyjwt {alg}: OK -> {body['name']} exp={body['exp']}")
    except Exception as exc:  # noqa: BLE001
        print(f"3) pyjwt {alg}: FAIL {type(exc).__name__}: {exc}")

# 复刻 license.py 的完整流程，看异常从哪里冒出来
from rest_framework.exceptions import ValidationError  # noqa: E402

from authentik.enterprise.license import LicenseKey  # noqa: E402

try:
    parsed = LicenseKey.validate(raw)
    print("4) LicenseKey.validate OK ->", parsed.name, parsed.internal_users)
except ValidationError as exc:
    print("4) LicenseKey.validate ValidationError:", exc.detail)
except Exception as exc:  # noqa: BLE001
    print("4) LicenseKey.validate", type(exc).__name__, exc)
