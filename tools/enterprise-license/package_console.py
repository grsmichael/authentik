#!/usr/bin/env python3
"""把许可证管理控制台打包成独立可执行文件（PyInstaller）。

用法（在 tools/enterprise-license/ 目录下）：
    pip install pyinstaller
    python package_console.py
构建产物在 dist/LicenseConsole/（Windows 为 LicenseConsole.exe）。

说明：
- 入口用 run_console.py（它把本目录加入 sys.path，使 console 包可导入）。
- styles.qss 作为数据文件打包到 console/ 旁边（运行时按 __file__ 同级读取）。
- 源码运行方式始终保留：直接 `python run_console.py` 即可，无需打包。
- 本脚本仅做打包，不修改任何业务逻辑。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import PyInstaller.__main__ as pyi
except ImportError:  # noqa: BLE001
    sys.exit("未找到 PyInstaller，请先执行：pip install pyinstaller")

HERE = Path(__file__).resolve().parent
ENTRY = HERE / "run_console.py"
STYLES = HERE / "console" / "styles.qss"
SEP = ";" if os.name == "nt" else ":"

if not STYLES.exists():
    sys.exit(f"缺少 {STYLES}")

argv = [
    str(ENTRY),
    "--name", "LicenseConsole",
    "--windowed",  # GUI 应用，不弹黑框（日志已落盘 console/console.log）
    "--paths", str(HERE),
    "--add-data", f"{STYLES}{SEP}console",
    "--hidden-import", "console",
    "--hidden-import", "console.widgets",
    "--hidden-import", "console.widgets.overview",
    "--hidden-import", "console.widgets.issue",
    "--hidden-import", "console.widgets.vault",
    "--hidden-import", "console.widgets.verify",
    "--hidden-import", "console.widgets.settings",
    "--hidden-import", "console.widgets.help",
    "--clean",
    "--noconfirm",
]

if __name__ == "__main__":
    print("开始打包（PyInstaller）…")
    pyi.run(argv)
    print(f"构建完成，产物在 {HERE / 'dist' / 'LicenseConsole'}")
