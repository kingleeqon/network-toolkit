# -*- coding: utf-8 -*-
"""CRC 历史趋势库（扫描 output/crc_compare/*/result.xlsx 按天聚合）。

复用现有 output 目录：每次 crc_compare 生成的 result.xlsx 天然就是按天的历史快照。
本模块负责：
- 扫描所有历史 result.xlsx，解析日期（优先用「日期」列，缺失时从目录名反推）
- 聚合为按天的时间序列：每天每接口的 CRC 绝对值 + 差值
- 提供查询：按设备/厂商/topN 筛选、单设备接口排行、接口走势

Python 3.8 兼容。可选依赖 pandas（缺失时降级提示）。
"""
import re
from pathlib import Path

# Excel 列名（与 crc_compare.py 的 _EXCEL_COLUMNS 一致）
_COL_DATE = "日期"
_COL_DEVICE = "设备名称"
_COL_IFACE = "接口名称"
_COL_TODAY = "今日CRC值"
_COL_YESTERDAY = "昨日CRC值"
_COL_DIFF = "CRC差值"
_COL_VENDOR = "厂商"

# 缓存版本：解析逻辑变更时自增，令旧缓存失效
# v2：设备/接口/厂商名统一 strip（兼容采集日志里的尾部空格脏数据）
_CACHE_VERSION = 2


def _guess_date_from_dir(dir_name):
    """从结果目录名（20261028-173522_xxx）反推日期。"""
    m = re.search(r"(20\d{2})(\d{2})(\d{2})-", dir_name)
    if m:
        return "%s-%s-%s" % (m.group(1), m.group(2), m.group(3))
    return None


def _pick_files(base):
    """挑选需要解析的 result.xlsx。

    同一天多次运行时每次都会落一份全量快照，最终按 (日期,设备,接口) 去重
    keep=last，只有最后一次快照会生效——所以**每个日期只需解析最新的一份**，
    避免重复解析几十个 1.6MB 的 xlsx（实测 20 个文件 45s，按日期去重后仅 1 个约 2s）。
    """
    by_date = {}
    others = []
    for f in sorted(base.glob("*/result.xlsx")):
        d = _guess_date_from_dir(f.parent.name)
        if d:
            by_date[d] = f          # 排序靠后的覆盖靠前的 = 取当天最新
        else:
            others.append(f)
    return [by_date[d] for d in sorted(by_date)] + others


def _fingerprint(files):
    """文件指纹：路径+修改时间+大小，用于判断缓存是否仍有效。"""
    out = []
    for f in files:
        try:
            st = f.stat()
            out.append((f.parent.name, int(st.st_mtime), st.st_size))
        except OSError:
            out.append((f.parent.name, 0, 0))
    return tuple(out)


def load_cached(output_base):
    """只读取已有缓存，**不触发解析**（供 UI 渲染下拉选项用，避免阻塞界面）。

    缓存不存在或版本不符时返回 None。
    """
    try:
        import pandas as pd
        from pathlib import Path
        cache_file = Path(output_base) / "output" / "crc_compare" / ".history_cache.pkl"
        if not cache_file.is_file():
            return None
        cached = pd.read_pickle(str(cache_file))
        if (isinstance(cached, dict)
                and cached.get("version") == _CACHE_VERSION):
            return cached.get("df")
    except Exception:
        pass
    return None


