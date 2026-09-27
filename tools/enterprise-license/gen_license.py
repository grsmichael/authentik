#!/usr/bin/env python3
"""自签 authentik 企业版许可证（离线，无需网络、无需官方 key）

原理
----
authentik 的许可证校验在 ``authentik/enterprise/license.py`` 里，是**完全离线**的：

1. ``get_licensing_key()`` 读取相对路径 ``authentik/enterprise/public.pem``
   （容器里是 ``/authentik/enterprise/public.pem``，因为容器 CWD 是 ``/``），
   把它当作 **Root CA 证书** —— 整个信任体系的唯一锚点。
2. 许可证 JWT 的 header 里带 ``x5c``，校验时只取前两个元素：
   ``x5c[0]`` = 叶子证书、``x5c[1]`` = 中间证书；
3. ``our_cert.verify_directly_issued_by(intermediate)``
   ``intermediate.verify_directly_issued_by(root)``
4. 用**叶子证书的公钥**验签 JWT，算法只允许 ``ES384`` / ``ES512``，
   且 ``aud`` 必须严格等于 ``enterprise.goauthentik.io/license/{install_id}``。

所以只要自备一套「根 CA → 中间 CA → 叶子证书」，把 ``public.pem`` 换成自签的根 CA 证书，
再用叶子私钥签许可证 JWT，官方那套校验就会全数通过 —— **一行 authentik 源码都不用改**。

产物
----
  public.pem              Root CA 证书 → 覆盖容器 /authentik/enterprise/public.pem（信任锚）
  license.jwt             许可证 → 填进 authentik 的 Licenses（UI 或 API 导入）
  chain/intermediate.crt.pem
  chain/leaf.crt.pem      中间与叶子证书。有了它们，光拿 leaf 私钥就能再签新票，
                          不必重做整条链（否则只有私钥，重建出的证书与原链不一致）
  private/root.key.pem    根 CA 私钥（离线保管，丢了要重签整条链）
  private/intermediate.key.pem
  private/leaf.key.pem    叶子私钥，许可证就由它签名

用法
----
  python gen_license.py --install-id <install_id> --out-dir ../../deploy/enterprise-pki

install_id 从数据库取：
  docker exec authentik-postgresql psql -U authentik -d authentik -tAc \\
      "select * from authentik_install_id limit 1"

它必须与 JWT 的 ``aud`` 一致，否则 authentik 报 "Invalid Install ID in license"。

图形化界面见同目录 ``ui/``（许可证的生成、签发、发布都可以在界面上完成）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

# 与 authentik 官方许可证一致：ES512 + secp384r1
CURVE = ec.SECP384R1
HASH = hashes.SHA512
JWT_ALG = "ES512"
AUD_PREFIX = "enterprise.goauthentik.io/license"

ROOT_CN = "authentik Enterprise Root CA"
INTER_CN = "authentik Enterprise Issuing CA"
LEAF_CN = "authentik Enterprise License"


def b64u(data: bytes) -> str:
    """base64url，不带 padding —— pyjwt 的 x5c / JWT 段都用这个格式"""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64u(seg: str) -> bytes:
    """反过来：补回 padding"""
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def b64std(data: bytes) -> str:
    """**标准** base64（带 padding，字母表含 + / ）。

    x5c 必须用这一套：license.py 里写的是 `from base64 import b64decode`，
    它按标准字母表解码 —— 若这里用 base64url 编出 - / _ 字符，
    解码时会把这些字符**当成非法字符直接丢弃**，DER 被破坏，
    最终报 "Unable to verify license"。
    """
    return base64.b64encode(data).decode("ascii")


def new_key() -> ec.EllipticCurvePrivateKey:
    """新生成一把 P-384 私钥（注意要实例，不是类）"""
    return ec.generate_private_key(CURVE())


def name_of(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def write(path: Path, text: str) -> None:
    """写文件并强制 LF 行尾（Windows 上 Python 默认转 CRLF，PEM 没必要）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fd:
        fd.write(text)
    print(f"  wrote {path}")


def privkey_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def load_private_key(path: Path) -> ec.EllipticCurvePrivateKey:
    """读一把 PKCS8 私钥"""
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def issue(
    subject: str,
    key: ec.EllipticCurvePrivateKey,
    issuer_cert: x509.Certificate | None,
    issuer_key: ec.EllipticCurvePrivateKey | None = None,
    ca: bool = False,
    years: int = 50,
) -> x509.Certificate:
    """签发一张证书。

    key       —— 这张证书对应的密钥对（公钥会写进证书）
    issuer_* —— 签发者；留空则自签（用 key 自己签自己）
    """
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name_of(subject))
        .issuer_name(issuer_cert.subject if issuer_cert is not None else name_of(subject))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365 * years))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    signer = issuer_key if issuer_cert is not None else key
    return builder.sign(private_key=signer, algorithm=hashes.SHA256())


