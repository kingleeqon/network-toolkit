# -*- coding: utf-8 -*-
"""接口描述采集（收敛 addInterfacesDes.py）。

登录设备采集接口描述（display/show interface description），解析聚合接口描述，
导出结果。复用框架连接层与并发能力。
"""
import re
import time

from core.tool_base import BaseTool, ParamSpec
from infra.connector import run_e1000
from infra.vendor_profiles import get_e1000, match_vendor

# 各厂商「接口描述」命令（key 为 match_vendor 归一后的标准厂商 key）
# 取值对齐原 addInterfaceDes 脚本：烽火用 show interface（与 CRC 采集的
# show interface verbose 不同，属正常——两个工具用途不同）
_DESC_CMDS = {
    "huawei": "display interface description",
    "h3c": "display interface description",
    "ruijie": "show interfaces description",
    "zte": "show interface description",
    "fiberhome": "show interface",
}

# 聚合接口名模式
_AGG_PATTERN = r"\b(Eth-Trunk\d+|Port-Group\d+|Agg\d+|AggregatePort\d+|AE\d+|smartgroup\d+)\b"
# 描述模式：pT:/uT:/dT: 后跟内容
_DESC_PATTERN = r"(pT[:|-]|uT[:|-]|dT[:|-])(.*?)$"


class IfaceDescTool(BaseTool):
    id = "iface_desc"
    name = "接口描述采集"
    version = "1.0.0"
    category = "采集分析类"
    description = "批量采集设备接口描述（聚合口），导出 Excel"

    params = [
        ParamSpec(key="devices", label="设备清单", type="device_list", required=True,
                  ext=".xlsx;.csv;.txt"),
        ParamSpec(key="concurrency", label="并发数", type="int", default=10, min=1, max=50),
        ParamSpec(key="timeout", label="超时(秒)", type="int", default=30, min=5, max=300),
    ]

    # 设备清单跑完必须有行；0 行说明一台都没处理 → 判失败
    empty_result_ok = False

    result_columns = ["设备名", "IP", "本地接口", "接口描述", "状态", "失败原因"]

    def run(self, params, ctx, emit):
        devices = ctx.load_devices(params.get("devices"))
        if not devices:
            emit("done", {"summary": {"total": 0}})
            return

        concurrency = int(params.get("concurrency", 10))
        timeout = int(params.get("timeout", 30))
        warned_vendors = set()

        def worker(dev):
            # 先归一厂商再查命令：清单里写「华为/锐捷/ruijie_os」都能命中
            key = match_vendor(dev.vendor)
            cmd = _DESC_CMDS.get(key)
            if not cmd:
                raw = dev.vendor or "(空)"
                if raw not in warned_vendors:
                    warned_vendors.add(raw)
                    ctx.log("厂商「%s」未收录接口描述命令，回退华为命令" % raw, level="WARN")
                cmd = _DESC_CMDS["huawei"]
            with ctx.open_session(dev, timeout=timeout) as session:
                if get_e1000(dev.ip):
                    # 华为 E1000 的 screen-length 0 temporary 不生效，
                    # 不走进 vty 那套会被分页截断（现场脚本同样特殊处理）
                    text = run_e1000(session, cmd, timeout)
                else:
                    text = session.send(cmd, timeout=timeout)
            return self._parse(text, dev)

        def row_worker(dev):
            # 返回可能有多行：用单行聚合结果（多接口用分号拼接），保证与列等长
            parsed = worker(dev)
            if not parsed:
                return [dev.hostname, dev.ip, "", "", "", ""]
            first = parsed[0]
            return [dev.hostname, dev.ip, first[0], first[1], "", ""]

        ctx.log("开始采集接口描述：%d 台设备" % len(devices))
        ctx.run_parallel(devices, row_worker, result_columns=self.result_columns,
                         max_workers=concurrency)
        emit("done", {"summary": {"total": len(devices)}})

    @staticmethod
    def _parse(output, dev):
        results = []
        for line in (output or "").split("\n"):
            if re.search(_AGG_PATTERN, line):
                m = re.search(_AGG_PATTERN, line)
                if m:
                    interface = m.group(0)
                    remaining = line[m.end():].strip()
                    d = re.search(_DESC_PATTERN, remaining)
                    if d:
                        desc_type = d.group(1)
                        desc = d.group(2).strip()
                        if " " in desc:
                            desc = desc.split(" ")[0]
                        results.append((interface, "%s%s" % (desc_type, desc)))
        return results
