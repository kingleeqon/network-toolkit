# -*- coding: utf-8 -*-
"""中兴交换机合规检查（收敛 checkConfig.py）。

检查接口描述规范（启用接口须有 dT:/uT:/pT: 描述，未启用须 NO_USE）
与 SNMP 配置（团体字 + ACL 2333），输出检查报告 Excel。
"""
import re

from core.tool_base import BaseTool, ParamSpec

# 关注的接口类型
_IFACE_TYPES = ("xxvgei", "cgei", "mgmt_eth", "smartgroup", "vlan", "loopback")


class ZteCheckTool(BaseTool):
    id = "zte_check"
    name = "中兴交换机合规检查"
    version = "1.0.0"
    category = "检查类"
    description = "检查中兴交换机接口描述规范与 SNMP 配置合规性"

    params = [
        ParamSpec(key="devices", label="设备清单", type="device_list", required=True,
                  ext=".xlsx;.csv;.txt"),
        ParamSpec(key="concurrency", label="并发数", type="int", default=10, min=1, max=50),
        ParamSpec(key="timeout", label="超时(秒)", type="int", default=30, min=5, max=300),
    ]

    result_columns = ["IP", "设备名", "检查项", "状态", "详情", "失败原因"]

    def run(self, params, ctx, emit):
        devices = ctx.load_devices(params.get("devices"))
        if not devices:
            emit("done", {"summary": {"total": 0}})
            return

        concurrency = int(params.get("concurrency", 10))
        timeout = int(params.get("timeout", 30))

        def worker(dev):
            rows = []
            with ctx.open_session(dev, timeout=timeout) as session:
                brief = session.send("show interface brief", timeout=timeout)
                snmp = session.send("show running-config | include snmp", timeout=timeout)

            # 接口描述检查
            for iface in self._check_interfaces(brief):
                rows.append([dev.ip, dev.hostname, "接口:%s" % iface["name"],
                             "PASS" if iface["status"] == "PASS" else iface["status"],
                             iface.get("message", ""), ""])
            # SNMP 检查
            s = self._check_snmp(snmp)
            rows.append([dev.ip, dev.hostname, "SNMP配置", s["status"],
                         "; ".join(s["details"]), ""])

            # 返回多行会与 result_columns 冲突，取一行摘要 + 其余写日志
            return self._summarize(dev, rows)

        ctx.log("开始检查：%d 台设备" % len(devices))
        ctx.run_parallel(devices, worker, result_columns=self.result_columns,
                         max_workers=concurrency)
        emit("done", {"summary": {"total": len(devices)}})

    def _summarize(self, dev, rows):
        fails = [r for r in rows if r[3] == "FAIL"]
        warns = [r for r in rows if r[3] == "WARNING"]
        status = "FAIL" if fails else ("WARNING" if warns else "PASS")
        detail = "%d 失败 / %d 警告" % (len(fails), len(warns))
        return [dev.ip, dev.hostname, "汇总", status, detail, ""]

    @staticmethod
    def _check_interfaces(output):
        results = []
        for line in (output or "").split("\n"):
            m = re.findall(r"(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)", line)
            if not m:
                continue
            if_name, admin, oper, speed, desc = m[0]
            if not any(p in if_name for p in _IFACE_TYPES):
                continue
            result = {"name": if_name, "status": "PASS"}
            if admin.upper() == "UP" and oper.upper() == "UP":
                d = desc.strip()
                if not d or d in ("", "NO_USE"):
                    result["status"] = "FAIL"
                    result["message"] = "启用接口未配置有效描述"
                elif not d.startswith(("dT:", "uT:", "pT:")):
                    result["status"] = "FAIL"
                    result["message"] = "接口描述格式不符合规范"
            elif admin.upper() == "DOWN":
                if desc.strip() != "NO_USE":
                    result["status"] = "WARNING"
                    result["message"] = "未启用接口建议配置 NO_USE"
            results.append(result)
        return results

    @staticmethod
    def _check_snmp(output):
        result = {"status": "FAIL", "details": []}
        acl_found = community_found = community_with_acl = False
        for raw in (output or "").split("\n"):
            line = raw.strip()
            if "snmp-server access-list ipv4 2333" in line:
                acl_found = True
                result["details"].append("找到 ACL 2333")
            if "snmp-server community" in line:
                community_found = True
                if "2333" in line:
                    community_with_acl = True
                    result["details"].append("团体字已调用 ACL 2333")
                else:
                    result["details"].append("团体字未调用 ACL 2333")
        if community_found and acl_found and community_with_acl:
            result["status"] = "PASS"
        elif community_found and not community_with_acl:
            result["status"] = "WARNING"
            result["details"].append("团体字未正确调用 ACL 2333")
        else:
            result["details"].append("SNMP 配置不完整")
        return result
