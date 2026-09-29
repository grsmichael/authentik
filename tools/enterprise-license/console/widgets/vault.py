"""许可证库页：列出 issued/ 下所有票，支持复制 / 导出 / 发布 / 删除（删除二次确认）。"""
from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

from PyQt5.QtWidgets import (
    QFileDialog, QHBoxLayout, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


def _decode_payload(jwt: str) -> dict:
    parts = jwt.split(".")
    if len(parts) != 3:
        return {}
    seg = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


class VaultTab(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.items: list[dict] = []
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新")
        self.btn_copy = QPushButton("复制")
        self.btn_export = QPushButton("导出")
        self.btn_publish = QPushButton("发布")
        self.btn_delete = QPushButton("删除")
        for b in (self.btn_refresh, self.btn_copy, self.btn_export, self.btn_publish, self.btn_delete):
            bar.addWidget(b)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_copy.clicked.connect(self.copy_selected)
        self.btn_export.clicked.connect(self.export_selected)
        self.btn_publish.clicked.connect(self.publish_selected)
        self.btn_delete.clicked.connect(self.delete_selected)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["文件名", "名称", "到期", "席位(内/外)", "本地校验", "audience"])
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(4, 80)

        layout.addLayout(bar)
        layout.addWidget(self.table)

    def refresh(self):
        self.items = self.ctx.list_issued()
        self.table.setRowCount(0)
        for it in self.items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(it["filename"]))
            self.table.setItem(r, 1, QTableWidgetItem(str(it["name"])))
            self.table.setItem(r, 2, QTableWidgetItem(str(it["expiry"])))
            self.table.setItem(r, 3, QTableWidgetItem(f"{it['internal_users']}/{it['external_users']}"))
            self.table.setItem(r, 4, QTableWidgetItem("通过" if it["ok"] else "失败"))
            self.table.setItem(r, 5, QTableWidgetItem(str(it["audience"])))
        self.ctx.log(f"[许可证库] 刷新，共 {len(self.items)} 张")

    def _selected(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.items):
            self.ctx.set_status("请先选择一行", error=True)
            return None
        return self.items[row]

    def copy_selected(self):
        it = self._selected()
        if not it:
            return
        from PyQt5.QtWidgets import QApplication
        QApplication.clipboard().setText(it["jwt"])
        self.ctx.set_status(f"已复制 {it['filename']}")

    def export_selected(self):
        it = self._selected()
        if not it:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出许可证", it["filename"], "JWT (*.jwt);;All (*)")
        if not path:
            return
        Path(path).write_text(it["jwt"] + "\n", encoding="utf-8", newline="")
        meta_path = Path(it["jwt_path"]).with_suffix(".json")
        if meta_path.exists():
            shutil.copy(str(meta_path), str(Path(path).with_suffix(".json")))
        self.ctx.set_status(f"已导出到 {path}")
        self.ctx.log(f"[许可证库] 导出 {path}")

    def publish_selected(self):
        it = self._selected()
        if not it:
            return
        p = _decode_payload(it["jwt"])
        if not p:
            self.ctx.set_status("无法解析该票据 payload", error=True)
            return
        self.ctx.set_status("发布中…")
        self.btn_publish.setEnabled(False)
        jwt = it["jwt"]
        self.ctx.run(
            lambda: self.ctx.client.publish_license(
                jwt, p.get("name", "license"),
                int(p.get("internal_users", 0)), int(p.get("external_users", 0)),
                int(p.get("exp", 0)),
            ),
            self._on_published, label="发布",
        )

    def _on_published(self, res):
        self.btn_publish.setEnabled(True)
        if not res.get("ok"):
            self.ctx.set_status(f"发布失败：{res.get('stage')} - {res.get('detail')}", error=True)
            return
        self.ctx.set_status("发布成功")
        self.ctx.log("[许可证库] 发布成功")
        if self.ctx.overview_tab:
            self.ctx.overview_tab.refresh()

    def delete_selected(self):
        it = self._selected()
        if not it:
            return
        ans = QMessageBox.question(
            self, "确认删除",
            f"确定删除 {it['filename']} 及其元数据？\n（仅删本地文件，不影响已发布到 authentik 的许可证）",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        try:
            Path(it["jwt_path"]).unlink(missing_ok=True)
            Path(it["jwt_path"]).with_suffix(".json").unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            self.ctx.set_status(f"删除失败：{exc}", error=True)
            return
        self.ctx.set_status(f"已删除 {it['filename']}")
        self.ctx.log(f"[许可证库] 删除 {it['filename']}")
        self.refresh()
