#!/usr/bin/env python3
"""许可证管理控制台启动器。

用法（在 tools/enterprise-license/ 目录下）：
    python run_console.py                # 用默认 console/config.json
    python run_console.py --config x.json

也可用：python -m console.main
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from console.main import main

if __name__ == "__main__":
    sys.exit(main())
