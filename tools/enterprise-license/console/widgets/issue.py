"""签发页：填参数 → 签票 → 本地逐项校验 → 落盘 / 发布。"""
from __future__ import annotations

from PyQt5.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QPushButton, QSpinBox,
    QTextEdit, QVBoxLayout, QWidget, QListWidget, QListWidgetItem, QLabel,
)


class IssueTab(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.current_jwt = ""
        self.current_payload: dict = {}
        self._build()

    def _field(self, label, widget, layout):
        layout.addRow(label, widget)
        return widget

    def _build(self):
        main = QHBoxLayout(self)

        # 左：表单
        left = QVBoxLayout()
        form_box = QGroupBox("签发参数")
        fl = QFormLayout(form_box)
        self.name = self._field("名称", QLineEdit("Personal self-signed license"), fl)
        self.install_id = QLineEdit(self.ctx.config.default_install_id)
        self.btn_read_id = QPushButton("从 authentik 读取")
        self.btn_read_id.clicked.connect(self.read_install_id)
        id_row = QHBoxLayout()
        id_row.addWidget(self.install_id)
        id_row.addWidget(self.btn_read_id)
        fl.addRow("install_id", id_row)

        self.years = QSpinBox(); self.years.setRange(1, 100); self.years.setValue(25)
        self._field("年限 (年)", self.years, fl)
        self.internal = QSpinBox(); self.internal.setRange(0, 1000000); self.internal.setValue(50)
        self._field("内部席位", self.internal, fl)
        self.external = QSpinBox(); self.external.setRange(0, 1000000); self.external.setValue(500)
        self._field("外部席位", self.external, fl)
        self.flags = QLineEdit("")
        self._field("flags (逗号分隔)", self.flags, fl)

        self.btn_sign = QPushButton("签 发")
        self.btn_sign.clicked.connect(self.do_sign)
        fl.addRow(self.btn_sign)

        left.addWidget(form_box)
        left.addStretch(1)

        # 右：结果
        right = QVBoxLayout()
        res_box = QGroupBox("校验结果")
        rl = QVBoxLayout(res_box)
        self.steps = QListWidget()
        rl.addWidget(self.steps)
        self.jwt_box = QTextEdit()
        self.jwt_box.setReadOnly(True)
        self.jwt_box.setPlaceholderText("签出的许可证 JWT 会显示在这里（只读）")
        rl.addWidget(QLabel("许可证 JWT（只读）"))
        rl.addWidget(self.jwt_box)
        btn_row = QHBoxLayout()
        self.btn_copy = QPushButton("复制")
        self.btn_copy.clicked.connect(self.copy_jwt)
        self.btn_save = QPushButton("保存到 issued/")
        self.btn_save.clicked.connect(self.save_jwt)
        self.btn_publish = QPushButton("发布到 authentik")
        self.btn_publish.clicked.connect(self.publish)
        btn_row.addWidget(self.btn_copy)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_publish)
        rl.addLayout(btn_row)
        right.addWidget(res_box)

        main.addLayout(left, 1)
        main.addLayout(right, 2)
        self._reset_result_buttons()

    def _reset_result_buttons(self):
        self.btn_copy.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_publish.setEnabled(False)

    def read_install_id(self):
        self.ctx.set_status("读取 install_id…")
        self.ctx.run(lambda: self.ctx.client.read_install_id(), self._on_install_id, label="读install_id")

    def _on_install_id(self, res):
        if not res.get("ok"):
            self.ctx.set_status(f"读取失败：{res.get('detail')}", error=True)
            return
        self.install_id.setText(str(res["result"]).strip())
        self.ctx.set_status("已填入 install_id")
        self.ctx.log(f"[签发] 读取 install_id = {res['result']}")

    def do_sign(self):
        iid = self.install_id.text().strip()
        if not iid:
            self.ctx.set_status("请先填 install_id（或点「从 authentik 读取」）", error=True)
            return
        flags = [f.strip() for f in self.flags.text().split(",") if f.strip()]
        self.ctx.set_status("签发中…")
        self.btn_sign.setEnabled(False)
        self.ctx.run(
            lambda: self.ctx.sign_and_prepare(
                iid, self.years.value(), self.name.text(),
                self.internal.value(), self.external.value(), flags,
            ),
            self._on_signed, label="签发",
        )

    def _on_signed(self, res):
        self.btn_sign.setEnabled(True)
        if not res.get("ok"):
            self.ctx.set_status(f"签发失败：{res.get('stage')} - {res.get('detail')}", error=True)
            self.ctx.log(f"[签发] 失败 {res.get('stage')}: {res.get('detail')}")
            return
        self.current_jwt = res["result"]["jwt"]
        self.current_payload = res["result"]["payload"]
        self._show_steps(res["result"]["steps"])
        self.jwt_box.setPlainText(self.current_jwt)
        self.btn_copy.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_publish.setEnabled(True)
        self.ctx.set_status("签出成功，已通过本地校验，可保存或发布")
        self.ctx.log(f"[签发] 成功 name={self.current_payload.get('name')} exp={self.current_payload.get('exp')}")

    def _show_steps(self, steps):
        self.steps.clear()
        for s in steps:
            item = QListWidgetItem(f"{'✓' if s['ok'] else '✗'} {s['name']}  {s.get('detail','')}")
            item.setData(100, s["ok"])
            self.steps.addItem(item)

    def copy_jwt(self):
        from PyQt5.QtWidgets import QApplication
        QApplication.clipboard().setText(self.current_jwt)
        self.ctx.set_status("已复制到剪贴板")

    def save_jwt(self):
        if not self.current_jwt:
            return
        paths = self.ctx.save_issued(self.current_jwt, self.current_payload, self.install_id.text().strip())
        self.ctx.set_status(f"已保存到 {paths[0].name}")
        self.ctx.log(f"[签发] 保存 {paths[0]} / {paths[1]}")
        if hasattr(self.ctx, "vault_tab") and self.ctx.vault_tab:
            self.ctx.vault_tab.refresh()

    def publish(self):
        if not self.current_jwt:
            return
        self.ctx.set_status("发布中…")
        self.btn_publish.setEnabled(False)
        p = self.current_payload
        self.ctx.run(
            lambda: self.ctx.client.publish_license(
                self.current_jwt, p.get("name", "license"),
                int(p["internal_users"]), int(p["external_users"]), int(p["exp"]),
            ),
            self._on_published, label="发布",
        )

    def _on_published(self, res):
        self.btn_publish.setEnabled(True)
        if not res.get("ok"):
            self.ctx.set_status(f"发布失败：{res.get('stage')} - {res.get('detail')}", error=True)
            self.ctx.log(f"[发布] 失败 {res.get('stage')}: {res.get('detail')}")
            return
        self.ctx.set_status("发布成功，已写库并清缓存")
        self.ctx.log("[发布] 成功")
        if hasattr(self.ctx, "overview_tab") and self.ctx.overview_tab:
            self.ctx.overview_tab.refresh()
