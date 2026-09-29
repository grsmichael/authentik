"""签发与许可证库：直接复用 gen_license.py 的密码学实现（单一事实来源）。

控制台不复制任何密码学逻辑，只调用 gen_license 的 build_license / verify_license / make_chain。
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.x509 import load_pem_x509_certificate

# 把 tools/enterprise-license/ 加入路径，复用 gen_license.py
_GEN_DIR = str(Path(__file__).resolve().parent.parent)
if _GEN_DIR not in sys.path:
    sys.path.insert(0, _GEN_DIR)

import gen_license  # noqa: E402

from .authentik import AkError  # noqa: E402

# 仓库根（console/ -> enterprise-license/ -> tools/ -> repo）
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _pem_cert(path: Path):
    return load_pem_x509_certificate(path.read_bytes())


def load_signing_material(pki_dir: Path) -> tuple:
    """取出签发所需的 叶子私钥 / 叶子证书 / 中间证书。

    缺失时抛出 AkError，UI 据此提示「重签整条 CA 链」（NFR-03）。
    """
    leaf_key_path = pki_dir / "private" / "leaf.key.pem"
    leaf_cert_path = pki_dir / "chain" / "leaf.crt.pem"
    inter_cert_path = pki_dir / "chain" / "intermediate.crt.pem"
    for p in (leaf_key_path, leaf_cert_path, inter_cert_path):
        if not p.exists():
            raise AkError(
                "加载签发材料",
                f"缺少 {p.name}（{p}）。请先到「设置/概览」执行「重签整条 CA 链」补齐。",
            )
    leaf_key = gen_license.load_private_key(leaf_key_path)
    leaf_cert = _pem_cert(leaf_cert_path)
    inter_cert = _pem_cert(inter_cert_path)
    return leaf_key, leaf_cert, inter_cert


def sign(
    pki_dir: Path,
    install_id: str,
    years: int,
    name: str,
    internal_users: int,
    external_users: int,
    flags: list[str] | None = None,
) -> tuple[str, dict]:
    """签一张许可证，先本地逐项校验再返回 (jwt, payload)。"""
    leaf_key, leaf_cert, inter_cert = load_signing_material(pki_dir)
    jwt, payload = gen_license.build_license(
        install_id=install_id.strip(),
        leaf_key=leaf_key,
        leaf_cert=leaf_cert,
        inter_cert=inter_cert,
        years=int(years),
        name=name,
        internal_users=int(internal_users),
        external_users=int(external_users),
        flags=flags or [],
    )
    result = gen_license.verify_license(jwt, pki_dir / "public.pem", expect_install_id=install_id.strip())
    if not result["ok"]:
        bad = [s["name"] for s in result["steps"] if not s["ok"]]
        raise AkError("本地校验", f"签出的票据未通过自校验：{', '.join(bad)}")
    return jwt, payload


def verify(jwt: str, pki_dir: Path, install_id: str | None = None) -> dict:
    return gen_license.verify_license(jwt, pki_dir / "public.pem", expect_install_id=install_id or None)


def _slug(name: str) -> str:
    out = []
    for ch in name.lower():
        out.append(ch if ch.isalnum() else "-")
    s = "-".join("".join(out).split("-"))
    return s or "license"


def save_issued(issued_dir: Path, jwt: str, payload: dict, install_id: str) -> tuple[Path, Path]:
    issued_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = _slug(payload.get("name", "license"))
    jwt_path = issued_dir / f"{ts}-{slug}.jwt"
    json_path = issued_dir / f"{ts}-{slug}.json"
    jwt_path.write_text(jwt + "\n", encoding="utf-8", newline="")
    meta = {
        "name": payload.get("name"),
        "install_id": install_id,
        "years": None,
        "internal_users": payload.get("internal_users"),
        "external_users": payload.get("external_users"),
        "flags": payload.get("license_flags", []),
        "exp": payload.get("exp"),
        "expiry": datetime.fromtimestamp(payload["exp"], timezone.utc).date().isoformat(),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "audience": payload.get("aud"),
        "sha256": hashlib.sha256(jwt.encode("utf-8")).hexdigest(),
    }
    json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8", newline="")
    return jwt_path, json_path


def list_issued(issued_dir: Path, pki_dir: Path | None = None) -> list[dict]:
    if pki_dir is None:
        pki_dir = REPO_ROOT / "deploy" / "enterprise-pki"
    if not issued_dir.exists():
        return []
    items: list[dict] = []
    for jwt_path in sorted(issued_dir.glob("*.jwt"), reverse=True):
        try:
            jwt = jwt_path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        meta_path = jwt_path.with_suffix(".json")
        meta: dict = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        # 实时校验状态
        ok = False
        try:
            res = gen_license.verify_license(jwt, pki_dir / "public.pem")
            ok = res["ok"]
        except Exception:
            ok = False
        items.append({
            "filename": jwt_path.name,
            "name": meta.get("name", "(未知)"),
            "expiry": meta.get("expiry", ""),
            "internal_users": meta.get("internal_users", ""),
            "external_users": meta.get("external_users", ""),
            "audience": meta.get("audience", ""),
            "ok": ok,
            "jwt_path": str(jwt_path),
            "jwt": jwt,
        })
    return items


def reissue_chain(
    pki_dir: Path,
    install_id: str,
    years: int,
    name: str,
    internal_users: int,
    external_users: int,
    flags: list[str] | None = None,
) -> dict:
    """重签整条 CA 链（FR-08）。

    备份现有 public.pem，生成全新三级链，写盘并重新签发一张默认许可证。
    返回摘要；旧票一律失效（因为信任锚变了）。
    """
    (root_cert, root_key), (inter_cert, inter_key), (leaf_cert, leaf_key) = gen_license.make_chain(50)

    # 备份信任锚
    public_pem = pki_dir / "public.pem"
    bak_name = f"public.pem.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    backup_path = pki_dir / bak_name
    if public_pem.exists():
        backup_path.write_bytes(public_pem.read_bytes())

    _write_pem(public_pem, root_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"))
    _write_pem(pki_dir / "chain" / "intermediate.crt.pem",
              inter_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"))
    _write_pem(pki_dir / "chain" / "leaf.crt.pem",
              leaf_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"))

    (pki_dir / "private").mkdir(parents=True, exist_ok=True)
    _write_pem(pki_dir / "private" / "root.key.pem", gen_license.privkey_pem(root_key))
    _write_pem(pki_dir / "private" / "intermediate.key.pem", gen_license.privkey_pem(inter_key))
    _write_pem(pki_dir / "private" / "leaf.key.pem", gen_license.privkey_pem(leaf_key))
    for kf in sorted((pki_dir / "private").glob("*.key.pem")):
        try:
            kf.chmod(0o600)
        except Exception:
            pass

    # 重新签发默认许可证（让控制台立刻能发布新票）
    jwt, payload = gen_license.build_license(
        install_id=install_id.strip(),
        leaf_key=leaf_key,
        leaf_cert=leaf_cert,
        inter_cert=inter_cert,
        years=int(years),
        name=name,
        internal_users=int(internal_users),
        external_users=int(external_users),
        flags=flags or [],
    )
    _write_pem(pki_dir / "license.jwt", jwt + "\n")
    res = gen_license.verify_license(jwt, pki_dir / "public.pem", expect_install_id=install_id.strip())
    return {
        "backup": str(backup_path),
        "verify_ok": res["ok"],
        "expiry": datetime.fromtimestamp(payload["exp"], timezone.utc).date().isoformat(),
        "name": name,
    }


def _write_pem(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
