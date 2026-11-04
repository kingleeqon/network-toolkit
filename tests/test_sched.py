# -*- coding: utf-8 -*-
"""验证：多任务定时器（存储/状态/立即执行）+ 工具定时条 + 总览窗口。

运行：python tests/test_sched.py（需要带 ttkbootstrap 的 Python）
"""
import json, sys, time
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent   # 项目根目录（本文件在 tests/ 下）
sys.path.insert(0, str(BASE))
from core.scheduler import SchedulerManager, PIPELINE_KEY

STORE = Path(r"C:/temp/sched_test/schedules.json")
if STORE.exists(): STORE.unlink()
logs = []
mgr = SchedulerManager(store_path=STORE, on_log=logs.append)

print("=== ① 设定两个定时 ===")
mgr.set_schedule("iface_collect", at="09:00", enabled=True,
                  params={"devices": "D:/test/device.xlsx", "concurrency": "5",
                          "timeout": "40", "backup_config": "true"})
mgr.set_schedule("batch_backup", at="09:30", enabled=True,
                  params={"devices": "D:/test/device.xlsx"})
mgr.set_schedule(PIPELINE_KEY, at="08:30", enabled=False)
print("   schedules.json:", json.dumps(json.loads(STORE.read_text(encoding="utf-8")),
                                  ensure_ascii=False)[:160])

print("\n=== ② 状态 ===")
for k in ("iface_collect", "batch_backup", PIPELINE_KEY, "crc_compare"):
    print("   %-14s %-16s %s" % (k, mgr.title_of(k), mgr.status_of(k)))
print("   下次执行(iface_collect):", mgr.next_run_at("iface_collect"))

print("\n=== ③ 生成的命令 ===")
cmd, cwd = mgr.build_cmd("iface_collect")
print("   ", " ".join(cmd[1:]))
cmd2, _ = mgr.build_cmd(PIPELINE_KEY)
print("   ", " ".join(cmd2[1:]))

print("\n=== ④ 立即执行（demo_scan，最快）===")
# ④-a 全部成功 → 退出码 0
mgr.set_schedule("demo_scan", at="23:59", enabled=False,
                 params={"count": 6, "concurrency": 3, "fail_rate": 0})
ok, msg = mgr.run_now("demo_scan")
print("   ", msg)
for _ in range(150):
    if not mgr.is_running("demo_scan"): break
    time.sleep(0.1)
print("   跑完后状态:", mgr.status_of("demo_scan"))
last = mgr.get("demo_scan").get("last")
print("   last 记录:", last, "→ 期望 code=0", "PASS" if last.get("code") == 0 else "FAIL")

# ④-b 部分失败 → 退出码 2（跑完但有失败项）
mgr.set_schedule("demo_scan", params={"count": 20, "concurrency": 5, "fail_rate": 50})
ok, msg = mgr.run_now("demo_scan")
for _ in range(150):
    if not mgr.is_running("demo_scan"): break
    time.sleep(0.1)
last = mgr.get("demo_scan").get("last")
print("   部分失败 last:", last, "→ 期望 code=2", "PASS" if last.get("code") == 2 else "FAIL")

# ④-c 全部失败 → 退出码 1（FAILED，与「部分失败」区分）
mgr.set_schedule("demo_scan", params={"count": 6, "concurrency": 3, "fail_rate": 100})
ok, msg = mgr.run_now("demo_scan")
for _ in range(150):
    if not mgr.is_running("demo_scan"): break
    time.sleep(0.1)
last = mgr.get("demo_scan").get("last")
print("   全部失败 last:", last, "→ 期望 code=1", "PASS" if last.get("code") == 1 else "FAIL")
print("   日志片段:", logs[:2])

print("\n=== ⑤ 一天只跑一次 ===")
print("   ran_today(demo_scan):", mgr.ran_today("demo_scan"))

print("\n=== ⑤b 触发判定：刚配的任务不能因今天已过点就补跑 ===")
# 用户反馈：15 点配一个 2 点的任务，刚配完就跑了
from datetime import datetime as _dt, timedelta as _td
STORE2 = Path(r"C:/temp/sched_test/schedules_trigger.json")
if STORE2.exists(): STORE2.unlink()
fired = []
mgr2 = SchedulerManager(store_path=STORE2)
mgr2.run_now = lambda key: fired.append(key) or (True, "ok")   # 只看触发判定

