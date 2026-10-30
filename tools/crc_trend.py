# -*- coding: utf-8 -*-
"""CRC 趋势分析（基于历史 result.xlsx 按天累积，一次生成多张图）。

复用 output/crc_compare/*/result.xlsx 作为历史库，一次运行可按需同时产出：
- 全局接口 CRC 差值排行 TopN（**锁定指定日期**，不再跨日累加）
- 按厂商分别排行（每个厂商一张）
- 按设备排行（指定设备或差值最大的若干台）
- 接口走势图（单接口双轴明细；多接口额外出一张同轴对比图）
- 持续恶化接口排行（最近连续 N 天 CRC 都在增长的接口，链路劣化早期信号）

口径说明：所有排行图都基于「单日快照」（默认最新一天），
避免历史累积多天后把多天差值相加导致数值失真。走势图与恶化识别才使用全量历史。

图表以 matplotlib Figure 对象通过 CHART 消息投递到主线程，
由 ui/chart_view.py 内嵌渲染（网格卡片 + 放大 + 悬浮取值）。
结果表不承载图表内容，保持为空。
"""
import math
from pathlib import Path

from core.tool_base import BaseTool, ParamSpec
from infra import crc_history as ch

# matplotlib 中文字体（按可用性降级）
_CN_FONTS = ["Microsoft YaHei", "SimHei", "SimSun", "KaiTi", "DengXian"]

# 每张排行图最多展示的接口数（卡片高约 410px，再多标签就会互相压）
_PER_CHART_MAX = 22
# 走势图接口名留空时，自动取该设备差值最大的前 N 个接口
_AUTO_TREND_MAX = 5


def _trim_num(s):
    """去掉 "1.0" 这种没有意义的尾零（只在小数点存在时才动）。"""
    return s[:-2] if s.endswith(".0") else s


def _cn_num(v, _pos=None):
    """坐标轴数字简写：亿/万，避免千分位长数字在窄图里挤成一团。

    不能一律取整：走势图左轴刻度 1800、2400 都会变成 "2千"，
    同一根轴上出现两个一模一样的标签，看图的根本分不清谁是谁。
    所以万位以下不缩写（"2,400" 只 5 个字符，塞得下），
    万位以上保留 1 位小数（1.2万 / 1.5万），到千万级再取整。
    """
    try:
        a = abs(v)
    except TypeError:
        return str(v)
    if a >= 100000000:
        return _trim_num("%.1f" % (v / 100000000.0)) + "亿"
    if a >= 1000000:                       # ≥100 万：整数万就够区分了（658万）
        return "%.0f万" % (v / 10000.0)
    if a >= 10000:
        return _trim_num("%.1f" % (v / 10000.0)) + "万"
    # 小数刻度（坐标范围退化时会出现 ±0.5 之类）：保留有效小数位
    if 0 < a < 1:
        return ("%.2f" % v).rstrip("0").rstrip(".")
    return format(int(round(v)), ",")


# 接口类型缩写：长前缀在 267px 宽的卡片里占掉半个标签区，
# 且截断后 "Ten-GigabitEthernet1/0/8" 和 "Ethernet1/0/8" 都会变成
# "…Ethernet1/0/8" —— 两种完全不同的口显示成一个名字。
# 注意顺序：必须先匹配长的（Ten-GigabitEthernet 含 GigabitEthernet 子串）。
_IFACE_ABBR = (
    ("Ten-GigabitEthernet", "XGE"),
    ("TenGigabitEthernet", "XGE"),
    ("TwentyFiveGigE", "25GE"),
    ("HundredGigE", "HGE"),
    ("FortyGigE", "FoGE"),
    ("M-GigabitEthernet", "MGE"),
    # 华为/中兴的万兆口常写成 XGigabitEthernet，不加这条会被截成 "…Ethernet1/0/1"，
    # 和真·千兆口的名字撞车
    ("XGigabitEthernet", "XGE"),
    ("GigabitEthernet", "GE"),
    ("FastEthernet", "FE"),
    ("Ethernet", "Eth"),
)


