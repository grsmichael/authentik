"""设置页：配置读写、连接测试、CA 链管理（重签 / 恢复信任锚 / 移除许可证）。"""
from __future__ import annotations

import shutil
from pathlib import Path

from PyQt5.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget, QComboBox, QLabel,
)

from .. import licenses
from ..config import FIELD_META


class SettingsTab(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.widgets: dict[str, QWidget] = {}
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        # 配置项
        cfg_box = QGroupBox("连接与路径配置")
        fl = QFormLayout(cfg_box)
        cfg = self.ctx.config
        data = cfg.__dict__
        for key, label, kind in FIELD_META:
            val = data.get(key, "")
            if kind == "choice":
                w = QComboBox(); w.addItems(["auto", "direct", "docker"]); w.setCurrentText(str(val))
            elif kind == "int":
                w = QSpinBox(); w.setRange(0, 65535); w.setValue(int(val))
            elif kind == "secret":
                w = QLineEdit(str(val)); w.setEchoMode(QLineEdit.Password)
            else:
                w = QLineEdit(str(val))
            self.widgets[key] = w
            fl.addRow(label, w)
        self.btn_save = QPushButton("保存配置")
        self.btn_save.clicked.connect(self.save)
        self.btn_test = QPushButton("测试连接")
        self.btn_test.clicked.connect(self.test)
        row = QHBoxLayout(); row.addWidget(self.btn_save); row.addWidget(self.btn_test)
        fl.addRow(row)
        layout.addWidget(cfg_box)

        # CA 链管理
        ca_box = QGroupBox("CA 链管理（危险操作，均有备份与二次确认）")
        cl = QVBoxLayout(ca_box)
        self.iid = QLineEdit(self.ctx.config.default_install_id)
        self.years = QSpinBox(); self.years.setRange(1, 100); self.years.setValue(25)
        self.name = QLineEdit("Personal self-signed license")
        self.internal = QSpinBox(); self.internal.setRange(0, 1000000); self.internal.setValue(50)
        self.external = QSpinBox(); self.external.setRange(0, 1000000); self.external.setValue(500)
        ca_form = QFormLayout()
        ca_form.addRow("install_id", self.iid)
        ca_form.addRow("年限", self.years)
        ca_form.addRow("名称", self.name)
        ca_form.addRow("内/外部席位", self._seat_row())
        cl.addLayout(ca_form)

        btn_row = QHBoxLayout()
        self.btn_reissue = QPushButton("重签整条 CA 链")
        self.btn_reissue.clicked.connect(self.reissue)
        self.btn_restore = QPushButton("恢复最近的信任锚备份")
        self.btn_restore.clicked.connect(self.restore)
        self.btn_remove = QPushButton("移除已发布许可证")
        self.btn_remove.clicked.connect(self.remove_license)
        btn_row.addWidget(self.btn_reissue)
        btn_row.addWidget(self.btn_restore)
        btn_row.addWidget(self.btn_remove)
        cl.addLayout(btn_row)
        layout.addWidget(ca_box)

        layout.addStretch(1)

    def _seat_row(self):
        w = QWidget(); h = QHBoxLayout(w)
        h.addWidget(self.internal); h.addWidget(QLabel("/")); h.addWidget(self.external)
        return w

    def _collect(self) -> dict:
        out = {}
        for key, _, kind in FIELD_META:
            w = self.widgets[key]
            if kind == "choice":
                out[key] = w.currentText()
            elif kind == "int":
                out[key] = w.value()
            else:
                out[key] = w.text()
        return out

    def save(self):
        data = self._collect()
        for k, v in data.items():
            setattr(self.ctx.config, k, v)
        self.ctx.config.save()
        # 同步给 client
        self.ctx.client.config = self.ctx.config
        self.ctx.log("[设置] 已保存配置")
        self.ctx.set_status("配置已保存")

    def test(self):
        self.ctx.set_status("测试连接…")
        self.ctx.run(lambda: self.ctx.client.test_connection(), self._on_test, label="测试连接")

    def _on_test(self, res):
        if not res.get("ok"):
            self.ctx.set_status(f"连接失败：{res.get('detail')}", error=True)
            self.ctx.log(f"[设置] 连接失败 {res.get('detail')}")
            return
        self.ctx.set_status(f"连接成功：{res['result'][1]}")
        self.ctx.log(f"[设置] 连接成功 {res['result'][1]}")

    def reissue(self):
        iid = self.iid.text().strip()
        if not iid:
            self.ctx.set_status("请先填 install_id", error=True)
            return
        ans = QMessageBox.question(
            self, "确认重签",
            "重签会生成全新三级链并覆盖 public.pem。\n旧票将全部失效，且需要重新挂载并发布新票。\n是否继续？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self.ctx.set_status("重签中…")
        self.btn_reissue.setEnabled(False)
        self.ctx.run(
            lambda: licenses.reissue_chain(
                self.ctx.config.pki_path(), iid, self.years.value(),
                self.name.text(), self.internal.value(), self.external.value(), [],
            ),
            self._on_reissue, label="重签",
        )

    def _on_reissue(self, res):
        self.btn_reissue.setEnabled(True)
        if not res.get("ok"):
            self.ctx.set_status(f"重签失败：{res.get('stage')} - {res.get('detail')}", error=True)
            return
        r = res["result"]
        self.ctx.set_status(f"重签完成，备份={Path(r['backup']).name}，请重新挂载 public.pem 并发布")
        self.ctx.log(f"[设置] 重签完成 备份={r['backup']} verify_ok={r['verify_ok']}")
        if self.ctx.overview_tab:
            self.ctx.overview_tab.refresh()

    def restore(self):
        pki = self.ctx.config.pki_path()
        backups = sorted(pki.glob("public.pem.bak-*"), reverse=True)
        if not backups:
            self.ctx.set_status("没有找到 public.pem 备份", error=True)
            return
        latest = backups[0]
        ans = QMessageBox.question(
            self, "确认恢复",
            f"将用备份 {latest.name} 覆盖当前 public.pem。\n是否继续？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        shutil.copy(str(latest), str(pki / "public.pem"))
        self.ctx.set_status(f"已恢复信任锚：{latest.name}")
        self.ctx.log(f"[设置] 恢复信任锚 {latest}")
        if self.ctx.overview_tab:
            self.ctx.overview_tab.refresh()

    def remove_license(self):
        ans = QMessageBox.question(
            self, "确认移除",
            "将从数据库删除已发布的许可证并清缓存，authentik 回到社区版（unlicensed）。\n是否继续？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self.ctx.set_status("移除中…")
        self.btn_remove.setEnabled(False)
        self.ctx.run(lambda: self.ctx.client.remove_license(), self._on_remove, label="移除")

    def _on_remove(self, res):
        self.btn_remove.setEnabled(True)
        if not res.get("ok"):
            self.ctx.set_status(f"移除失败：{res.get('stage')} - {res.get('detail')}", error=True)
            return
        self.ctx.set_status("已移除许可证，authentik 回到社区版")
        self.ctx.log("[设置] 已移除许可证")
        if self.ctx.overview_tab:
            self.ctx.overview_tab.refresh()