_past = _dt.now() - _td(hours=3)
_past_at = "%d:%02d" % (_past.hour, _past.minute)

mgr2.set_schedule("fresh", at=_past_at, enabled=True)   # 刚配、今天已过点
mgr2._tick()
print("   刚配的已过点任务触发了吗:", "fresh" in fired, "→ 期望 False", "PASS" if "fresh" not in fired else "FAIL")

mgr2.set_schedule("future", at="23:59", enabled=True)   # 配的是未来时间
mgr2._tick()
print("   未来时间任务触发了吗:", "future" in fired, "→ 期望 False", "PASS" if "future" not in fired else "FAIL")

mgr2.set_schedule("stale", at=_past_at, enabled=True)   # 昨天配的：正常补跑
_d = mgr2._read()
_d["stale"]["updated_at"] = (_dt.now() - _td(days=1)).strftime("%Y-%m-%d %H:%M:%S")
mgr2._write(_d)
mgr2._tick()
print("   昨天配的已过点任务触发了吗:", "stale" in fired, "→ 期望 True", "PASS" if "stale" in fired else "FAIL")

_before = mgr2.get("fresh").get("updated_at")
time.sleep(1.1)
mgr2.set_schedule("fresh", params={"x": 1})   # 只改参数（自动同步）
print("   只改参数 updated_at 变了吗:",
      mgr2.get("fresh").get("updated_at") != _before, "→ 期望 False",
      "PASS" if mgr2.get("fresh").get("updated_at") == _before else "FAIL")

print("\n=== ⑥ 界面：工具定时条 + 总览窗口 ===")
import ttkbootstrap as ttk
import ui.dpi as dpi
import ui.schedule_overview as so
from ui.main_window import MainWindow
for n in ("show_info", "show_warning", "show_error"):
    setattr(so.Messagebox, n, staticmethod(lambda *a, **k: None))
so.Messagebox.yesno = staticmethod(lambda *a, **k: True)

dpi.set_dpi_awareness()
root = ttk.Window(themename="cosmo", size=(1180, 760), hdpi=True)
dpi.configure_fonts(root)
mw = MainWindow(root, tools_dir=BASE/"tools", output_base=str(BASE/"output"), app_version="1")
root.update_idletasks(); time.sleep(0.5); root.update()
# 指向测试 store
mw.scheduler.store_path = STORE
mw.tool_tree.selection_set("iface_collect"); mw.on_select_tool()
root.update_idletasks(); time.sleep(0.4); root.update()
print("   定时条 时间框:", mw.var_sched_at.get(), " 启用:", mw.var_sched_on.get())
print("   定时条 状态:", mw.lbl_sched.cget("text"))
mw.var_sched_at.set("11:15")
mw._save_sched()
root.update_idletasks(); time.sleep(0.3); root.update()
print("   改时间后 → 存盘:", json.loads(STORE.read_text(encoding="utf-8"))["iface_collect"]["at"])
print("   状态栏:", repr(mw.sched_label.cget("text")))

mw.show_auto_task()
ov = root._schedule_overview
root.update_idletasks(); time.sleep(0.5); root.update()
rows = ov.tree.get_children()
print("   总览行数:", len(rows))
for r in rows[:4]:
    print("     ", ov.tree.item(r, "values"))

print("\n=== ⑥b 定时运行的结果行回填结果表 ===")
# 定时是子进程跑的，结果行走不到界面消息，靠任务目录里的 result.json 回填
mw.tool_tree.selection_set("demo_scan"); mw.on_select_tool()
root.update_idletasks(); time.sleep(0.3); root.update()
mw.scheduler.set_schedule("demo_scan", at="23:59", enabled=False,
                          params={"count": 3, "concurrency": 2, "fail_rate": 0})
mw.scheduler.run_now("demo_scan")
_t0 = time.time()
while time.time() - _t0 < 60:
    root.update()
    if (not mw.scheduler.is_running("demo_scan") and mw._sched_q.empty()
            and time.time() - _t0 > 4):
        break
    time.sleep(0.05)
_n = len(mw.result_view.rows)
print("   结果表行数:", _n, "→ 期望 3", "PASS" if _n == 3 else "FAIL")
print("   表格节点数:", len(mw.result_view.tree.get_children()),
      "→ 期望 3", "PASS" if len(mw.result_view.tree.get_children()) == 3 else "FAIL")
root.destroy()
