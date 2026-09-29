"""概览页：三张卡片 —— 信任锚 / 已发布许可证 / authentik 生效状态。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.x509 import load_pem_x509_certificate
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFormLayout, QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget,
)


def _fp(cert) -> str:
    return cert.fingerprint(hashes.SHA256()).hex()


class OverviewTab(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._build()

    def _card(self, title: str) -> tuple[QGroupBox, QFormLayout]:
        box = QGroupBox(title)
        form = QFormLayout(box)
        return box, form

    def _build(self):
        layout = QVBoxLayout(self)

        # 信任锚
        self.anchor_box, self.anchor_form = self._card("信任锚（Root CA，唯一信任锚）")
        self.l_anchor_cn = QLabel("-")
        self.l_anchor_valid = QLabel("-")
        self.l_anchor_fp = QLabel("-")
        self.l_anchor_path = QLabel("-")
        self.l_anchor_fp.setTextInteractionFlags(Qt.TextSelectableByMouse)  # 可选中复制
        self.anchor_form.addRow("CN", self.l_anchor_cn)
        self.anchor_form.addRow("有效期", self.l_anchor_valid)
        self.anchor_form.addRow("SHA256 指纹", self.l_anchor_fp)
        self.anchor_form.addRow("证书文件", self.l_anchor_path)

        # 已发布许可证
        self.pub_box, self.pub_form = self._card("已发布到数据库的许可证")
        self.l_pub_name = QLabel("-")
        self.l_pub_expiry = QLabel("-")
        self.l_pub_seats = QLabel("-")
        self.l_pub_aud = QLabel("-")
        self.l_pub_written = QLabel("-")
        self.pub_form.addRow("名称", self.l_pub_name)
        self.pub_form.addRow("到期", self.l_pub_expiry)
        self.pub_form.addRow("席位(内/外)", self.l_pub_seats)
        self.pub_form.addRow("audience", self.l_pub_aud)
        self.pub_form.addRow("写入时间", self.l_pub_written)

        # 生效状态
        self.status_box, self.status_form = self._card("authentik 实际生效状态")
        self.l_status = QLabel("-")
        self.l_status.setObjectName("statusBig")
        self.l_status_seats = QLabel("-")
        self.l_status_usage = QLabel("-")
        self.l_status_valid = QLabel("-")
        self.l_status_src = QLabel("-")
        self.status_form.addRow("状态", self.l_status)
        self.status_form.addRow("授权席位(内/外)", self.l_status_seats)
        self.status_form.addRow("实际使用(内/外)", self.l_status_usage)
        self.status_form.addRow("latest_valid", self.l_status_valid)
        self.status_form.addRow("数据来源", self.l_status_src)

        btn = QPushButton("重新读取")
        btn.clicked.connect(self.refresh)

        layout.addWidget(self.anchor_box)
        layout.addWidget(self.pub_box)
        layout.addWidget(self.status_box)
        layout.addWidget(btn)
        layout.addStretch(1)

    def refresh(self):
        self.ctx.set_status("读取中…")
        self.ctx.run(self._collect, self._on_data, label="概览")

    def _trust_anchor(self) -> dict:
        pki = self.ctx.config.pki_path()
        pub = pki / "public.pem"
        if not pub.exists():
            return {"cn": "（缺失 public.pem）", "valid": "-", "fp": "-", "path": str(pub)}
        cert = load_pem_x509_certificate(pub.read_bytes())
        nb = cert.not_valid_before_utc if hasattr(cert, "not_valid_before_utc") else cert.not_valid_before
        na = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after
        return {
            "cn": cert.subject.rfc4514_string(),
            "valid": f"{nb.date()} ~ {na.date()}",
            "fp": _fp(cert),
            "path": str(pub),
        }

    def _collect(self) -> dict:
        anchor = self._trust_anchor()
        client = self.ctx.client
        # install_id：优先从库读，失败用配置默认
        try:
            install_id = client.read_install_id()
        except Exception:
            install_id = self.ctx.config.default_install_id or "（读取失败，请手填或使用设置页测试连接）"
        published = client.read_published_license()
        usage = client.read_usage_counts()
        status = client.compute_status(published, usage)
        return {
            "anchor": anchor,
            "install_id": install_id,
            "published": (
                {
                    "name": published.name,
                    "expiry": published.expiry.date().isoformat(),
                    "internal_users": published.internal_users,
                    "external_users": published.external_users,
                    "audience": published.audience or "",
                }
                if published
                else None
            ),
            "status": status,
        }

    def _on_data(self, res: dict):
        if not res.get("ok"):
            self.ctx.set_status(f"读取失败：{res.get('stage')} - {res.get('detail')}", error=True)
            self.ctx.log(f"[概览] 失败 {res.get('stage')}: {res.get('detail')}")
            return
        d = res["result"]
        a = d["anchor"]
        self.l_anchor_cn.setText(a["cn"])
        self.l_anchor_valid.setText(a["valid"])
        self.l_anchor_fp.setText(a["fp"])
        self.l_anchor_path.setText(a["path"])

        p = d["published"]
        if p:
            self.l_pub_name.setText(str(p["name"]))
            self.l_pub_expiry.setText(p["expiry"])
            self.l_pub_seats.setText(f"{p['internal_users']} / {p['external_users']}")
            self.l_pub_aud.setText(p["audience"] or "（数据库行不含，见签发页）")
            self.l_pub_written.setText("（库内现有行）")
        else:
            self.l_pub_name.setText("（未发布）")
            self.l_pub_expiry.setText("-")
            self.l_pub_seats.setText("-")
            self.l_pub_aud.setText("-")
            self.l_pub_written.setText("-")

        s = d["status"]
        self.l_status.setText(s["status"])
        self.l_status.setProperty("statusValue", s["status"])
        self.l_status_seats.setText(f"{s['internal_users']} / {s['external_users']}")
        self.l_status_usage.setText(f"{s['usage_internal']} / {s['usage_external']}")
        self.l_status_valid.setText(s["latest_valid"] or "-")
        self.l_status_src.setText(s.get("source", ""))
        self.ctx.set_status(f"当前状态：{s['status']}（{s['usage_internal']}/{s['usage_external']} 用户，本地推算）")
        self.ctx.log(f"[概览] 状态={s['status']} 席位={s['internal_users']}/{s['external_users']} 使用={s['usage_internal']}/{s['usage_external']}")