def _abbr_iface(s):
    """把常见接口长前缀换成业界通用缩写（XGE/GE/Eth…）。"""
    for full, short in _IFACE_ABBR:
        if s.startswith(full):
            return short + s[len(full):]
    return s


def _short(name, n=14):
    """标签压缩：先换缩写，仍超长才保留尾部截断。

    接口名区分度在末尾（...1/0/1 与 ...1/0/2），从右侧截断会让整列标签
    长得一模一样；而长前缀（Ten-GigabitEthernet）又占掉半个标签区。
    两步走：先缩写掉前缀，剩下的通常 14 字符内够放，不必截断。
    """
    s = _abbr_iface(str(name or ""))
    if len(s) <= n:
        return s
    return "…" + s[-(n - 1):]


def _ensure_span(ax, which="y"):
    """坐标范围退化时撑开跨度。

    数据全等或只有一个点时 autoscale 会给出 lo == hi，
    MaxNLocator 于是吐出 5 个一模一样的刻度（图上就是 0 0 0 0 0）。
    """
    try:
        lo, hi = (ax.get_ylim() if which == "y" else ax.get_xlim())
        if hi - lo > 1e-12:
            return
        pad = max(abs(hi), 1.0)
        if which == "y":
            ax.set_ylim(lo - pad, hi + pad)
        else:
            ax.set_xlim(lo - pad, hi + pad)
    except Exception:
        pass


def _tick_positions(n, want=3):
    """等距挑 want 个刻度位置（含首尾），日期标签才不会叠在一起。

    266px 宽的卡片里一个 "10-25" 就要 40px，放 5 个必然首尾相撞。
    """
    if n <= want:
        return list(range(n))
    if want <= 1:
        return [n - 1]
    idx = [int(round(i * (n - 1) / float(want - 1))) for i in range(want)]
    return sorted(set(idx))


def _insufficient_figure(Figure, days):
    """历史不足时给一张最小的提示卡，而不是画一张空图。

    要点：
    1. **不建坐标轴**——单日数据下 autoscale 会退化（±0.05 的刻度配空白坐标区），
       把提示塞进坐标区还会被 `_scale_figure` 放大到撑破卡片；
    2. **每行不超过 8 个字**——卡片只有 266px 宽，9pt 的中文一行放得下 10 个字左右，
       写成整句（"当前仅 1 天历史（2026-10-28）"）会左右两边同时被裁掉。
    """
    fig = Figure(figsize=(2.4, 2.2), layout="constrained", facecolor="white")
    ax = fig.add_subplot(111)
    ax.axis("off")
    if days < 2:
        head = "数据不足"
        body = "仅 %d 天历史\n需要至少 2 天\n明天再采集一次" % days
    else:
        head = "暂无数据"
        body = "可用历史 %d 天\n未找到该接口\n的采集记录" % days
    ax.text(0.5, 0.62, head, ha="center", va="center", transform=ax.transAxes,
            fontsize=12, color="#b9770e", weight="bold")
    ax.text(0.5, 0.32, body, ha="center", va="center", transform=ax.transAxes,
            fontsize=9, color="#7f8c8d", linespacing=1.9)
    return fig


def _setup_matplotlib():
    """配置 matplotlib（中文字体），返回 Figure 类；失败返回 None。"""
    try:
        import matplotlib
        from matplotlib.figure import Figure
        from matplotlib import font_manager, rcParams
        available = set(f.name for f in font_manager.fontManager.ttflist)
        for name in _CN_FONTS:
            if name in available:
                rcParams["font.sans-serif"] = [name]
                break
        rcParams["axes.unicode_minus"] = False
        return Figure
    except Exception:
        return None


# —— 表单动态选项（只读缓存，绝不阻塞 UI）——
def _project_root_static():
    return Path(__file__).resolve().parent.parent


# 记忆化：仅在缓存文件变化时重新读取（避免每次渲染表单都反序列化 10MB）
_OPT_MEMO = {"key": None, "df": None}


