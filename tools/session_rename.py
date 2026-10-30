# -*- coding: utf-8 -*-
"""会话 Hostname 批量修改（收敛 MatchFilesnameChangeIP.py）。

按清单（Excel/CSV，三列：文件名、旧值、新值）批量修改 .ini 会话的 Hostname。
写操作前自动备份到 _backup 目录。
"""
from pathlib import Path

from core.tool_base import BaseTool, ParamSpec
from infra.ini_utils import read_ini, replace_fields, backup_ini


class SessionRenameTool(BaseTool):
    id = "session_rename"
    name = "会话Hostname修改"
    version = "1.0.0"
    category = "会话管理类"
    description = "按清单批量修改 SecureCRT 会话文件的 Hostname（改前自动备份）"

    params = [
        ParamSpec(key="folder", label="会话目录", type="dir", required=True,
                  help="包含 .ini 会话文件的目录"),
        ParamSpec(key="manifest", label="修改清单", type="file", required=True,
                  ext=".xlsx;.csv",
                  help="三列：文件名(不含扩展名)、旧值、新值"),
    ]

    result_columns = ["文件名", "旧值", "新值", "状态", "失败原因"]

    def run(self, params, ctx, emit):
        folder = Path(params["folder"])
        rows = self._load_manifest(params["manifest"], ctx)
        if not rows:
            emit("done", {"summary": {"total": 0}})
            return

        results = []
        for file_name, old_val, new_val in rows:
            ini_path = folder / ("%s.ini" % file_name)
            if not ini_path.exists():
                results.append([file_name, old_val, new_val, "失败", "文件不存在"])
                continue
            content = read_ini(ini_path)
            if content is None:
                results.append([file_name, old_val, new_val, "失败", "无法解码"])
                continue
            # 备份
            backup_ini(ini_path)
            new_content, changed = replace_fields(content, {"hostname": new_val})
            try:
                ini_path.write_text(new_content, encoding="utf-8")
                results.append([file_name, old_val, new_val, "", ""])
            except Exception as e:
                results.append([file_name, old_val, new_val, "失败", str(e)])

        out = ctx.output_dir / "result.csv"
        import csv
        with open(str(out), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(self.result_columns)
            w.writerows(results)

        ok = sum(1 for r in results if r[3] != "失败")
        # 结果表展示真实明细行
        for r in results:
            emit("result", {"row": r, "status": "OK" if r[3] != "失败" else "FAIL_PARSE"})
        ctx.log("修改完成：%d 个会话（%d 成功）-> %s（原文件已备份至 _backup）" % (len(results), ok, out))
        emit("done", {"summary": {"total": len(rows)}})

    @staticmethod
    def _load_manifest(path, ctx):
        rows = []
        try:
            import pandas as pd
            df = pd.read_excel(path) if str(path).lower().endswith((".xlsx", ".xls")) else pd.read_csv(path)
            for _, r in df.iterrows():
                fname = str(r.iloc[0]).strip()
                old = str(r.iloc[1]).strip() if len(r) > 1 else ""
                new = str(r.iloc[2]).strip() if len(r) > 2 else ""
                if fname and fname.lower() != "nan":
                    rows.append((fname, old, new))
        except Exception as e:
            ctx.log("清单读取失败: %s" % e, level="ERROR")
        return rows
