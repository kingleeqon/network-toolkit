# -*- coding: utf-8 -*-
"""跑全部验证脚本：python tests/run_all.py

自动发现 tests/test_*.py（按名字排序）逐个跑，新增测试文件不用改这里。
需要 GUI 的脚本在缺 ttkbootstrap 时自动跳过并说明原因（不算失败）。

也可以单独跑：python tests/test_xxx.py
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent

# 需要 tkinter + ttkbootstrap 的脚本：缺依赖时跳过而不是失败
GUI_SCRIPTS = {"test_sched.py"}


def discover():
    return sorted(p.name for p in TESTS_DIR.glob("test_*.py"))


def main():
    scripts = discover()
    if not scripts:
        print("没找到测试脚本（tests/test_*.py）")
        return 1
    print("发现 %d 个测试脚本：%s" % (len(scripts), "、".join(scripts)))

    failed = []
    skipped = []
    for name in scripts:
        print("\n" + "=" * 60)
        print("▶ %s" % name)
        print("=" * 60)
        if name in GUI_SCRIPTS and importlib.util.find_spec("ttkbootstrap") is None:
            print("[跳过] 当前 Python 没有 ttkbootstrap，请用装了依赖的解释器跑 %s" % name)
            skipped.append(name)
            continue
        proc = subprocess.run([sys.executable, str(TESTS_DIR / name)], cwd=str(ROOT))
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
