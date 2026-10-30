# -*- coding: utf-8 -*-
"""会话配置项同步（收敛 AutoSessionPassword.py 与 ChangeINFO.py 两个重复脚本）。

从基准 .ini 复制指定配置项（密码、登录脚本、仿真等）到一批 .ini 会话文件。
写操作前自动备份。
"""
from pathlib import Path

from core.tool_base import BaseTool, ParamSpec
from infra.ini_utils import read_ini, replace_fields, backup_ini, iter_ini_files

# 需要同步的配置项（合并原两个脚本的 target_configs）
_SYNC_FIELDS = ["password", "login_script", "session_password_saved",
                "pre_connect", "emulation", "initial_cr"]


class SessionSyncTool(BaseTool):
    id = "session_sync"
    name = "会话配置同步"
    version = "1.0.0"
    category = "会话管理类"
    description = "从基准 .ini 复制密码/登录脚本等配置项到批量会话文件（改前自动备份）"

    params = [
        ParamSpec(key="base_file", label="基准配置文件", type="file", required=True,
                  ext=".ini", help="包含正确配置的基准 .ini"),
        ParamSpec(key="folder", label="目标会话目录", type="dir", required=True,
                  help="需要修改的会话 .ini 所在目录"),
    ]

    result_columns = ["文件名", "修改项数", "状态", "失败原因"]

    def run(self, params, ctx, emit):
        base = Path(params["base_file"])
        folder = params["folder"]

        base_content = read_ini(base)
        if base_content is None:
            ctx.log("基准文件无法读取", level="ERROR")
            emit("done", {"summary": {"total": 0}})
            return

        # 从基准文件提取各配置项的值
        from infra.ini_utils import extract_field
        base_values = {}
        for field in _SYNC_FIELDS:
            val = extract_field(base_content, field)
            if val is not None:
                base_values[field] = val

        if not base_values:
            ctx.log("基准文件中未找到任何可同步的配置项", level="ERROR")
            emit("done", {"summary": {"total": 0}})
            return

        files = [p for p in iter_ini_files(folder, recursive=True)]
        if not files:
            ctx.log("目标目录下未找到 .ini 文件", level="WARN")
            emit("done", {"summary": {"total": 0}})
            return

        ctx.log("将同步 %d 个配置项到 %d 个会话文件" % (len(base_values), len(files)))

        results = []
        for path in files:
            content = read_ini(path)
            if content is None:
                results.append([path.name, 0, "失败", "无法解码"])
                continue
            backup_ini(path)
            new_content, changed = replace_fields(content, base_values)
            try:
                path.write_text(new_content, encoding="utf-8")
                results.append([path.name, len(changed), "", ""])
            except Exception as e:
                results.append([path.name, 0, "失败", str(e)])

        import csv
        out = ctx.output_dir / "result.csv"
        with open(str(out), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(self.result_columns)
            w.writerows(results)

        ok = sum(1 for r in results if r[2] != "失败")
        # 结果表展示真实明细行
        for r in results:
            emit("result", {"row": r, "status": "OK" if r[2] != "失败" else "FAIL_PARSE"})
        ctx.log("同步完成：%d 个文件（%d 成功）-> %s（原文件已备份至 _backup）" % (len(files), ok, out))
        emit("done", {"summary": {"total": len(files)}})