def scan_history(output_base, use_cache=True):
    """扫描 output/crc_compare/*/result.xlsx，返回按日期排序的 DataFrame。

    列：日期、设备名称、接口名称、今日CRC值、昨日CRC值、CRC差值、厂商
    返回 None 表示无数据或 pandas 缺失（调用方处理）。

    性能：同一天只解析最新一次的快照；解析结果按文件指纹缓存到
    output/crc_compare/.history_cache.pkl，文件未变化时直接命中（秒开）。
    """
    try:
        import pandas as pd
    except ImportError:
        return None

    base = Path(output_base) / "output" / "crc_compare"
    if not base.is_dir():
        return None

    files = _pick_files(base)
    if not files:
        return None
    fingerprint = _fingerprint(files)
    cache_file = base / ".history_cache.pkl"

    # 1) 命中缓存直接返回
    if use_cache:
        try:
            cached = pd.read_pickle(str(cache_file))
            if (isinstance(cached, dict)
                    and cached.get("version") == _CACHE_VERSION
                    and cached.get("fingerprint") == fingerprint):
                df = cached.get("df")
                if df is not None and not df.empty:
                    return df
        except Exception:
            pass

    # 2) 未命中：解析（每天只读最新一份）
    frames = []
    for result_file in files:
        try:
            df = pd.read_excel(str(result_file))
        except Exception:
            continue
        if df.empty:
            continue
        # 兼容旧格式：无「日期」列时从目录名反推
        if _COL_DATE not in df.columns:
            date_str = _guess_date_from_dir(result_file.parent.name)
            if not date_str:
                continue
            df[_COL_DATE] = date_str
        # 统一列序，丢弃多余列
        keep = [_COL_DATE, _COL_DEVICE, _COL_IFACE, _COL_TODAY, _COL_YESTERDAY, _COL_DIFF, _COL_VENDOR]
        df = df[[c for c in keep if c in df.columns]]
        frames.append(df)

    if not frames:
        return None

    all_df = pd.concat(frames, ignore_index=True)
    all_df[_COL_DATE] = all_df[_COL_DATE].astype(str)
    # 名称列规范化：采集日志里存在 "设备名  "（尾部空格）这类脏数据，
    # 不清理会导致按设备/接口精确查询时匹配不到
    for col in (_COL_DEVICE, _COL_IFACE, _COL_VENDOR):
        if col in all_df.columns:
            all_df[col] = all_df[col].astype(str).str.strip()
    # 数值列规整
    for col in (_COL_TODAY, _COL_YESTERDAY, _COL_DIFF):
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce").fillna(0).astype("int64")
    all_df = all_df.drop_duplicates(subset=[_COL_DATE, _COL_DEVICE, _COL_IFACE], keep="last")
    all_df = all_df.sort_values([_COL_DATE, _COL_DEVICE, _COL_IFACE]).reset_index(drop=True)

    # 3) 写缓存（失败不影响主流程）
    if use_cache:
        try:
            pd.to_pickle({"version": _CACHE_VERSION, "fingerprint": fingerprint, "df": all_df},
                         str(cache_file))
        except Exception:
            pass
    return all_df


def list_dates(df):
    """返回去重排序的日期列表。"""
    return sorted(df[_COL_DATE].unique().tolist()) if df is not None and not df.empty else []


def list_vendors(df):
    return sorted(df[_COL_VENDOR].dropna().unique().tolist()) if df is not None and not df.empty else []


def list_devices(df):
    return sorted(df[_COL_DEVICE].unique().tolist()) if df is not None and not df.empty else []


def filter_data(df, device=None, vendor=None):
    """按设备（精确）与厂商筛选，返回子集 DataFrame。"""
    d = df
    if device:
        d = d[d[_COL_DEVICE] == device]
    if vendor:
        d = d[d[_COL_VENDOR] == vendor]
    return d


def iface_trend(df, device, iface):
    """某设备某接口的按日 CRC 走势，返回 DataFrame（日期、今日CRC值、昨日CRC值、CRC差值）。"""
    d = df[(df[_COL_DEVICE] == device) & (df[_COL_IFACE] == iface)]
    return d[[_COL_DATE, _COL_TODAY, _COL_YESTERDAY, _COL_DIFF]].sort_values(_COL_DATE)


def top_ifaces(df, date=None, top_n=20):
    """全局（跨设备）按 CRC 差值绝对值排行的 top 接口。"""
    d = df if date is None else df[df[_COL_DATE] == date]
    if d.empty:
        return d
    agg = (d.groupby([_COL_DEVICE, _COL_IFACE, _COL_VENDOR], as_index=False)[_COL_DIFF].sum())
    agg["abs"] = agg[_COL_DIFF].abs()
    agg = agg.sort_values("abs", ascending=False).head(top_n)
    return agg.drop(columns=["abs"])


