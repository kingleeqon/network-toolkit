# -*- coding: utf-8 -*-
"""每日流水线：接口采集 + 配置备份 + CRC 对比（无界面，供 Windows 计划任务调用）。

一条命令跑完两件事：
    1) 按设备清单（Excel/CSV/TXT）登录设备采集接口回显，
       归档成 <archive_root>/<YYYY-MM-DD>/<设备名>.log
       顺带在**同一次登录里**把配置也取走 → <backup_root>/<日期>/<设备名>.cfg
    2) 取 archive_root 下最新的两个日期目录，跑「接口CRC对比分析」，
       得出当天与前一天的 CRC 差值 → output/crc_compare/<时间戳>/result.xlsx

接口数据和配置是**两个文件**（.log / .cfg），但只登录一次设备。
备份失败不阻断对比（对比只依赖 .log），但会在退出码里体现（6）。

用法：
    # 先看会做什么，不连设备
    python scripts/daily_crc.py --config scripts/daily_config.json --dry-run

    # 真跑
    python scripts/daily_crc.py --config scripts/daily_config.json

    # 只跑对比（今天已经采集过）
    python scripts/daily_crc.py --config scripts/daily_config.json --skip-collect

    # 注册成每天 08:30 自动跑
    scripts\\install_task.bat scripts\\daily_config.json 08:30

退出码：0 全成功 / 2 日期目录不足 / 3 采集失败 / 4 对比失败 / 5 配置有误 / 6 配置备份失败
（常量定义见 core/exit_codes.py 的 PIPE_*）
"""
import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.exit_codes import (PIPE_BACKUP_FAIL, PIPE_BAD_CONFIG,  # noqa: E402
                             PIPE_COLLECT_FAIL, PIPE_COMPARE_FAIL,
                             PIPE_DATE_DIR_LACK, PIPE_OK, PIPE_TASK_FAIL)

_DATE_RE = re.compile(r"(20\d{2})[-_]?(\d{1,2})[-_]?(\d{1,2})")
_MARKER = BASE_DIR / "output" / "crc_compare" / ".last_auto_run.json"
DEFAULT_CONFIG = Path(__file__).resolve().parent / "daily_config.json"


# ---------- 日志 ----------
def setup_logger():
    (BASE_DIR / "logs").mkdir(exist_ok=True)
    name = "daily_crc_%s.log" % datetime.now().strftime("%Y%m%d")
    logger = logging.getLogger("daily_crc")
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(str(BASE_DIR / "logs" / name), encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


# ---------- 配置 ----------
def load_config(path):
    p = Path(path)
    if not p.is_file():
        raise IOError("配置文件不存在：%s" % p)
    with open(str(p), "r", encoding="utf-8") as f:
        raw = json.load(f)
    cfg = {k: v for k, v in raw.items() if not k.startswith("_")}
    cfg.setdefault("devices", "")
    cfg.setdefault("archive_root", "")
    cfg.setdefault("concurrency", 5)
    cfg.setdefault("timeout", 40)
    cfg.setdefault("skip_collect", False)
    cfg.setdefault("backup_enabled", True)
    cfg.setdefault("backup_root", "")
    cfg.setdefault("daily_time", "08:30")
    return cfg


def save_config(cfg, path=None):
    """写配置（界面「保存」也走这里，保证键名和注释模板一致）。"""
    p = Path(path or DEFAULT_CONFIG)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_说明": "每日流水线配置：采集接口数据 + 备份设备配置 + CRC 对比。界面右上角「定时任务」可直接改。",
        "devices": cfg.get("devices", ""),
        "archive_root": cfg.get("archive_root", ""),
        "concurrency": int(cfg.get("concurrency", 5) or 5),
        "timeout": int(cfg.get("timeout", 40) or 40),
        "skip_collect": bool(cfg.get("skip_collect", False)),
        "backup_enabled": bool(cfg.get("backup_enabled", True)),
        "backup_root": cfg.get("backup_root", ""),
        "daily_time": cfg.get("daily_time", "08:30"),
    }
    with open(str(p), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return p


# ---------- 计划任务 ----------
TASK_NAME = "NetToolkit-DailyCRC"
_HERE = Path(__file__).resolve().parent
WRAPPER = _HERE / "_run_daily.bat"


def _run(cmd):
    import subprocess
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           encoding="mbcs", errors="replace")
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as exc:
        return False, str(exc)


