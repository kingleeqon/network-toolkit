# -*- coding: utf-8 -*-
"""演示工具：不连接真实设备，用于验证框架全链路（队列、并发、取消、异常隔离、结果落盘）。

新工具接入时可直接复制本文件作为模板。
"""
import random
import socket
import time

from core.errors import ParseError
from core.tool_base import BaseTool, ParamSpec
from infra.device_loader import Device


class DemoScanTool(BaseTool):
    id = "demo_scan"
    name = "演示：批量模拟采集"
    version = "1.0.0"
    category = "示例"
    description = "无需真实设备，用于验证框架闭环（并发/进度/失败隔离/中止/导出）"

    params = [
        ParamSpec(key="count", label="模拟设备数", type="int", default=20, min=1, max=200,
                  help="生成虚拟设备数量"),
        ParamSpec(key="concurrency", label="并发数", type="int", default=10, min=1, max=50),
        ParamSpec(key="fail_rate", label="失败率(%)", type="int", default=15, min=0, max=100,
                  help="随机制造失败，用于验证异常隔离"),
    ]

    result_columns = ["IP", "设备名", "厂商", "状态", "耗时(ms)", "失败原因"]

    def run(self, params, ctx, emit):
        count = int(params.get("count", 20))
        concurrency = int(params.get("concurrency", 10))
        fail_rate = int(params.get("fail_rate", 15))

        devices = []
        for i in range(count):
            devices.append(Device(ip="10.0.0.%d" % (i + 1), hostname="demo-%02d" % (i + 1),
                                  vendor="huawei", credential_ref="__demo__"))

        def worker(dev):
            start = time.time()
            time.sleep(random.uniform(0.05, 0.3))
            elapsed = int((time.time() - start) * 1000)

            roll = random.randint(1, 100)
            if roll <= fail_rate:
                kind = roll % 3
                if kind == 0:
                    raise socket.timeout("模拟连接超时")
                if kind == 1:
                    raise ConnectionRefusedError("模拟连接被拒绝")
                raise ParseError("模拟回显解析失败")

            return [dev.ip, dev.hostname, dev.vendor, "OK", elapsed, ""]

        ctx.log("开始模拟采集：%d 台，并发 %d，失败率 %d%%" % (count, concurrency, fail_rate))
        ctx.run_parallel(devices, worker, result_columns=self.result_columns, max_workers=concurrency)
        emit("done", {"summary": {"total": count}})