def _cached_df():
    root = _project_root_static()
    cache_file = root / "output" / "crc_compare" / ".history_cache.pkl"
    try:
        key = (str(cache_file), cache_file.stat().st_mtime)
    except OSError:
        return None
    if _OPT_MEMO["key"] == key:
        return _OPT_MEMO["df"]
    df = ch.load_cached(str(root))
    _OPT_MEMO["key"] = key
    _OPT_MEMO["df"] = df
    return df


def _date_options():
    """分析日期下拉：最新（自动）+ 历史各天（新→旧）。"""
    df = _cached_df()
    dates = ch.list_dates(df) if df is not None else []
    opts = [{"label": "最新（自动）", "value": ""}]
    for d in reversed(dates):
        opts.append({"label": d, "value": d})
    return opts


def _device_options():
    """设备下拉：最新一天差值最大的前 50 台（便于直接挑问题设备）。"""
    df = _cached_df()
    if df is None or df.empty:
        return [{"label": "（留空：自动取差值最大的前N台）", "value": ""}]
    actual, ok = ch.resolve_date(df)
    if not ok:
        return [{"label": "（留空：自动取差值最大的前N台）", "value": ""}]
    d = df[df[ch._COL_DATE] == actual]
    if d.empty:
        return [{"label": "（留空：自动取差值最大的前N台）", "value": ""}]
    agg = (d.assign(_a=d[ch._COL_DIFF].abs())
             .groupby(ch._COL_DEVICE)["_a"].sum()
             .sort_values(ascending=False).head(50))
    opts = [{"label": "（留空：自动取差值最大的前N台）", "value": ""}]
    for dev, val in agg.items():
        opts.append({"label": "%s  (差值 %d)" % (dev, int(val)), "value": dev})
    return opts