def ensure_wrapper(config_path, python_exe=None):
    """生成计划任务要调用的 bat（避免 schtasks 里的嵌套引号问题）。"""
    py = python_exe or sys.executable
    lines = [
        "@echo off",
        'cd /d "%s"' % BASE_DIR,
        '"%s" "%s" --config "%s"' % (py, Path(__file__).resolve(), config_path),
    ]
    # bat 里可能有中文路径，用 ANSI(mbcs) 写，cmd 才能正确解析
    try:
        WRAPPER.write_text("\r\n".join(lines) + "\r\n", encoding="mbcs")
    except Exception:
        WRAPPER.write_text("\r\n".join(lines) + "\r\n", encoding="gbk", errors="replace")
    return WRAPPER


def normalize_time(text):
    """把 "8:5" / "08:05" / "8：05" 规整成 "HH:MM"，非法返回 None。"""
    s = str(text or "").strip().replace("：", ":")
    m = re.match(r"^(\d{1,2}):(\d{1,2})$", s)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return "%02d:%02d" % (hh, mm)


def install_task(config_path, at="08:30", python_exe=None):
    """注册每天定时任务，返回 (ok, message)。"""
    at = normalize_time(at)
    if at is None:
        return False, "时间格式不对，应为 HH:MM（00:00 ~ 23:59）"
    wrapper = ensure_wrapper(config_path, python_exe)
    ok, out = _run(["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", at,
                    "/TN", TASK_NAME, "/TR", str(wrapper)])
    if ok:
        return True, "已注册：每天 %s 执行\n%s" % (at, out)
    return False, "注册失败：\n%s" % out


def remove_task():
    ok, out = _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
    if ok:
        return True, "已取消定时任务"
    return False, "取消失败（可能本来就没注册）：\n%s" % out


def query_task():
    """返回 (是否存在, 描述文本)。"""
    ok, out = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    if not ok:
        return False, ""
    info = {}
    for line in out.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            info[k.strip()] = v.strip()
    nxt = info.get("下次运行时间") or info.get("Next Run Time") or ""
    st = info.get("状态") or info.get("Status") or ""
    return True, "下次运行：%s    状态：%s" % (nxt or "-", st or "-")


