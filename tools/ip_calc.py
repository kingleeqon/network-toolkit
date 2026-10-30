# -*- coding: utf-8 -*-
"""IP / 掩码 / CIDR 转换计算工具（收敛 IP&Mask_TO_IP&CIDR_v1.12.py）。

纯计算，无 GUI：输入「IP 掩码」或「IP/CIDR」多行文本，
输出 CIDR 格式与网络地址，并支持导出。
"""
from core.tool_base import BaseTool, ParamSpec
from infra.ip_utils import normalize_to_cidr, network_str, detect_ip_version


class IpCalcTool(BaseTool):
    id = "ip_calc"
    name = "IP/CIDR转换计算"
    version = "1.0.0"
    category = "工具类"
    description = "批量把「IP 掩码」或「IP/CIDR」转换为 CIDR 格式并计算网络地址"

    params = [
        ParamSpec(key="input_file", label="输入文件", type="file", required=True,
                  ext=".txt", help="每行一个：'IP 掩码' 或 'IP/CIDR'（空格/制表符分隔）"),
    ]

    result_columns = ["行号", "原始输入", "IP版本", "CIDR格式", "网络地址", "状态", "失败原因"]

    def run(self, params, ctx, emit):
        path = params["input_file"]
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                lines = [l.strip() for l in f if l.strip()]
        except UnicodeDecodeError:
            with open(path, "r", encoding="gbk") as f:
                lines = [l.strip() for l in f if l.strip()]

        rows = []
        for i, line in enumerate(lines, 1):
            try:
                cidr = self._to_cidr(line)
                net = network_str(cidr)
                rows.append([i, line, detect_ip_version(line.split("/")[0].split()[0]),
                             cidr, net, "", ""])
            except Exception as e:
                rows.append([i, line, "", "", "", "失败", str(e)])

        out = ctx.output_dir / "result.txt"
        with open(str(out), "w", encoding="utf-8") as f:
            for r in rows:
                f.write("\t".join(str(c) for c in r[:5]) + "\n")

        ok = sum(1 for r in rows if r[5] != "失败")
        # 结果表展示真实明细行
        for r in rows:
            emit("result", {"row": r, "status": "OK" if r[5] != "失败" else "FAIL_PARSE"})
        ctx.log("转换完成：%d 行（%d 成功）-> %s" % (len(rows), ok, out))
        emit("done", {"summary": {"total": len(lines)}})

    @staticmethod
    def _to_cidr(line):
        """把一行输入归一化为 'ip/prefix'。"""
        if "/" in line:
            return line  # 已是 CIDR
        parts = line.split()
        if len(parts) < 2:
            raise ValueError("缺少掩码/前缀")
        return normalize_to_cidr(parts[0], parts[1])