class CrcTrendTool(BaseTool):
    id = "crc_trend"
    name = "CRC趋势分析"
    version = "1.4.1"
    category = "采集分析类"
    description = ("基于历史 CRC 对比结果，生成单日差值排行、厂商/设备排行、"
                   "多接口走势与持续恶化识别图（界面内实时显示）")

    params = [
        ParamSpec(key="date", label="分析日期", type="choice", default="",
                  options_provider=_date_options,
                  help="排行图统计的是「这一天」的 CRC 增量；默认取最新一天"),
        ParamSpec(key="show_global", label="全局 TopN 排行图", type="bool", default=True,
                  help="生成一张跨设备的接口 CRC 差值排行条形图"),
        ParamSpec(key="show_worsening", label="持续恶化接口图", type="bool", default=True,
                  help="列出最近连续 N 天 CRC 都在增长的接口（链路劣化早期信号）"),
        ParamSpec(key="worsen_days", label="连续增长天数 N", type="int", default=3,
                  min=2, max=30,
                  help="连续多少天差值都 >0 才判定为持续恶化；需历史天数≥N"),
        ParamSpec(key="show_vendor", label="按厂商排行图", type="bool", default=False,
                  help="每个厂商各生成一张接口 CRC 差值排行条形图"),
        ParamSpec(key="show_device", label="按设备排行图", type="bool", default=False,
                  help="对指定设备（或差值最大的若干设备）各生成一张排行条形图"),
        ParamSpec(key="device", label="设备（按设备图/走势图）", type="choice", default="",
                  options_provider=_device_options,
                  help="留空则自动取差值最大的前 N 台设备出图；走势图需指定设备"),
        ParamSpec(key="max_devices", label="按设备图最大台数", type="int", default=5,
                  min=1, max=30,
                  help="未指定设备时，自动选取差值最大的前 N 台设备各出一张图"),
        ParamSpec(key="show_trend", label="走势图", type="bool", default=False,
                  help="对指定接口生成每日走势（差值柱+累计线）；填多个接口时"
                       "会先出一张多接口同轴对比图，再逐个出明细"),
        ParamSpec(key="iface", label="接口名称（可逗号分隔多个）", type="text", required=False,
                  help="如 cgei-0/1/1/49 或 GE1,GE2,GE3；留空则自动取该设备差值最大的前 %d 个接口"
                       % _AUTO_TREND_MAX),
        ParamSpec(key="top_n", label="TopN 数量", type="int", default=20, min=1, max=100,
                  help="每张排行图展示的接口数量（单图最多 %d 条）" % _PER_CHART_MAX),
        ParamSpec(key="log_axis", label="对数坐标(X轴)", type="bool", default=True,
                  help="CRC 差值常跨 3~4 个数量级（实测第1名 657万、第20名 3409），"
                       "线性坐标下除第一名外全被压成看不见的一条；对数坐标能同时"
                       "看清大值和小值。出现 0 或负值时会自动退回对称对数，不会报错"),
    ]

    # 结果表不承载图表内容，保持为空列（仅占位，不 emit 数据行）
    result_columns = ["说明", "状态", "失败原因"]

    # 本工具只出图，无需结果表标签页
    output_tabs = ["chart", "log", "summary"]

    def run(self, params, ctx, emit):
        Figure = _setup_matplotlib()
        if Figure is None:
            ctx.log("未安装 matplotlib，无法生成图表；请 pip install matplotlib", level="ERROR")
            emit("done", {"summary": {"total": 0}})
            return

        df = ch.scan_history(self._project_root(ctx))
        if df is None or df.empty:
            ctx.log("未找到历史 CRC 对比结果，请先运行「接口CRC对比分析」", level="WARN")
            emit("done", {"summary": {"total": 0}})
            return

        all_dates = ch.list_dates(df)
        ctx.log("已加载历史 CRC 数据：%d 行，%d 天（%s ~ %s）"
                % (len(df), len(all_dates), all_dates[0], all_dates[-1]))

        # 锁定分析日期：排行图只统计这一天
        date, ok = ch.resolve_date(df, params.get("date"))
        if not ok:
            ctx.log("分析日期「%s」无数据，可选：%s"
                    % (params.get("date"), "、".join(all_dates)), level="ERROR")
            emit("done", {"summary": {"total": 0}})
            return
        day = df[df[ch._COL_DATE] == date]
        if day.empty:
            ctx.log("日期 %s 无数据" % date, level="WARN")
            emit("done", {"summary": {"total": 0}})
            return

        top_n = int(params.get("top_n", 20))
        limit = min(top_n, _PER_CHART_MAX)
        log_axis = bool(params.get("log_axis", True))
        device = (params.get("device") or "").strip()
        iface_raw = (params.get("iface") or "").strip()
        emitted = 0

        ctx.log("排行统计口径：%s 单日增量（%d 条记录）" % (date, len(day)))
        if top_n > _PER_CHART_MAX:
            ctx.log("TopN=%d 超过单图上限，实际展示前 %d 条" % (top_n, _PER_CHART_MAX),
                    level="WARN")

        # 1) 全局 TopN 排行
        if params.get("show_global", True):
            rank = ch.daily_rank(day, date=date, top_n=limit)
            if rank is not None and not rank.empty:
                title = "%s 全局接口 CRC 差值排行 Top%d" % (date, len(rank))
                fig = self._bar_figure(Figure, rank, top_n=limit, log_axis=log_axis)
                if fig is not None:
                    emit("chart", {"figure": fig, "title": title})
                    emitted += 1
            else:
                ctx.log("当日无接口差值数据，跳过全局排行", level="WARN")

        # 2) 持续恶化接口
        if params.get("show_worsening", True):
            need = int(params.get("worsen_days", 3))
            if len(all_dates) < need:
                ctx.log("历史仅 %d 天，不足 %d 天，无法判断持续恶化（需先累积数据）"
                        % (len(all_dates), need), level="WARN")
            else:
                worse = ch.worsening_ifaces(df, min_days=need, top_n=limit)
                if worse is None or worse.empty:
                    ctx.log("未发现连续 %d 天增长的接口" % need, level="WARN")
                else:
                    title = "持续恶化接口（连续≥%d天增长）Top%d" % (need, len(worse))
                    fig = self._worsen_figure(Figure, worse, log_axis=log_axis)
                    if fig is not None:
                        emit("chart", {"figure": fig, "title": title})
                        emitted += 1

        # 3) 按厂商排行（单日）
        if params.get("show_vendor", False):
            for vendor in ch.list_vendors(day):
                sub = day[day[ch._COL_VENDOR] == vendor]
                rank = ch.daily_rank(sub, date=date, top_n=limit)
                if rank is None or rank.empty:
                    continue
                title = "%s 厂商 %s 接口 CRC 差值排行 Top%d" % (date, vendor, len(rank))
                fig = self._bar_figure(Figure, rank, top_n=limit, log_axis=log_axis)
                if fig is not None:
                    emit("chart", {"figure": fig, "title": title})
                    emitted += 1

        # 4) 按设备排行（单日）
        if params.get("show_device", False):
            devices = self._pick_devices(day, device, int(params.get("max_devices", 5)))
            for dev in devices:
                sub = day[day[ch._COL_DEVICE] == dev]
                rank = ch.daily_rank(sub, date=date, top_n=limit)
                if rank is None or rank.empty:
                    continue
                title = "%s 设备 %s 接口 CRC 差值排行 Top%d" % (date, dev, len(rank))
                fig = self._bar_figure(Figure, rank, top_n=limit, log_axis=log_axis)
                if fig is not None:
                    emit("chart", {"figure": fig, "title": title})
                    emitted += 1

        # 5) 走势图（支持多接口对比）
        if params.get("show_trend", False):
            ifaces = [x.strip() for x in iface_raw.replace("，", ",").split(",") if x.strip()]
            # 设备/接口都留空时自动兜底：勾了「走势图」却因为没填参数一张图都不出，
            # 提示又只写在「运行日志」页（用户看的是「图表」页，只会觉得功能没做）。
            if not device:
                picked = self._pick_devices(day, "", 1)
                device = picked[0] if picked else ""
                if device:
                    ctx.log("未指定「设备」，自动选取当日差值最大的设备：%s" % device)
            if device and not ifaces:
                sub = day[day[ch._COL_DEVICE] == device]
                auto = (sub.sort_values(ch._COL_DIFF, ascending=False)[ch._COL_IFACE]
                           .head(_AUTO_TREND_MAX).tolist())
                ifaces = [str(x) for x in auto]
                if ifaces:
                    ctx.log("未指定「接口名称」，自动取该设备差值最大的 %d 个接口：%s"
                            % (len(ifaces), "、".join(ifaces)))
            if not device:
                ctx.log("当日无任何设备数据，无法生成走势图", level="WARN")
            elif not ifaces:
                ctx.log("设备「%s」当日没有接口数据，无法生成走势图" % device, level="WARN")
            elif len(all_dates) < 2:
                # 只有 1 天：画不出走势。只给**一张**提示卡，不要按接口各出一张
                # （5 个接口就是 5 张一模一样的"仅 1 天"，整页都是重复提示）
                ctx.log("历史仅 %d 天（%s），走势图需要至少 2 天数据，"
                        "本次只输出一张提示卡" % (len(all_dates), all_dates[-1]),
                        level="WARN")
                emit("chart", {"figure": _insufficient_figure(Figure, len(all_dates)),
                    "title": "CRC 走势：历史数据不足（仅 %d 天）" % len(all_dates)})
                emitted += 1
            else:
                # 多接口先出一张对比图（同一时间轴叠所有接口），再逐接口出明细
                if len(ifaces) >= 2:
                    cmp_fig = self._compare_figure(Figure, df, device, ifaces)
                    if cmp_fig is not None:
                        emit("chart", {"figure": cmp_fig,
                                       "title": "%s 多接口 CRC 走势对比（%d 个）"
                                                % (device, len(ifaces))})
                        emitted += 1
                for iface in ifaces:
                    fig = self._trend_figure(Figure, df, device, iface)
                    if fig is None:
                        ctx.log("接口「%s」在该设备下不足 2 天数据，已跳过" % iface,
                                level="WARN")
                        continue
                    emit("chart", {"figure": fig,
                                   "title": "%s / %s 的 CRC 走势" % (device, iface)})
                    emitted += 1

        ctx.log("图表生成完成：%d 张" % emitted)
        emit("done", {"summary": {"total": emitted, "charts": emitted}})

    @staticmethod
    def _pick_devices(day, device, max_devices):
        """确定按设备出图的设备列表：指定则单台，否则取当日差值最大的前 N 台。"""
        if device:
            return [device]
        agg = (day.assign(_a=day[ch._COL_DIFF].abs())
                  .groupby(ch._COL_DEVICE)["_a"].sum()
                  .sort_values(ascending=False).head(max_devices))
        return agg.index.tolist()

    @staticmethod
    def _project_root(ctx):
        """从 ctx.output_dir 反推项目根目录（output 的上一级）。"""
        p = ctx.output_dir
        while p.name != "output" and p.parent != p:
            p = p.parent
        return p.parent

    # —— 图表构建（不绑定 canvas，线程安全）——
    @staticmethod
    def _bar_figure(Figure, rank, top_n, log_axis=True):
        """单日接口差值排行条形图。rank 需含 设备名称/接口名称/CRC差值。"""
        if rank is None or rank.empty:
            return None
        sub = rank.head(min(top_n, _PER_CHART_MAX))
        # 数值大的排上方（barh 自下而上，故反转）
        rows = [{"iface": str(r[ch._COL_IFACE]),
                 "device": str(r[ch._COL_DEVICE]),
                 "value": int(r[ch._COL_DIFF])}
                for _, r in sub.iterrows()][::-1]
        return CrcTrendTool._draw_barh(
            Figure, rows, value_label="CRC差值", log_axis=log_axis,
            hover_sub=lambda r: r["device"][:28])

    @staticmethod
    def _worsen_figure(Figure, worse, log_axis=True):
        """持续恶化接口排行：条长=累计增量，悬浮显示连续天数与最近差值。"""
        if worse is None or worse.empty:
            return None
        rows = [{"iface": str(r[ch._COL_IFACE]),
                 "device": str(r[ch._COL_DEVICE]),
                 "value": int(r["累计增量"]),
                 "days": int(r["连续天数"]),
                 "last": int(r["最近差值"])}
                for _, r in worse.iterrows()][::-1]
        return CrcTrendTool._draw_barh(
            Figure, rows, value_label="累计增量", log_axis=log_axis,
            hover_sub=lambda r: "%s\n连续 %d 天 · 最近 %d"
                                % (r["device"][:26], r["days"], r["last"]))

    @staticmethod
    def _draw_barh(Figure, rows, value_label, hover_sub=None, log_axis=True):
        """通用横向条形图：rows 已按显示顺序（下→上）排好。

        log_axis：CRC 差值实测跨度 3409 ~ 6576999（3 个数量级），线性坐标下
        除第一名外全被压成亚像素；对数坐标能把 20 条都画出来。
        """
        if not rows:
            return None
        labels = [_short(r["iface"], 14) for r in rows]
        values = [r["value"] for r in rows]
        use_log = bool(log_axis) and len(values) > 1 and any(v != 0 for v in values)

        # 用 constrained_layout 自动分配留白：卡片宽度/高度由界面决定，
        # 手动按比例算边距在宽卡片上会留出一大片死白（曾出现左半屏全空）。
        # 悬浮提示框已 set_in_layout(False)，不会参与布局，所以鼠标移动时不会跳动。
        fig = Figure(figsize=(4.2, 2.85), layout="constrained")
        ax = fig.add_subplot(111)
        colors = ["#e74c3c" if v > 0 else "#27ae60" for v in values]
        ax.barh(range(len(labels)), values, color=colors, height=0.72)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels, fontsize=7)
        ax.set_xlabel(value_label, fontsize=8)
        ax.tick_params(axis="x", labelsize=6.5)
        ax.grid(axis="x", alpha=0.3)
        # x 轴：只放 3 个刻度 + 万/亿简写。卡片宽度只有 300 多像素，
        # 5 个「300万」这样的刻度必然首尾相撞
        from matplotlib.ticker import (FuncFormatter, MaxNLocator, FixedLocator,
                                       NullFormatter, NullLocator)
        if use_log:
            # symlog 而不是 log：接口差值可能是 0 或负数（CRC 回落），
            # 纯 log 轴遇到非正值会直接把这根柱子丢掉甚至报错。
            # linthresh=1 让 |x|<1 走线性，其余按对数，正值区与 log 等价。
            ax.set_xscale("symlog", linthresh=1.0)
            # 全为正数时把左边界收到最小值的一半：symlog 默认会把 0 也纳入视野，
            # 左边 0~最小值 那段是空的（线性区），白白浪费 10%~20% 的轴宽
            if min(values) > 0:
                ax.set_xlim(min(values) * 0.5, max(values) * 2.5)
            else:
                # 有 0 或负值时也把左边界钉死：交给 symlog 自动定界的话，
                # 左边界会落到 -1e-9 一带，接着按 log10 数量级挑刻度就会算出
                # k0=-9，最终整根轴只剩 "0" 和 "1,000" 两个标签。
                ax.set_xlim(min(0, min(values)) - 1, max(values) * 2.5)
            # 刻度自己挑：LogLocator(numticks=4) 在 127px 宽的轴上照样吐 6 个，
            # 标签直接叠成 "100万100万100万"。这里每个数量级取一个，最多 3 个。
            lo, hi = ax.get_xlim()
            k0 = int(math.floor(math.log10(max(lo, 1.0))))
            k1 = int(math.ceil(math.log10(max(hi, 1e-9))))
            ks = list(range(k0, k1 + 1))
            if len(ks) > 3:
                step = int(math.ceil(len(ks) / 3.0))
                ks = ks[::step]
            ax.xaxis.set_major_locator(FixedLocator([10 ** k for k in ks]))
            ax.xaxis.set_minor_locator(NullLocator())
            ax.xaxis.set_minor_formatter(NullFormatter())
            ax.grid(axis="x", which="minor", alpha=0.0)
        else:
            _ensure_span(ax, "x")
            ax.xaxis.set_major_locator(MaxNLocator(nbins=3, integer=True))
        ax.xaxis.set_major_formatter(FuncFormatter(_cn_num))
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

        items = []
        for r in rows:
            sub = hover_sub(r) if hover_sub else r.get("device", "")[:28]
            items.append({"title": r["iface"], "sub": sub, "value": r["value"]})
        fig._ntk_hover = {"horizontal": True, "value_label": value_label, "items": items}
        return fig

    @staticmethod
    def _trend_figure(Figure, df, device, iface):
        """单个接口的每日 CRC 走势（左轴：当日差值柱 + 右轴：当日累计线）。

        差值画柱、累计画线：两条都画折线的话，累计值本质是差值的逐日累加，
        形状几乎一样，在图上完全重叠，看不出哪条是哪条。
        """
        trend = ch.iface_trend(df, device, iface)
        if trend.empty or len(trend) < 2:
            # 单点构不成走势，直接跳过（由调用方出一张总的提示卡）
            return None
        dates = trend[ch._COL_DATE].tolist()
        diff = trend[ch._COL_DIFF].tolist()
        today = trend[ch._COL_TODAY].tolist()
        xs = list(range(len(dates)))

        # 竖一点的比例：卡片本身就是竖长形，横向铺满会被压成一条
        fig = Figure(figsize=(3.6, 3.0), layout="constrained")
        ax1 = fig.add_subplot(111)
        ax1.bar(xs, diff, width=0.55, color="#e74c3c", alpha=0.85, label="差值")
        ax1.set_ylabel("差值", color="#e74c3c", fontsize=8)
        ax1.tick_params(axis="y", labelcolor="#e74c3c", labelsize=7)
        ax1.tick_params(axis="x", labelsize=7)
        ax1.grid(alpha=0.3, axis="y")

        ax2 = ax1.twinx()
        ax2.plot(xs, today, marker="s", ms=3.5, lw=1.6,
                 color="#3498db", label="累计")
        ax2.set_ylabel("累计", color="#3498db", fontsize=8)
        ax2.tick_params(axis="y", labelcolor="#3498db", labelsize=7)
        # 折线压到柱子之上，避免被柱遮住
        ax2.set_zorder(ax1.get_zorder() + 1)
        ax2.patch.set_visible(False)

        # 明细图左右各挂一个 y 轴标签，坐标区只剩 ~130px，只放得下 2 个日期
        ticks = _tick_positions(len(dates), 2)
        ax1.set_xticks(ticks)
        ax1.set_xticklabels([dates[i][-5:] for i in ticks], fontsize=7)
        ax1.axhline(0, color="#888888", lw=0.6, ls="--", alpha=0.7)
        # 双轴都用万/亿简写，避免窄图刻度重叠；范围退化时撑开，防止 0 0 0 0 0
        from matplotlib.ticker import FuncFormatter, MaxNLocator
        _ensure_span(ax1, "y")
        _ensure_span(ax2, "y")
        for axis in (ax1.yaxis, ax2.yaxis):
            axis.set_major_locator(MaxNLocator(nbins=4, integer=True))
            axis.set_major_formatter(FuncFormatter(_cn_num))

        h1, l1 = ax1.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        # 图例放到坐标区上方横排：卡片是竖长形，图例放在轴内会压住右侧刻度
        ax1.legend(h1 + h2, l1 + l2, fontsize=7, loc="lower left",
                   bbox_to_anchor=(0.0, 1.01), ncol=2, frameon=False,
                   handlelength=1.2, columnspacing=0.9, borderaxespad=0.1)
        ax1.spines["top"].set_visible(False)

        fig._ntk_hover = {
            "horizontal": False,
            "value_label": "CRC差值",
            "items": [{"title": dates[i],
                       "sub": "累计 %s" % today[i],
                       "value": diff[i]} for i in range(len(dates))],
        }
        return fig

    @staticmethod
    def _compare_figure(Figure, df, device, ifaces):
        """多接口走势对比：同一台设备多个接口的每日 CRC 差值画在一张图上。

        逐接口出一叠明细图只能一个个看，横向比对"哪个口涨得快"要来回切；
        这张图把 N 个接口叠在同一时间轴上，一眼能看出谁在恶化。
        单点提示框放不下 N 个接口的数值，所以这张图不挂悬浮数据。
        """
        dates = None
        series = []
        for ifc in ifaces:
            tr = ch.iface_trend(df, device, ifc)
            if tr.empty or len(tr) < 2:
                continue
            if dates is None:
                dates = tr[ch._COL_DATE].tolist()
            series.append((ifc, tr))
        if dates is None or len(series) < 2:
            return None          # 不足两个接口就没有"对比"可言

        from matplotlib.ticker import FuncFormatter, MaxNLocator
        fig = Figure(figsize=(3.6, 3.0), layout="constrained")
        ax = fig.add_subplot(111)
        xs = list(range(len(dates)))
        for ifc, tr in series:
            m = dict(zip(tr[ch._COL_DATE].tolist(), tr[ch._COL_DIFF].tolist()))
            # 某天该接口缺数据时留空（NaN）：折线断开，比补 0 更诚实
            ys = [m.get(d, float("nan")) for d in dates]
            ax.plot(xs, ys, marker="o", ms=3, lw=1.5, label=_short(ifc, 10))
        ax.set_ylabel("差值", fontsize=8)
        ax.grid(alpha=0.3, axis="y")
        ax.axhline(0, color="#888888", lw=0.6, ls="--", alpha=0.7)
        ticks = _tick_positions(len(dates), 3)
        ax.set_xticks(ticks)
        ax.set_xticklabels([dates[i][-5:] for i in ticks], fontsize=7)
        ax.tick_params(axis="y", labelsize=7)
        _ensure_span(ax, "y")
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
        ax.yaxis.set_major_formatter(FuncFormatter(_cn_num))
        # 图例单列放在坐标区左上：接口名横排 3 个就要 300px+，
        # 比卡片还宽，constrained_layout 会把坐标区压成一条竖线（实测只剩 10px 宽）
        ax.legend(fontsize=6.5, loc="upper left", ncol=1, frameon=True,
                  framealpha=0.85, edgecolor="#dddddd", handlelength=1.2,
                  borderpad=0.3, labelspacing=0.35)
        ax.spines["top"].set_visible(False)
        return fig
