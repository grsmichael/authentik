"""与 authentik 后端交互：直连 Postgres（主通道）+ docker exec（兜底通道）。

设计要点（见需求文档 03 第十一章「执行通道设计」）：
- 许可证写在表 public.authentik_enterprise_license；authentik 的缓存后端也是 Postgres 表
  public.django_postgres_cache_cacheentry。两者同库，所以「写表 + 清缓存」不需要 docker。
- 唯一需要 docker 的是读「服务端实时生效状态」（LicenseKey.cached_summary()），
  无 docker 时按 FR-13 在本地复算（复刻 license.py 的判定顺序）。
- 所有异常统一包成 AkError(stage, detail)，UI 直接展示，绝不抛 traceback。
"""
from __future__ import annotations

import base64
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import psycopg2
    import psycopg2.extensions
    _HAS_PSYCOPG2 = True
except Exception:  # pragma: no cover
    _HAS_PSYCOPG2 = False

from .config import Config

CACHE_KEY_SUFFIX = "goauthentik.io/enterprise/license"

# 阈值（与 authentik/enterprise/models.py 保持一致）
THRESHOLD_READ_ONLY_WEEKS = 6
THRESHOLD_WARNING_USER_WEEKS = 4
THRESHOLD_WARNING_ADMIN_WEEKS = 2
THRESHOLD_WARNING_EXPIRY_WEEKS = 2


class AkError(Exception):
    """统一异常：stage=出错的环节，detail=可读原因"""

    def __init__(self, stage: str, detail: str):
        super().__init__(f"[{stage}] {detail}")
        self.stage = stage
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PublishedLicense:
    key: str
    name: str
    expiry: datetime  # 带时区
    internal_users: int
    external_users: int

    @property
    def exp_ts(self) -> int:
        return int(self.expiry.timestamp())

    @property
    def audience(self) -> str:
        return ""  # 仅 DB 行不含 install_id；从 JWT 解析


def _parse_pg_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    # 兜底：字符串
    return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)


