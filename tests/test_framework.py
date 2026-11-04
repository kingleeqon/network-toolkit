# -*- coding: utf-8 -*-
"""验证框架层并发与发现：异常隔离、取消补账、空列表、列数不匹配、工具发现。

运行：python tests/test_framework.py
"""
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.message import ItemStatus, MsgType
from core.registry import Registry
from core.task_context import TaskContext

fails = []


def check(name, got, expect):
    ok = got == expect
    print("  %-56s %s" % (name, "OK" if ok else "FAIL got=%r" % (got,)))
    if not ok:
        fails.append(name)


class Ctx(TaskContext):
    """捕获 emit 的上下文，落盘目录指向临时目录。"""

    def __init__(self, cancel_event=None):
        self.msgs = []
        out = Path(tempfile.mkdtemp())
        out.mkdir(parents=True, exist_ok=True)
        super().__init__("t", out, self._emit, None, cancel_event=cancel_event)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _emit(self, mtype, payload):
        self.msgs.append((mtype, payload))

    def rows(self):
        return [p for t, p in self.msgs if t == MsgType.RESULT]

    def logs(self, level=None):
        return [p["msg"] for t, p in self.msgs
                if t == MsgType.LOG and (level is None or p.get("level") == level)]


COLS = ["IP", "状态", "失败原因"]

print("=== ① 单台异常不影响其他（异常隔离）===")
ctx = Ctx()


def worker_a(item):
    if item == "bad":
        raise RuntimeError("模拟设备级异常")
    return [item, "", ""]


results = ctx.run_parallel(["a", "bad", "c"], worker_a, result_columns=COLS,
                           max_workers=3)
statuses = [s for _i, s in results]
check("三条结果都产出", len(results), 3)
check("失败项被判失败", statuses.count(ItemStatus.OK), 2)
check("异常项不是 OK", [s for s in statuses if s != ItemStatus.OK][0],
      ItemStatus.FAIL_UNKNOWN)
check("异常写进日志", any("执行失败" in m for m in ctx.logs()), True)

print("=== ② 取消：未跑的先 cancel，收尾补 SKIPPED 对齐条数 ===")
from threading import Event
ev = Event()
ev.set()                       # 进来就处于取消状态
ctx2 = Ctx(cancel_event=ev)


def slow(item):
    time.sleep(0.3)
    return [item, "", ""]


results2 = ctx2.run_parallel(["a", "b", "c", "d"], slow, result_columns=COLS,
                             max_workers=2)
check("条数与输入一致", len(results2), 4)
check("全部 SKIPPED", len([s for _i, s in results2 if s == ItemStatus.SKIPPED]), 4)

print("=== ③ 空列表 ===")
ctx3 = Ctx()
check("返回空列表", ctx3.run_parallel([], worker_a, result_columns=COLS), [])
check("发了一条 total=0 的进度", [p for t, p in ctx3.msgs
                                  if t == MsgType.PROGRESS][0]["total"], 0)

print("=== ④ 行列数不匹配的行被丢弃并记 ERROR ===")
ctx4 = Ctx()


def bad_row(item):
    return [item]              # 少两列


results4 = ctx4.run_parallel(["a", "b"], bad_row, result_columns=COLS, max_workers=2)
check("没有发出畸形行", len(ctx4.rows()), 0)
check("记了 ERROR", any("列数不匹配" in m for m in ctx4.logs("ERROR")), True)
check("内部结果仍记录状态", len(results4), 2)

print("=== ⑤ 工具发现：真实 tools 目录 ===")
reg = Registry(BASE / "tools")
reg.discover()
ids = [t.id for t in reg.tools]
check("工具数量 >= 12", len(ids) >= 12, True)
check("id 无重复", len(ids), len(set(ids)))
check("无加载错误", reg.load_errors, [])
check("能取到 iface_collect", reg.get("iface_collect") is not None, True)
check("取不存在的工具返回 None", reg.get("no_such_tool"), None)

print()
if fails:
    print("[FAIL] %d 项不通过：%s" % (len(fails), "、".join(fails)))
    sys.exit(1)
print("[OK] 全部通过")