def make_chain(root_years: int = 50) -> tuple[
    tuple[x509.Certificate, ec.EllipticCurvePrivateKey],
    tuple[x509.Certificate, ec.EllipticCurvePrivateKey],
    tuple[x509.Certificate, ec.EllipticCurvePrivateKey],
]:
    """生成完整的「根 CA → 中间 CA → 叶子证书」三级链。

    返回 ((root_cert, root_key), (inter_cert, inter_key), (leaf_cert, leaf_key))。
    叶子证书不设 CA 约束，它就是用来签许可证 JWT 的那把。
    """
    root_key = new_key()
    root_cert = issue(ROOT_CN, root_key, None, years=root_years)

    inter_key = new_key()
    inter_cert = issue(INTER_CN, inter_key, root_cert, root_key, ca=True, years=root_years)

    leaf_key = new_key()
    leaf_cert = issue(LEAF_CN, leaf_key, inter_cert, inter_key)
    return (root_cert, root_key), (inter_cert, inter_key), (leaf_cert, leaf_key)


def build_license(
    install_id: str,
    leaf_key: ec.EllipticCurvePrivateKey,
    leaf_cert: x509.Certificate,
    inter_cert: x509.Certificate,
    years: int,
    name: str,
    internal_users: int,
    external_users: int,
    flags: list[str] | None = None,
) -> tuple[str, dict]:
    """签一张许可证 JWT。

    返回 (jwt 字符串, payload 字典)。副作用：无 —— 想落到磁盘自己 write。
    """
    now = datetime.now(timezone.utc)
    exp_ts = int((now + timedelta(days=365 * years)).timestamp())
    header = {
        "typ": "JWT",
        "alg": JWT_ALG,
        # x5c 必须用标准 base64（见 b64std 的注释，这里踩过坑）
        "x5c": [
            b64std(leaf_cert.public_bytes(serialization.Encoding.DER)),
            b64std(inter_cert.public_bytes(serialization.Encoding.DER)),
        ],
    }
    payload = {
        "aud": f"{AUD_PREFIX}/{install_id}",
        "exp": exp_ts,
        "name": name,
        "internal_users": internal_users,
        "external_users": external_users,
        "license_flags": flags or [],
    }
    seg_header = b64u(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    seg_payload = b64u(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{seg_header}.{seg_payload}".encode("ascii")
    # pyjwt 的 ES512 == ECDSA(SHA-512) over signing_input，输出 raw r||s
    signature = leaf_key.sign(signing_input, ec.ECDSA(HASH()))
    return signing_input.decode("ascii") + "." + b64u(signature), payload


def verify_license(
    jwt_str: str, root_pem: Path | bytes, expect_install_id: str | None = None
) -> dict:
    """复刻 authentik/enterprise/license.py 的校验顺序，返回逐项结果。

    返回 {"ok": bool, "steps": [{"name", "ok", "detail"}], "payload": {...}}
    """
    steps: list[dict] = []
    segs = jwt_str.split(".")
    if len(segs) != 3:
        return {"ok": False, "steps": [{"name": "JWT 段数", "ok": False, "detail": "应为 3 段"}], "payload": {}}

    def step(name: str, ok: bool, detail: str = "") -> None:
        steps.append({"name": name, "ok": ok, "detail": detail})

    # x5c 用**标准** base64 解，和 license.py 的 b64decode 完全一致
    try:
        header = json.loads(unb64u(segs[0]))
        x5c = header["x5c"]
        our = x509.load_der_x509_certificate(base64.b64decode(x5c[0]))
        inter = x509.load_der_x509_certificate(base64.b64decode(x5c[1]))
        step("解析 x5c", True, f"叶子 {our.subject.rfc4514_string()}")
    except Exception as exc:  # noqa: BLE001
        step("解析 x5c", False, f"{type(exc).__name__}: {exc}")
        return {"ok": False, "steps": steps, "payload": {}}

    try:
        our.verify_directly_issued_by(inter)
        step("叶子 ← 中间", True)
    except Exception as exc:  # noqa: BLE001
        step("叶子 ← 中间", False, type(exc).__name__)

    root_bytes = root_pem.read_bytes() if isinstance(root_pem, Path) else root_pem
    root = x509.load_pem_x509_certificate(root_bytes)
    try:
        inter.verify_directly_issued_by(root)
        step("中间 ← 根 CA", True, root.subject.rfc4514_string())
    except Exception as exc:  # noqa: BLE001
        step("中间 ← 根 CA", False, type(exc).__name__)

    try:
        our.public_key().verify(unb64u(segs[2]), f"{segs[0]}.{segs[1]}".encode(), ec.ECDSA(HASH()))
        step("JWT 验签", True, f"{JWT_ALG}，用叶子证书公钥")
    except Exception as exc:  # noqa: BLE001
        step("JWT 验签", False, type(exc).__name__)

    try:
        payload = json.loads(unb64u(segs[1]))
        audience = f"{AUD_PREFIX}/{expect_install_id}" if expect_install_id else payload.get("aud")
        step(
            "audience",
            payload.get("aud") == audience,
            f"{payload.get('aud')}（期望 {audience}）" if expect_install_id else str(payload.get("aud")),
        )
        step("未过期", payload.get("exp", 0) > datetime.now(timezone.utc).timestamp(),
             datetime.fromtimestamp(payload["exp"], timezone.utc).date().isoformat())
        payload["_steps_ok"] = all(s["ok"] for s in steps)
        return {"ok": all(s["ok"] for s in steps), "steps": steps, "payload": payload}
    except Exception as exc:  # noqa: BLE001
        step("解析 payload", False, f"{type(exc).__name__}: {exc}")
        return {"ok": False, "steps": steps, "payload": {}}


def generate(
    install_id: str,
    out_dir: Path,
    license_years: int,
    root_years: int,
    internal_users: int,
    external_users: int,
    license_name: str,
    flags: list[str],
    force: bool,
) -> None:
    print("生成密钥与证书链（EC P-384）...")
    (root_cert, root_key), (inter_cert, inter_key), (leaf_cert, leaf_key) = make_chain(root_years)

    jwt_str, payload = build_license(
        install_id=install_id,
        leaf_key=leaf_key,
        leaf_cert=leaf_cert,
        inter_cert=inter_cert,
        years=license_years,
        name=license_name,
        internal_users=internal_users,
        external_users=external_users,
        flags=flags,
    )

    # chain/ 里存中间和叶子的**证书**（不是私钥），这样以后拿着私钥就能再签新许可证，
    # 不必重整条链。public.pem 始终是 Root CA —— 也就是容器的信任锚。
    targets = [
        out_dir / "public.pem",
        out_dir / "license.jwt",
        out_dir / "chain" / "intermediate.crt.pem",
        out_dir / "chain" / "leaf.crt.pem",
        out_dir / "private" / "root.key.pem",
        out_dir / "private" / "intermediate.key.pem",
        out_dir / "private" / "leaf.key.pem",
    ]
    if any(p.exists() for p in targets) and not force:
        sys.exit(f"产物已存在，加 --force 覆盖：{', '.join(str(p) for p in targets)}")

    write(out_dir / "public.pem", root_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"))
    write(out_dir / "license.jwt", jwt_str + "\n")
    write(out_dir / "chain" / "intermediate.crt.pem",
          inter_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"))
    write(out_dir / "chain" / "leaf.crt.pem",
          leaf_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"))
    (out_dir / "private").mkdir(parents=True, exist_ok=True)
    write(out_dir / "private" / "root.key.pem", privkey_pem(root_key))
    write(out_dir / "private" / "intermediate.key.pem", privkey_pem(inter_key))
    write(out_dir / "private" / "leaf.key.pem", privkey_pem(leaf_key))
    for key_file in sorted((out_dir / "private").glob("*.key.pem")):
        os.chmod(key_file, 0o600)

    print(f"\n有效期  {datetime.now(timezone.utc).date()} ~ "
          f"{datetime.fromtimestamp(payload['exp'], timezone.utc).date()}")
    print(f"席位    internal={internal_users}  external={external_users}")
    print(f"audience  {AUD_PREFIX}/{install_id}\n")

    # ---- 自检：严格复刻 license.py 的校验顺序 ----
    print("自检（复刻 authentik/enterprise/license.py 的校验顺序）...")
    result = verify_license(jwt_str, out_dir / "public.pem", expect_install_id=install_id)
    for item in result["steps"]:
        print(f"  {'✓' if item['ok'] else '✗'} {item['name']:<12} {item['detail']}")
    if not result["ok"]:
        sys.exit("自检未通过，未写入任何文件之外的判断仅供参考")

    print("\n下一步：")
    print("  1) 把 public.pem 挂到容器的 /authentik/enterprise/public.pem（server + worker）")
    print("  2) license.jwt 的内容填进 authentik：System → Licenses → Add")
    print("  3) 重启 server / worker，让 lru_cache 失效")


def main() -> None:
    ap = argparse.ArgumentParser(description="自签 authentik 企业版许可证")
    ap.add_argument("--install-id", required=True, help="本实例 install_id，决定 JWT 的 aud")
    ap.add_argument("--out-dir", default=".", help="产物输出目录")
    ap.add_argument("--years", type=int, default=25, help="许可证有效期（年）")
    ap.add_argument("--root-years", type=int, default=50, help="根/中间 CA 有效期（年）")
    ap.add_argument("--internal-users", type=int, default=50, help="内部用户席位数")
    ap.add_argument("--external-users", type=int, default=500, help="外部用户席位数")
    ap.add_argument("--name", default="Personal self-signed license")
    ap.add_argument("--flags", default="", help="许可证标记，逗号分隔（trial / non_production）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的产物")
    args = ap.parse_args()

    generate(
        install_id=args.install_id.strip(),
        out_dir=Path(args.out_dir),
        license_years=args.years,
        root_years=args.root_years,
        internal_users=args.internal_users,
        external_users=args.external_users,
        license_name=args.name,
        flags=[f.strip() for f in args.flags.split(",") if f.strip()],
        force=args.force,
    )


if __name__ == "__main__":
    main()