class AkClient:
    def __init__(self, config: Config):
        self.config = config

    # ---- 连接 ------------------------------------------------
    def _connect_direct(self):
        if not _HAS_PSYCOPG2:
            raise AkError("直连", "psycopg2 未安装（应在隔离 Python 中已就位）")
        try:
            conn = psycopg2.connect(
                host=self.config.pg_host,
                port=int(self.config.pg_port),
                dbname=self.config.pg_db,
                user=self.config.pg_user,
                password=self.config.pg_password,
                sslmode=self.config.pg_ssl_mode,
                connect_timeout=5,
            )
            return conn
        except Exception as exc:  # noqa: BLE001
            raise AkError("直连", f"无法连接 {self.config.pg_host}:{self.config.pg_port} —— {type(exc).__name__}: {exc}")

    def _decide_channel(self) -> str:
        if self.config.channel == "direct":
            return "direct"
        if self.config.channel == "docker":
            return "docker"
        # auto：先试直连
        try:
            conn = self._connect_direct()
            conn.close()
            return "direct"
        except AkError:
            return "docker"

    # ---- 直连 SQL -------------------------------------------
    def _query_direct(self, sql: str, params: tuple | None = None, fetch: bool = True):
        conn = self._connect_direct()
        try:
            cur = conn.cursor()
            cur.execute(sql, params or ())
            rows = cur.fetchall() if fetch else None
            conn.commit()
            return rows
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise AkError("查询", f"{type(exc).__name__}: {exc}")
        finally:
            conn.close()

    # ---- docker 兜底 -----------------------------------------
    def _psql_docker(self, sql: str, fetch: bool = True):
        cmd = [
            "docker", "exec", "-i", self.config.pg_container,
            "psql", "-U", self.config.pg_user, "-d", self.config.pg_db,
            "-v", "ON_ERROR_STOP=1", "--no-psqlrc",
        ]
        if fetch:
            cmd += ["-t", "-A"]
        try:
            proc = subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            raise AkError("docker", "本机未找到 docker 命令（请安装 Docker 或改用直连通道）")
        except Exception as exc:  # noqa: BLE001
            raise AkError("docker", f"执行失败：{type(exc).__name__}: {exc}")
        if proc.returncode != 0:
            raise AkError("docker", f"psql 返回 {proc.returncode}：{proc.stderr.strip()}")
        if not fetch:
            return None
        out = []
        for line in proc.stdout.splitlines():
            line = line.rstrip("\r")
            if line == "" or line.startswith("DELETE") or line.startswith("INSERT") or line.startswith("UPDATE"):
                continue
            out.append(tuple(line.split("|")))
        return out

    def _ak_shell_docker(self, code: str) -> str:
        """在 server 容器里跑一段 Python（用 base64 传，规避多层引号）"""
        b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
        inner = f"import base64,sys; exec(base64.b64decode(sys.argv[1]).decode())"
        cmd = ["docker", "exec", self.config.server_container, "python", "-c", inner, b64]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            raise AkError("docker", "本机未找到 docker 命令")
        except Exception as exc:  # noqa: BLE001
            raise AkError("docker", f"执行失败：{type(exc).__name__}: {exc}")
        if proc.returncode != 0:
            raise AkError("docker", f"ak shell 返回 {proc.returncode}：{proc.stderr.strip()}")
        return proc.stdout.strip()

    # ---- 统一 SQL 入口（auto 时直连失败回退 docker）-----------
    def _sql(self, sql: str, params: tuple | None = None, fetch: bool = True, as_docker: bool | None = None):
        if as_docker is True or (as_docker is None and self._decide_channel() == "docker" and params is None):
            # docker psql 不支持参数化占位符，这里仅用于无参数/已拼好的语句
            return self._psql_docker(sql, fetch=fetch)
        try:
            return self._query_direct(sql, params, fetch=fetch)
        except AkError:
            if self.config.channel == "auto" and params is None:
                return self._psql_docker(sql, fetch=fetch)
            raise

    # ---- 读操作 ----------------------------------------------
    def read_install_id(self) -> str:
        rows = self._sql("SELECT id FROM public.authentik_install_id ORDER BY id LIMIT 1", fetch=True)
        if not rows:
            raise AkError("读取 install_id", "public.authentik_install_id 为空或不存在")
        return str(rows[0][0]).strip()

    def read_published_license(self) -> PublishedLicense | None:
        rows = self._sql(
            "SELECT key, name, expiry, internal_users, external_users "
            "FROM public.authentik_enterprise_license LIMIT 1",
            fetch=True,
        )
        if not rows:
            return None
        key, name, expiry, iu, eu = rows[0]
        return PublishedLicense(
            key=str(key), name=str(name), expiry=_parse_pg_timestamp(expiry),
            internal_users=int(iu), external_users=int(eu),
        )

    def read_usage_counts(self) -> tuple[int, int]:
        anon = self.config.anonymous_username
        iu = self._sql(
            "SELECT count(*) FROM public.authentik_core_user "
            "WHERE username <> %s AND is_active = true AND type = 'internal'",
            (anon,), fetch=True,
        )
        eu = self._sql(
            "SELECT count(*) FROM public.authentik_core_user "
            "WHERE username <> %s AND is_active = true AND type = 'external'",
            (anon,), fetch=True,
        )
        return (int(iu[0][0]) if iu else 0, int(eu[0][0]) if eu else 0)

    def read_last_valid_date(self) -> datetime:
        """读取最近一次 VALID 用量记录时间（用于超限判定）。读不到按 epoch。"""
        try:
            rows = self._sql(
                "SELECT record_date FROM public.authentik_enterprise_licenseusage "
                "WHERE status = 'valid' ORDER BY record_date DESC LIMIT 1",
                fetch=True,
            )
            if rows:
                return _parse_pg_timestamp(rows[0][0])
        except AkError:
            pass
        return datetime.fromtimestamp(0, timezone.utc)

    def clear_cache(self) -> None:
        """清企业版许可证缓存（DatabaseCache 后端）。非 DatabaseCache 时跳过。"""
        sql = (
            "DELETE FROM public.django_postgres_cache_cacheentry "
            f"WHERE cache_key LIKE '%{CACHE_KEY_SUFFIX}'"
        )
        try:
            self._sql(sql, fetch=False)
        except AkError as exc:
            # 表不存在（非 DatabaseCache 后端）等非致命情况：仅提示，不阻断发布
            raise AkError("清缓存(跳过)", f"未清理缓存（可能非 DatabaseCache 后端）：{exc.detail}")

    # ---- 写操作（发布 / 移除）--------------------------------
    def publish_license(self, jwt: str, name: str, internal_users: int, external_users: int, exp_ts: int) -> None:
        channel = self._decide_channel()
        if channel == "direct":
            try:
                self._publish_direct(jwt, name, internal_users, external_users, exp_ts)
                return
            except AkError:
                if self.config.channel != "auto":
                    raise
                # 回退 docker
        # docker 路径
        self._publish_docker(jwt, name, internal_users, external_users, exp_ts)

    def _publish_direct(self, jwt, name, internal_users, external_users, exp_ts) -> None:
        conn = self._connect_direct()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM public.authentik_enterprise_license")
            cur.execute(
                "INSERT INTO public.authentik_enterprise_license "
                "(license_uuid, key, name, expiry, internal_users, external_users) "
                "VALUES (gen_random_uuid(), %s, %s, to_timestamp(%s), %s, %s)",
                (jwt, name, exp_ts, internal_users, external_users),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise AkError("发布(直连)", f"{type(exc).__name__}: {exc}")
        finally:
            conn.close()
        try:
            self.clear_cache()
        except AkError as exc:
            raise AkError("发布", f"已写库但清缓存失败：{exc.detail}（实例可能 12h 内仍读旧值，可稍后重读/重启）")

    def _publish_docker(self, jwt, name, internal_users, external_users, exp_ts) -> None:
        sql = (
            "DELETE FROM public.authentik_enterprise_license; "
            f"INSERT INTO public.authentik_enterprise_license "
            f"(license_uuid, key, name, expiry, internal_users, external_users) "
            f"VALUES (gen_random_uuid(), '{jwt}', '{name}', to_timestamp({exp_ts}), {internal_users}, {external_users});"
        )
        self._psql_docker(sql, fetch=False)
        # 清缓存需走 ak shell（django cache 接口）
        code = (
            "from authentik.enterprise.license import LicenseKey, CACHE_KEY_ENTERPRISE_LICENSE\n"
            "from django.core.cache import cache\n"
            "cache.delete(CACHE_KEY_ENTERPRISE_LICENSE)\n"
            "print('cache cleared')\n"
        )
        try:
            self._ak_shell_docker(code)
        except AkError as exc:
            raise AkError("发布", f"已写库但清缓存失败：{exc.detail}")

    def remove_license(self) -> None:
        channel = self._decide_channel()
        if channel == "direct":
            try:
                conn = self._connect_direct()
                try:
                    cur = conn.cursor()
                    cur.execute("DELETE FROM public.authentik_enterprise_license")
                    conn.commit()
                finally:
                    conn.close()
                self.clear_cache()
                return
            except AkError:
                if self.config.channel != "auto":
                    raise
        # docker 兜底
        self._psql_docker("DELETE FROM public.authentik_enterprise_license", fetch=False)
        code = (
            "from authentik.enterprise.license import LicenseKey, CACHE_KEY_ENTERPRISE_LICENSE\n"
            "from django.core.cache import cache\n"
            "cache.delete(CACHE_KEY_ENTERPRISE_LICENSE)\n"
            "print('cache cleared')\n"
        )
        try:
            self._ak_shell_docker(code)
        except AkError as exc:
            raise AkError("移除", f"已删库但清缓存失败：{exc.detail}")

    # ---- 本地复算生效状态（FR-13）----------------------------
    def compute_status(self, published: PublishedLicense | None, usage: tuple[int, int]) -> dict:
        iu, eu = usage
        now = _now()
        now_ts = now.timestamp()
        if published is None:
            return {
                "status": "Unlicensed", "internal_users": 0, "external_users": 0,
                "latest_valid": "", "usage_internal": iu, "usage_external": eu,
                "source": "本地推算",
            }
        li = published.internal_users
        le = published.external_users
        exp_ts = published.exp_ts
        status = "Valid"
        if iu > li or eu > le:
            last_valid = self.read_last_valid_date()
            if last_valid < now - timedelta(weeks=THRESHOLD_READ_ONLY_WEEKS):
                status = "Read Only"
            elif last_valid < now - timedelta(weeks=THRESHOLD_WARNING_USER_WEEKS):
                status = "Limit Exceeded (User)"
            elif last_valid < now - timedelta(weeks=THRESHOLD_WARNING_ADMIN_WEEKS):
                status = "Limit Exceeded (Admin)"
            # 否则落空，继续走到期判定
        if status == "Valid":
            if exp_ts < now_ts:
                if exp_ts < now_ts - timedelta(weeks=THRESHOLD_READ_ONLY_WEEKS).total_seconds():
                    status = "Read Only"
                else:
                    status = "Expired"
            elif exp_ts <= now_ts + timedelta(weeks=THRESHOLD_WARNING_EXPIRY_WEEKS).total_seconds():
                status = "Expiry Soon"
        return {
            "status": status,
            "internal_users": li,
            "external_users": le,
            "latest_valid": published.expiry.date().isoformat(),
            "usage_internal": iu,
            "usage_external": eu,
            "source": "本地推算",
        }

    # ---- 测试连接 --------------------------------------------
    def test_connection(self) -> tuple[bool, str]:
        if self.config.channel == "docker":
            try:
                self._psql_docker("SELECT 1", fetch=True)
                return True, "docker 通道可达"
            except AkError as exc:
                return False, exc.detail
        try:
            conn = self._connect_direct()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            conn.close()
            return True, f"直连 {self.config.pg_host}:{self.config.pg_port} 成功"
        except AkError as exc:
            if self.config.channel == "auto":
                # 试 docker
                try:
                    self._psql_docker("SELECT 1", fetch=True)
                    return True, "直连失败，但 docker 通道可达"
                except AkError as dex:
                    return False, f"直连失败：{exc.detail}；docker 亦失败：{dex.detail}"
            return False, exc.detail
