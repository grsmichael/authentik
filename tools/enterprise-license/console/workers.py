"""工作线程：把所有阻塞调用（数据库 / docker / 签发）放进后台线程，避免 UI 假死。

采用 PyQt5 最稳妥的 QThread + QObject 模式（QRunnable 不是 QObject，无法直接挂 pyqtSignal）。
统一的 Worker 把任意无参可调用包起来，完成时发 finished(dict)：
- ok=True  → dict 含 "result"
- ok=False → dict 含 "stage" / "detail"
"""
from __future__ import annotations

from PyQt5.QtCore import QObject, QThread, pyqtSignal


class Worker(QObject):
    finished = pyqtSignal(dict)

    def __init__(self, fn, label: str = ""):
        super().__init__()
        self.fn = fn
        self.label = label

    def run(self) -> None:
        try:
            result = self.fn()
            self.finished.emit({"ok": True, "result": result, "label": self.label})
        except Exception as exc:  # noqa: BLE001
            stage = getattr(exc, "stage", type(exc).__name__)
            detail = getattr(exc, "detail", str(exc))
            self.finished.emit({"ok": False, "stage": str(stage), "detail": str(detail), "label": self.label})
