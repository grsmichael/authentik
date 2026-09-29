#!/usr/bin/env python3
"""authentik 许可证管理控制台 —— 入口。

用法：
    python console/main.py [--config 路径/config.json]

线程模型：所有阻塞调用（数据库 / docker / 签发）都经 QThreadPool 跑 Worker，
完成后回主线程更新 UI，界面不假死（NFR-01）。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QThread
from PyQt5.QtWidgets import (
    QApplication, QDockWidget, QMainWindow, QStatusBar, QTabWidget, QTextEdit,
    QWidget,
)

from . import licenses
from .authentik import AkClient, AkError
from .config import Config, CONFIG_PATH_DEFAULT
from .workers import Worker
from .widgets.help import HelpTab
from .widgets.issue import IssueTab
from .widgets.overview import OverviewTab
from .widgets.settings import SettingsTab
from .widgets.vault import VaultTab
from .widgets.verify import VerifyTab


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path):
        super().__init__()
        self.config_path = config_path
        self.config = Config.load(config_path)
        self.client = AkClient(self.config)
        self._threads: list[QThread] = []
        self._workers: list[Worker] = []
        self.log_path = Path(__file__).resolve().parent / "console.log"

        self.setWindowTitle("authentik 许可证管理控制台")
        self.resize(1100, 720)
        self.setMinimumSize(900, 600)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.overview_tab = OverviewTab(self)
        self.issue_tab = IssueTab(self)
        self.vault_tab = VaultTab(self)
        self.verify_tab = VerifyTab(self)
        self.settings_tab = SettingsTab(self)
        self.help_tab = HelpTab(self)

        self.tabs.addTab(self.overview_tab, "概览")
        self.tabs.addTab(self.issue_tab, "签发")
        self.tabs.addTab(self.vault_tab, "许可证库")
        self.tabs.addTab(self.verify_tab, "校验")
        self.tabs.addTab(self.settings_tab, "设置")
        self.tabs.addTab(self.help_tab, "原理")

        # 状态条
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._status_label = QWidget()
        from PyQt5.QtWidgets import QLabel
        self._status_text = QLabel("就绪")
        self.status_bar.addWidget(self._status_text)

        # 日志 dock
        dock = QDockWidget("操作日志", self)
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        dock.setWidget(self.log_widget)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)

        # 把 issue 页的 install_id 默认值同步到 config 读取后
        self.log("控制台启动；PKI 目录：" + str(self.config.pki_path()))
        # 启动即拉一次概览
        self.overview_tab.refresh()

    # ---- 共享方法（供各页签调用）-----------------------------
    def run(self, fn, on_done, label: str = ""):
        worker = Worker(fn, label)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(on_done)
        thread.finished.connect(thread.deleteLater)

        # 保留强引用，防止 worker 在 run() 返回后被 GC（Qt 跨线程对象被销毁会崩溃）
        self._workers.append(worker)
        self._threads.append(thread)
        thread.finished.connect(lambda: self._safe_remove(worker, thread))
        thread.start()

    def _safe_remove(self, worker, thread):
        try:
            self._threads.remove(thread)
        except ValueError:
            pass
        try:
            self._workers.remove(worker)
        except ValueError:
            pass

    def set_status(self, text: str, error: bool = False, level: str = ""):
        color = ""
        if error or level == "err":
            color = "#c0392b"
        elif level == "warn":
            color = "#b9770e"
        elif level == "ok":
            color = "#1e7e34"
        self._status_text.setText(text)
        self._status_text.setStyleSheet(f"color: {color};" if color else "")

    def log(self, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts}  {msg}"
        self.log_widget.append(line)
        try:
            with open(self.log_path, "a", encoding="utf-8") as fd:
                fd.write(line + "\n")
        except Exception:
            pass

    def sign_and_prepare(self, install_id, years, name, internal, external, flags):
        jwt, payload = licenses.sign(
            self.config.pki_path(), install_id, years, name, internal, external, flags
        )
        steps = licenses.verify(jwt, self.config.pki_path(), install_id)["steps"]
        return {"jwt": jwt, "payload": payload, "steps": steps}

    def save_issued(self, jwt, payload, install_id):
        return licenses.save_issued(self.config.issued_path(), jwt, payload, install_id)

    def verify_jwt(self, jwt, install_id):
        return licenses.verify(jwt, self.config.pki_path(), install_id)

    def list_issued(self):
        return licenses.list_issued(self.config.issued_path(), self.config.pki_path())


def main() -> int:
    args = sys.argv[1:]
    config_path = CONFIG_PATH_DEFAULT
    if "--config" in args:
        i = args.index("--config")
        if i + 1 < len(args):
            config_path = Path(args[i + 1])

    app = QApplication(sys.argv)
    # 样式
    style_path = Path(__file__).resolve().parent / "styles.qss"
    if style_path.exists():
        try:
            app.setStyleSheet(style_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    win = MainWindow(config_path)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
