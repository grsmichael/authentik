"""控制台配置：读写 console/config.json，并给一套合理默认值。

默认值会尽量从仓库里的 deploy/.env 与目录结构推导，使首次打开即可用（无需手填）。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# console/ -> tools/enterprise-license/ -> tools/ -> repo 根
CONSOLE_DIR = Path(__file__).resolve().parent
ENT_LICENSE_DIR = CONSOLE_DIR.parent  # tools/enterprise-license/
REPO_ROOT = ENT_LICENSE_DIR.parent.parent  # 仓库根
DEPLOY_DIR = REPO_ROOT / "deploy"
PKI_DIR_DEFAULT = DEPLOY_DIR / "enterprise-pki"
ISSUED_DIR_DEFAULT = CONSOLE_DIR / "issued"
CONFIG_PATH_DEFAULT = CONSOLE_DIR / "config.json"


def _read_env(path: Path) -> dict[str, str]:
    """极简 KEY=VALUE 解析（不展开变量、不处理引号嵌套）"""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


@dataclass
class Config:
    # PKI 与产物
    pki_dir: str = str(PKI_DIR_DEFAULT)
    issued_dir: str = str(ISSUED_DIR_DEFAULT)
    # 通道与数据库（直连）
    channel: str = "auto"  # auto / direct / docker
    pg_host: str = "127.0.0.1"
    pg_port: int = 5433
    pg_db: str = "authentik"
    pg_user: str = "authentik"
    pg_password: str = ""
    pg_ssl_mode: str = "prefer"
    anonymous_username: str = "AnonymousUser"
    # docker 兜底通道
    pg_container: str = "authentik-postgresql"
    server_container: str = "authentik-server"
    # 其它
    default_install_id: str = ""

    def pki_path(self) -> Path:
        return Path(self.pki_dir).expanduser()

    def issued_path(self) -> Path:
        p = Path(self.issued_dir).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def save(self, path: Path = CONFIG_PATH_DEFAULT) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = CONFIG_PATH_DEFAULT) -> "Config":
        env = _read_env(DEPLOY_DIR / ".env")
        defaults = cls(
            pg_user=env.get("PG_USER", "authentik"),
            pg_db=env.get("PG_DB", "authentik"),
            pg_password=env.get("PG_PASS", ""),
        )
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            merged = asdict(defaults)
            merged.update({k: v for k, v in data.items() if k in merged})
            return cls(**merged)
        # 首次：落盘默认配置
        defaults.save(path)
        return defaults


# 给设置页用的字段元信息（顺序 + 中文标签 + 类型）
FIELD_META: list[tuple[str, str, str]] = [
    ("channel", "通道模式 (auto/direct/docker)", "choice"),
    ("pg_host", "数据库主机", "str"),
    ("pg_port", "数据库端口", "int"),
    ("pg_db", "数据库名", "str"),
    ("pg_user", "数据库用户", "str"),
    ("pg_password", "数据库密码", "secret"),
    ("pg_ssl_mode", "SSL 模式", "str"),
    ("anonymous_username", "匿名用户名", "str"),
    ("pg_container", "Postgres 容器名", "str"),
    ("server_container", "Server 容器名", "str"),
    ("default_install_id", "默认 install_id", "str"),
    ("pki_dir", "PKI 目录", "str"),
    ("issued_dir", "许可证库目录", "str"),
]