# ---------- 日期目录 ----------
def parse_dir_date(name):
    m = _DATE_RE.search(name or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    except ValueError:
        return None


def scan_dated_dirs(root):
    """返回 [(date, path)]，按日期从旧到新；只收含 .log 的目录。"""
    p = Path(root)
    if not p.is_dir():
        return []
    by_date = {}
    for d in sorted(p.iterdir()):
        if not d.is_dir():
            continue
        day = parse_dir_date(d.name)
        if day is None or not any(d.glob("*.log")):
            continue
        by_date[day] = d
    return sorted(by_date.items())


# ---------- 引擎调用 ----------
def run_tool(tool_id, params, logger):
    """跑一个工具，返回 (ok, stats, output_dir, logs)。"""
    from core.registry import Registry
    from core.task_engine import TaskEngine

    reg = Registry(BASE_DIR / "tools")
    reg.discover()
    tool = reg.get(tool_id)
    if tool is None:
        logger.error("未找到工具：%s" % tool_id)
        return False, {}, None, []

    engine = TaskEngine(output_base=str(BASE_DIR / "output"),
                        app_version="daily", operator="scheduler")
    engine.start(tool, params)
    captured = []

    def on_msg(msg):
        type_ = msg.get("type")
        value = getattr(type_, "value", type_)
        payload = msg.get("payload", {})
        if value == "log":
            level = str(payload.get("level", "INFO")).upper()
            text = payload.get("msg")
            captured.append((level, text))
            if level == "ERROR":
                logger.error(text)
            elif level == "WARN":
                logger.warning(text)
            else:
                logger.info(text)
        elif value == "error":
            logger.error(payload.get("msg") or payload)

    deadline = time.time() + 6 * 3600
    while engine.state in ("RUNNING", "CANCELLING") and time.time() < deadline:
        engine.poll(on_msg)
        time.sleep(0.05)
    engine.poll(on_msg)
    ok = engine.state == "SUCCEEDED"
    logger.info("[%s] 状态=%s 统计=%s", tool_id, engine.state, engine.stats)
    return ok, engine.stats or {}, engine.output_dir, captured


# ---------- 防重复 ----------
def last_run_date():
    try:
        with open(str(_MARKER), "r", encoding="utf-8") as f:
            return json.load(f).get("date")
    except Exception:
        return None


def save_marker(date_str, out_dir):
    try:
        _MARKER.parent.mkdir(parents=True, exist_ok=True)
        with open(str(_MARKER), "w", encoding="utf-8") as f:
            json.dump({"date": date_str,
                       "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "output": str(out_dir or "")}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser(description="每日：采集接口数据 → CRC 对比")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径")
    ap.add_argument("--devices", help="覆盖配置里的设备清单路径")
    ap.add_argument("--archive-root", help="覆盖配置里的日志归档根目录")
    ap.add_argument("--skip-collect", action="store_true", help="跳过采集，只做对比")
    ap.add_argument("--skip-backup", action="store_true", help="跳过配置备份")
    ap.add_argument("--backup-root", help="覆盖配置里的配置备份根目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不连设备")
    ap.add_argument("--force", action="store_true", help="当天已跑过也再跑")
    ap.add_argument("--install-task", nargs="?", const="08:30", metavar="HH:MM",
                    help="注册每天定时任务（默认 08:30）后退出")
    ap.add_argument("--remove-task", action="store_true", help="取消定时任务后退出")
    ap.add_argument("--status", action="store_true", help="查看定时任务状态后退出")
    args = ap.parse_args()

    logger = setup_logger()

    # ---- 计划任务管理（不跑流水线）----
    if args.status:
        exists, text = query_task()
        print(("定时任务：%s" % text) if exists else "尚未注册定时任务")
        return PIPE_OK
    if args.remove_task:
        ok, msg = remove_task()
        print(msg)
        return PIPE_OK if ok else PIPE_TASK_FAIL
    if args.install_task:
        ok, msg = install_task(args.config, args.install_task)
        print(msg)
        return PIPE_OK if ok else PIPE_TASK_FAIL

    logger.info("=" * 64)
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        logger.error("配置读取失败：%s", exc)
        return PIPE_BAD_CONFIG
    if args.devices:
        cfg["devices"] = args.devices
    if args.archive_root:
        cfg["archive_root"] = args.archive_root
    if args.skip_collect:
        cfg["skip_collect"] = True
    if args.skip_backup:
        cfg["backup_enabled"] = False
    if args.backup_root:
        cfg["backup_root"] = args.backup_root

    devices = (cfg.get("devices") or "").strip()
    archive_root = (cfg.get("archive_root") or "").strip()
    backup_on = bool(cfg.get("backup_enabled", True))
    backup_root = (cfg.get("backup_root") or "").strip()
    if not archive_root:
        logger.error("配置里缺少 archive_root（日志归档根目录）")
        return PIPE_BAD_CONFIG
    if backup_on and not backup_root:
        logger.error("已开启配置备份，但配置里缺少 backup_root（配置备份根目录）")
        return PIPE_BAD_CONFIG
    if not cfg.get("skip_collect"):
        if not devices:
            logger.error("配置里缺少 devices（设备清单 Excel 路径）")
            return PIPE_BAD_CONFIG
        if not Path(devices).is_file():
            logger.error("设备清单不存在：%s", devices)
            return PIPE_BAD_CONFIG

    logger.info("配置：devices=%s", devices or "(跳过采集)")
    logger.info("配置：archive_root=%s，并发=%s，超时=%ss",
                archive_root, cfg.get("concurrency"), cfg.get("timeout"))
    logger.info("配置：配置备份=%s%s", "开启" if backup_on else "关闭",
                ("  → " + backup_root) if (backup_on and backup_root) else "")

    # ---- 1) 采集 ----
    if cfg.get("skip_collect"):
        logger.info("--skip-collect：跳过采集，直接对比已有日志")
    else:
        if args.dry_run:
            logger.info("[dry-run] 将执行 iface_collect：%s → %s（配置备份 %s）",
                        devices, archive_root,
                        ("同时存到 " + backup_root) if backup_on else "关闭")
        else:
            ok, stats, out, _ = run_tool("iface_collect", {
                "devices": devices,
                "concurrency": int(cfg.get("concurrency", 5)),
                "timeout": int(cfg.get("timeout", 40)),
                "archive_dir": archive_root,
                # 同一个会话里顺带把配置也取走：一次登录拿两份，不用登两遍
                "backup_config": backup_on,
                "backup_dir": backup_root,
            }, logger)
            success = int((stats or {}).get("success", 0) or 0)
            if not ok or success <= 0:
                logger.error("采集未成功（成功 %d 台），跳过对比", success)
                return PIPE_COLLECT_FAIL
            logger.info("采集完成：成功 %d 台 → %s", success, out)

    # ---- 2) 校验配置备份有没有落盘（采集时顺带做的）----
    backup_failed = False
    if backup_on:
        today = datetime.now().strftime("%Y-%m-%d")
        cfg_dir = Path(backup_root) / today
        n_cfg = len(list(cfg_dir.glob("*.cfg"))) if cfg_dir.is_dir() else 0
        if n_cfg > 0:
            logger.info("配置备份：%d 个文件 → %s", n_cfg, cfg_dir)
        else:
            backup_failed = True
            logger.error("配置备份没有产出文件（%s），继续做 CRC 对比", cfg_dir)
    else:
        logger.info("已关闭配置备份，跳过")

    # ---- 3) 选日期目录 ----
    pairs = scan_dated_dirs(archive_root)
    if len(pairs) < 2:
        logger.warning("归档目录里可用日期不足 2 个（找到 %d 个），无法对比", len(pairs))
        for d, p in pairs:
            logger.warning("   %s -> %s", d, p)
        return PIPE_DATE_DIR_LACK
    (prev_date, prev_dir), (cur_date, cur_dir) = pairs[-2], pairs[-1]
    logger.info("昨日：%s -> %s", prev_date, prev_dir)
    logger.info("今日：%s -> %s", cur_date, cur_dir)

    if args.dry_run:
        logger.info("[dry-run] 将执行 crc_compare：%s vs %s", prev_dir, cur_dir)
        logger.info("--dry-run 结束，未执行任何任务")
        return PIPE_OK
    if not args.force and last_run_date() == cur_date.isoformat():
        logger.info("数据日期 %s 上次已成功对比过，跳过；需要重跑加 --force", cur_date)
        return PIPE_OK

    # ---- 4) 对比 ----
    ok, stats, out, _ = run_tool("crc_compare", {
        "yesterday_dir": str(prev_dir),
        "today_dir": str(cur_dir),
    }, logger)
    if not ok:
        logger.error("CRC 对比未成功")
        return PIPE_COMPARE_FAIL
    save_marker(cur_date.isoformat(), out)
    logger.info("完成：%s vs %s，结果 %s", cur_date, prev_date, out)
    logger.info("提示：「CRC趋势分析」会自动读取这些历史，攒够 2 天即可看走势")
    if backup_failed:
        logger.warning("注意：本次配置备份有失败，详见上面的日志")
        return PIPE_BACKUP_FAIL
    return PIPE_OK


if __name__ == "__main__":
    sys.exit(main())