def latest_date(df):
    """返回最新日期字符串；无数据返回 None。"""
    dates = list_dates(df)
    return dates[-1] if dates else None


def resolve_date(df, date=None):
    """把「空/auto」解析为最新日期，并校验其存在性。

    返回 (实际日期, 是否存在)。date 为空时取最新一天。
    """
    dates = list_dates(df)
    if not dates:
        return None, False
    if not date or str(date).strip().lower() in ("", "auto", "latest", "最新"):
        return dates[-1], True
    date = str(date).strip()
    return date, (date in dates)


def daily_rank(df, date=None, top_n=20):
    """**指定单日**的接口 CRC 差值排行（不跨日累加）。

    与 top_ifaces 的区别：top_ifaces 在不传 date 时会把所有日期求和，
    历史累积多天后语义会变成「累计差值」。本函数始终锁定一天，
    保证排行图反映的确实是「这一天」的增量。
    返回列：设备名称、接口名称、CRC差值、厂商
    """
    actual, ok = resolve_date(df, date)
    if not ok:
        return None
    d = df[df[_COL_DATE] == actual]
    if d.empty:
        return None
    agg = (d.groupby([_COL_DEVICE, _COL_IFACE, _COL_VENDOR], as_index=False)[_COL_DIFF].sum())
    agg["_abs"] = agg[_COL_DIFF].abs()
    agg = agg.sort_values("_abs", ascending=False).head(top_n)
    return agg.drop(columns=["_abs"])


def worsening_ifaces(df, min_days=3, top_n=20):
    """找出「最近连续 N 天 CRC 差值都 > 0」的接口（持续恶化）。

    单日增长可能是突发流量，连续多日单调增长才是链路/光模块劣化的早期信号。
    仅统计**从最新一天往前**连续为正的天数（中间断一天即重新计算）。

    返回 DataFrame：设备名称、接口名称、厂商、连续天数、累计增量、最近差值
    按累计增量降序；无满足条件者返回 None。
    """
    try:
        import pandas as pd
    except ImportError:
        return None
    if df is None or df.empty or int(min_days) < 1:
        return None

    cols = [_COL_DATE, _COL_DEVICE, _COL_IFACE, _COL_DIFF, _COL_VENDOR]
    d = df[[c for c in cols if c in df.columns]].sort_values(
        [_COL_DEVICE, _COL_IFACE, _COL_DATE])
    if d.empty:
        return None

    # 反转后逐组求「连续 >0」的长度（cumprod：一旦遇到 <=0 后续全为 0）
    rev = d.iloc[::-1]
    pos = (rev[_COL_DIFF] > 0).astype(int)
    run = pos.groupby([rev[_COL_DEVICE], rev[_COL_IFACE]]).cumprod()
    streak = run.groupby([rev[_COL_DEVICE], rev[_COL_IFACE]]).sum()
    hits = streak[streak >= int(min_days)]
    if hits.empty:
        return None

    groups = {k: v for k, v in d.groupby([_COL_DEVICE, _COL_IFACE])}
    rows = []
    for (dev, iface), days in hits.items():
        g = groups.get((dev, iface))
        if g is None or g.empty:
            continue
        seg = g.tail(int(days))
        rows.append({
            _COL_DEVICE: dev,
            _COL_IFACE: iface,
            _COL_VENDOR: seg[_COL_VENDOR].iloc[-1] if _COL_VENDOR in seg.columns else "",
            "连续天数": int(days),
            "累计增量": int(seg[_COL_DIFF].sum()),
            "最近差值": int(seg[_COL_DIFF].iloc[-1]),
        })
    if not rows:
        return None
    out = pd.DataFrame(rows)
    out = out.sort_values("累计增量", ascending=False).head(int(top_n)).reset_index(drop=True)
    return out
