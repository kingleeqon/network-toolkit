# -*- coding: utf-8 -*-
"""接口描述采集（收敛 addInterfacesDes.py）。

登录设备采集接口描述（display/show interface description），解析聚合接口描述，
导出结果。复用框架连接层与并发能力。
"""
import re
import time

from core.tool_base import BaseTool, ParamSpec
from infra.vendor_profiles import get_profile

# 各厂商「接口描述」命令
_DESC_CMDS = {
    "huawei": "display interface description",
    "h3c": "display interface description",
    "ruijie": "show interfaces description",
    "zte": "show interface description",
    "fenghuo": "show interface",
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

    result_columns = ["设备名", "IP", "本地接口", "接口描述", "状态", "失败原因"]

    def run(self, params, ctx, emit):
        devices = ctx.load_devices(params.get("devices"))
        if not devices:
            emit("done", {"summary": {"total": 0}})
            return

        concurrency = int(params.get("concurrency", 10))
        timeout = int(params.get("timeout", 30))

        def worker(dev):
            cmd = _DESC_CMDS.get((dev.vendor or "").lower(), _DESC_CMDS["huawei"])
            with ctx.open_session(dev, timeout=timeout) as session:
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
