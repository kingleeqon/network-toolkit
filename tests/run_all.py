# -*- coding: utf-8 -*-
"""跑全部验证脚本：python tests/run_all.py

三个脚本各自独立可跑（python tests/test_xxx.py），这里只是串起来跑一遍，
适合改完代码后做一次总回归。

test_sched.py 需要 tkinter + ttkbootstrap；当前解释器缺 ttkbootstrap 时
会自动跳过它（不算失败），换成装了依赖的 Python 再跑那个即可。
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent

SCRIPTS = ["test_fenghuo.py", "test_tools.py", "test_sched.py"]
GUI_SCRIPTS = {"test_sched.py"}


def main():
    failed = []
    skipped = []
    for name in SCRIPTS:
        print("\n" + "=" * 60)
        print("▶ %s" % name)
        print("=" * 60)
        if name in GUI_SCRIPTS and importlib.util.find_spec("ttkbootstrap") is None:
            print("[跳过] 当前 Python 没有 ttkbootstrap，请用装了依赖的解释器跑 %s" % name)
            skipped.append(name)
            continue
        proc = subprocess.run([sys.executable, str(TESTS_DIR / name)],
                              cwd=str(ROOT))
        if proc.returncode != 0:
            failed.append(name)
    print("\n" + "=" * 60)
    if skipped:
        print("跳过 %d 个（缺 GUI 依赖）：%s" % (len(skipped), "、".join(skipped)))
    if failed:
        print("[FAIL] %d 个脚本未通过：%s" % (len(failed), "、".join(failed)))
        return 1
    print("[OK] 全部脚本通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
