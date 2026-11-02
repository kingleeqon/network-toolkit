# -*- coding: utf-8 -*-
"""验证：烽火/迪普的产品分流、结束标记、paramiko 读取完整性。

对照现场脚本：
- D:\\test\\object\\InterfaceCollectAndanalysis&comparison\\netmiko_SSH_device_system.py
- D:\\test\\object\\BackupConifg\\netmiko_SSH.py
"""
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent   # 项目根目录（本文件在 tests/ 下）
sys.path.insert(0, str(BASE))

from infra.connector import ParamikoSession
from infra.vendor_profiles import (get_cmd, get_device_type_label, get_end_marker,
                                 get_paging_cmd, get_product)

fails = []


def check(name, got, expect):
    ok = got == expect
    print("  %-52s %s" % (name, "OK" if ok else "FAIL got=%r" % (got,)))
    if not ok:
        fails.append(name)


print("=== ① 厂商列写「烽火」：按设备名分产品 ===")
for dev_name, product, iface, label, paging in [
        ("DP-FIREWALL-01", "firewall", "show interface", "DPtech", "terminal line 0"),
        ("FW1000-SW-02", "firewall", "show interface", "DPtech", "terminal line 0"),
        ("BASS-GX-7F-E10-WB-S6510-02", "switch", "show interface verbose",
         "FiberHome", "terminal length 0"),
        ("MSS-WX-2F-I102", "switch", "show interface verbose",
         "FiberHome", "terminal length 0")]:
    check("product(%s)" % dev_name, get_product("fiberhome", dev_name), product)
    check("interface(%s)" % dev_name,
          get_cmd("fiberhome", "interface", device_name=dev_name), iface)
    check("label(%s)" % dev_name,
          get_device_type_label("fiberhome", dev_name), label)
    check("paging(%s)" % dev_name,
          get_paging_cmd("fiberhome", "1.1.1.1", dev_name), paging)

print("=== ② 配置命令与结束标记 ===")
check("config cmd", get_cmd("fiberhome", "config", device_name="DP-FIREWALL-01"),
      "show running-config")
check("end marker", get_end_marker("fiberhome", "config", device_name="DP-SW"),
      "!end")
check("dipu interface", get_cmd("dipu", "interface"), "show interface")
check("dipu label", get_device_type_label("dipu"), "DPtech")

print("=== ②b 配置备份时的分屏命令 ===")
# 迪普防火墙：按产品用 terminal line 0
check("DP 设备备份分屏",
      get_paging_cmd("fiberhome", "172.20.249.88", "DP-FW-01", for_backup=True),
      "terminal line 0")
# 名单内 IP 的交换机：terminal line 0
check("名单内交换机备份分屏",
      get_paging_cmd("fiberhome", "172.20.249.88", "FH-SW-01", for_backup=True),
      "terminal line 0")
# 名单外的交换机：terminal length 0
check("名单外交换机备份分屏",
      get_paging_cmd("fiberhome", "10.9.9.9", "FH-SW-01", for_backup=True),
      "terminal length 0")


class FakeShell(object):
    """模拟 paramiko 通道：数据分批到达。"""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = []

    def recv_ready(self):
        return bool(self.chunks)

    def recv(self, n):
        time.sleep(0.05)
        return self.chunks.pop(0)

    def send(self, s):
        self.sent.append(s)


def mk(chunks):
    s = ParamikoSession.__new__(ParamikoSession)
    s.paging = ""
    s.shell = FakeShell(chunks)
    return s


print("=== ③ paramiko 读取完整性 ===")
# 分包到达，最后 !end
s = mk([b"!Software Version V800\nsysname DEV1\n",
        b"interface GE0/0/1\n", b"!end\n"])
out = s._read(timeout=5, end_marker="!end")
check("!end 读到完整配置", ("!end" in out and "GE0/0/1" in out), True)

# 分页符：回空格继续读
s = mk([b"line1\n", b"--More--", b"line2\n", b"dev# "])
out = s._read(timeout=5)
check("分页后拿到后续内容", "line2" in out, True)
check("分页时回了空格", " " in s.shell.sent, True)

# 提示符 + 空闲结束（不等满超时）
s = mk([b"output line\n", b"dev# "])
t0 = time.time()
out = s._read(timeout=10)
check("提示符后快速结束(%.1fs)" % (time.time() - t0), time.time() - t0 < 3, True)

# 发送前清残留：残留不会被当成本条命令的回显
s = mk(["上一条命令的残留内容\n".encode("utf-8")])
s.send("show running-config")
check("send 前清残留后发出命令", s.shell.sent[0], "show running-config\n")

print()
if fails:
    print("[FAIL] %d 项不通过：%s" % (len(fails), "、".join(fails)))
    sys.exit(1)
print("[OK] 全部通过")
