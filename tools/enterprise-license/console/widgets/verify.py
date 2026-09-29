"""校验面板：对任意一张票据（粘贴进来，或从许可证库选）跑 verify_license 的六项校验。"""
from __future__ import annotations

from PyQt5.QtWidgets import (
    QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QTextEdit, QVBoxLayout, QWidget, QLabel,
)


class VerifyTab(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("install_id（可选，用于核对 audience）"))
        self.install_id = QLineEdit(self.ctx.config.default_install_id)
        top.addWidget(self.install_id)
        layout.addLayout(top)

        self.input = QTextEdit()
        self.input.setPlaceholderText("在此粘贴一张许可证 JWT（整行）")
        layout.addWidget(QLabel("待校验的许可证 JWT"))
        layout.addWidget(self.input)

        btn = QPushButton("校 验")
        btn.clicked.connect(self.do_verify)
        layout.addWidget(btn)

        self.steps = QListWidget()
        layout.addWidget(QLabel("逐项校验结果"))
        layout.addWidget(self.steps)

        self.summary = QLabel("")
        layout.addWidget(self.summary)

    def do_verify(self):
        jwt = self.input.toPlainText().strip()
        if not jwt:
            self.ctx.set_status("请先粘贴 JWT", error=True)
            return
        iid = self.install_id.text().strip() or None
        self.ctx.set_status("校验中…")
        self.ctx.run(
            lambda: self.ctx.verify_jwt(jwt, iid),
            self._on_result, label="校验",
        )

    def _on_result(self, res):
        if not res.get("ok"):
            self.ctx.set_status(f"校验异常：{res.get('stage')} - {res.get('detail')}", error=True)
            return
        r = res["result"]
        self.steps.clear()
        for s in r["steps"]:
            item = QListWidgetItem(f"{'✓' if s['ok'] else '✗'} {s['name']}  {s.get('detail','')}")
            item.setData(100, s["ok"])
            self.steps.addItem(item)
        if r["ok"]:
            p = r.get("payload", {})
            self.summary.setText(
                f"✅ 校验通过 ｜ name={p.get('name')} 到期={p.get('exp')} "
                f"席位 {p.get('internal_users')}/{p.get('external_users')}"
            )
            self.ctx.set_status("校验通过")
        else:
            self.summary.setText("❌ 校验未通过，见上方红色项")
            self.ctx.set_status("校验未通过", error=True)
        self.ctx.log(f"[校验] ok={r['ok']}")
