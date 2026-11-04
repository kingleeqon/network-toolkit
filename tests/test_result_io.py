# -*- coding: utf-8 -*-
"""验证结果行落盘契约：write_rows 写入 → read_rows 读回（定时结果表靠它回填）。

运行：python tests/test_result_io.py
"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.result_writer import read_rows, write_rows

fails = []


def check(name, got, expect):
    ok = got == expect
    print("  %-52s %s" % (name, "OK" if ok else "FAIL got=%r" % (got,)))
    if not ok:
        fails.append(name)


print("=== ① 往返：写进去能原样读回来 ===")
d = Path(tempfile.mkdtemp())
cols = ["IP", "设备名", "状态", "失败原因"]
rows = [["10.0.0.1", "dev1", "成功", ""],
        ["10.0.0.2", "dev2", "连接超时", "连接超时"]]
statuses = ["OK", "FAIL_TIMEOUT"]
write_rows(d, cols, rows, statuses)
got_cols, got_rows = read_rows(d)
check("列一致", got_cols, cols)
check("行数一致", len(got_rows), 2)
check("第一行内容", got_rows[0], (rows[0], "OK"))
check("失败行状态", got_rows[1][1], "FAIL_TIMEOUT")

print("=== ② 中文/空值不丢 ===")
d2 = Path(tempfile.mkdtemp())
write_rows(d2, ["说明", "状态"], [["含中文的说明", "部分成功"], ["", ""]], ["PARTIAL_OK", "OK"])
cols2, rows2 = read_rows(d2)
check("中文保留", rows2[0][0][0], "含中文的说明")
check("空串保留", rows2[1][0], ["", ""])

print("=== ③ 缺文件/坏文件不炸 ===")
check("目录里没有 result.json", read_rows(Path(tempfile.mkdtemp())), ([], []))
d3 = Path(tempfile.mkdtemp())
(d3 / "result.json").write_text("{不是合法 json", encoding="utf-8")
check("坏 json 返回空", read_rows(d3), ([], []))

print("=== ④ 没传 statuses 时默认 OK ===")
d4 = Path(tempfile.mkdtemp())
write_rows(d4, ["A"], [["x"]])
check("默认状态 OK", read_rows(d4)[1][0][1], "OK")

print()
if fails:
    print("[FAIL] %d 项不通过：%s" % (len(fails), "、".join(fails)))
    sys.exit(1)
print("[OK] 全部通过")
