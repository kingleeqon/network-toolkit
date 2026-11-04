# -*- coding: utf-8 -*-
"""验证：采集类工具的行为（命令选择 / 状态语义 / 归档内容）。

用假会话代替真实设备，不连网。运行：python tests/test_tools.py
"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent   # 项目根目录（本文件在 tests/ 下）
sys.path.insert(0, str(BASE))

from core.task_context import TaskContext
from infra.device_loader import Device
import tools.batch_backup as bb
import tools.iface_collect as ic

fails = []


def check(name, got, expect):
    ok = got == expect
    print("  %-52s %s" % (name, "OK" if ok else "FAIL got=%r" % (got,)))
    if not ok:
        fails.append(name)


class FakeSession(object):
    """假会话：记录发过的命令，按模式返回回显。"""

    def __init__(self, mode, log):
        self.mode = mode
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send_timing(self, cmd, timeout=10):
        # 真会话有这个方法（E1000 序列用它发 sys / user-interface 等）
        self.log.append(cmd)
        return ""

    def send(self, cmd, timeout=30, end_marker=None):
        self.log.append(cmd)
        if self.mode == "connect_fail":
            raise ConnectionRefusedError("模拟登录不上")
        is_config = ("running-config" in cmd or "current-configuration" in cmd)
        if self.mode == "cfg_fail" and is_config:
            raise RuntimeError("模拟配置命令失败")
        if self.mode == "short":
            return "x" * 30
        if is_config:
            # 烽火配置：以 !end 收尾；长度要够（低于 100 字符会判「可能不完整」）
            return ("!Software Version V800\nsysname DEV1\n"
                    "interface GE0/0/1\n description uplink\n"
                    "interface GE0/0/2\n description downlink\n" * 5 + "!end\n")
        return "!Software Version V800\ninterface GE0/0/1\n  CRC: 0\n" * 10


class FakeCtx(TaskContext):
    def __init__(self, mode, out, devices):
        super().__init__("t", out, lambda t, p: None, None)
        self.mode = mode
        self.devices = devices
        self.cmds = []
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def load_devices(self, value):
        return self.devices

    def open_session(self, device, timeout=30, paging_cmd=None):
        return FakeSession(self.mode, self.cmds)


def run(tool, mode, devices, params=None):
    out = Path(tempfile.mkdtemp())
    msgs = []
    ctx = FakeCtx(mode, out, devices)
    ctx._emit = lambda t, p: msgs.append((t, p))
    tool.run(params or {}, ctx, lambda *a, **k: None)
    results = [p for t, p in msgs if getattr(t, "value", t) == "result"]
    logs = [p["msg"] for t, p in msgs if getattr(t, "value", t) == "log"]
    return results, logs, ctx, out


HW = [Device(ip="10.0.0.1", hostname="DEV1", vendor="huawei", credential_ref="x")]
FH = [Device(ip="10.0.0.2", hostname="DP-FW-01", vendor="fiberhome", credential_ref="x")]
FH_SW = [Device(ip="10.0.0.3", hostname="FH-SW-01", vendor="fiberhome", credential_ref="x")]

print("=== ① 接口采集：状态语义 ===")
res, logs, ctx, out = run(ic.IfaceCollectTool(), "ok", HW)
check("正常 → OK", res[0]["status"], "OK")

cfgdir = tempfile.mkdtemp()
res, logs, ctx, out = run(ic.IfaceCollectTool(), "cfg_fail", HW,
                           {"backup_config": True, "backup_dir": cfgdir})
check("配置备份失败 → 部分成功", res[0]["status"], "PARTIAL_OK")
check("失败原因写明配置失败", "配置备份失败" in res[0]["row"][6], True)
check("配置失败时没生成 .cfg", len(list(Path(cfgdir).rglob("*.cfg"))), 0)

res, logs, ctx, out = run(ic.IfaceCollectTool(), "short", HW)
check("回显过短 → 部分成功", res[0]["status"], "PARTIAL_OK")
check("回显过短有告警", any("不完整" in l for l in logs), True)

print("=== ② 烽火桶内按设备名分产品 ===")
res, logs, ctx, out = run(ic.IfaceCollectTool(), "ok", FH)
check("DP 设备接口命令", ctx.cmds[0], "show interface")

res, logs, ctx, out = run(ic.IfaceCollectTool(), "ok", FH_SW)
check("烽火交换机接口命令", ctx.cmds[0], "show interface verbose")

print("=== ③ 接口采集顺带备份配置：拿到的是配置不是接口 ===")
cfgdir = tempfile.mkdtemp()
res, logs, ctx, out = run(ic.IfaceCollectTool(), "ok", FH_SW,
                           {"backup_config": True, "backup_dir": cfgdir})
cfg_files = list(Path(cfgdir).rglob("*.cfg"))
check("生成了 .cfg", len(cfg_files), 1)
body = cfg_files[0].read_text(encoding="utf-8")
check("配置内容以 !end 结尾", body.rstrip().endswith("!end"), True)
check("配置内容不是接口回显", "CRC: 0" in body, False)
check("未报「没采全」", any("结束标记" in l for l in logs), False)

print("=== ④ 配置备份：状态语义 ===")
res, logs, ctx, out = run(bb.BatchBackupTool(), "ok", FH_SW, {"archive_dir": ""})
check("正常 → OK", res[0]["status"], "OK")
check("配置命令是 running-config", ctx.cmds[0], "show running-config")

res, logs, ctx, out = run(bb.BatchBackupTool(), "short", FH_SW, {"archive_dir": ""})
check("配置过短 → 部分成功", res[0]["status"], "PARTIAL_OK")
check("备注写明可能不完整", "可能不完整" in res[0]["row"][6], True)

print("=== ⑤ 登录失败的设备：留失败清单，不留半截文件 ===")
archivedir = tempfile.mkdtemp()
res, logs, ctx, out = run(ic.IfaceCollectTool(), "connect_fail", FH_SW,
                          {"archive_dir": archivedir})
check("失败设备状态是失败", res[0]["status"] not in ("OK", "PARTIAL_OK"), True)
err_logs = list(Path(archivedir).rglob("error_log.txt"))
check("生成了 error_log.txt", len(err_logs), 1)
check("清单里有设备名和原因",
      ("FH-SW-01" in err_logs[0].read_text(encoding="utf-8")), True)
check("没有生成半截 .log",
      len(list(Path(archivedir).rglob("*.log"))), 0)

archivedir2 = tempfile.mkdtemp()
res, logs, ctx, out = run(bb.BatchBackupTool(), "connect_fail", FH_SW,
                           {"archive_dir": archivedir2})
err_logs2 = list(Path(archivedir2).rglob("error_log.txt"))
check("配置备份也生成 error_log.txt", len(err_logs2), 1)
check("配置备份没有生成半截 .cfg",
      len(list(Path(archivedir2).rglob("*.cfg"))), 0)

print("=== ⑥ 华为 E1000：接口和配置都要走 vty 关屏显序列 ===")
# 现场反馈：E1000 采接口正常，但同会话备份配置报 Pattern not detected。
# 根因是配置命令在 undo screen-length 之后才发（分页已恢复、提示符又变了）。
E1000_DEV = [Device(ip="10.185.2.3", hostname="TTOA-BS-2-4F-B08-E1000E-01",
                    vendor="huawei", credential_ref="x")]
cfgdir3 = tempfile.mkdtemp()
res, logs, ctx, out = run(ic.IfaceCollectTool(), "ok", E1000_DEV,
                          {"backup_config": True, "backup_dir": cfgdir3})
cmds = ctx.cmds
print("   发出的命令序列:", cmds)
check("接口命令前有 sys", cmds[0], "sys")
check("进了 vty", cmds[1], "user-interface vty 0 4")
check("关了屏显", cmds[2], "screen-length 0")
check("收尾回到用户视图", "return" in cmds, True)
# 配置命令必须也夹在 vty 序列里（它前面还得再有一次 sys）
idx_cfg = next((i for i, c in enumerate(cmds) if "current-configuration" in c), -1)
check("配置命令在序列里", idx_cfg > 0, True)
check("配置命令前重新进了 sys",
      cmds[idx_cfg - 3:idx_cfg] == ["sys", "user-interface vty 0 4", "screen-length 0"],
      True)
check("E1000 也生成了 .cfg", len(list(Path(cfgdir3).rglob("*.cfg"))), 1)

print("=== ⑦ 接口描述采集：E1000 同样要走 vty 序列 ===")
import tools.iface_desc as ide
res, logs, ctx, out = run(ide.IfaceDescTool(), "ok", E1000_DEV)
print("   发出的命令序列:", ctx.cmds)
check("描述采集也进 vty", "user-interface vty 0 4" in ctx.cmds, True)
check("描述采集也关屏显", "screen-length 0" in ctx.cmds, True)
check("描述采集收尾 return", "return" in ctx.cmds, True)

print()
if fails:
    print("[FAIL] %d 项不通过：%s" % (len(fails), "、".join(fails)))
    sys.exit(1)
print("[OK] 全部通过")
