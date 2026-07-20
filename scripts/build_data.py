# -*- coding: utf-8 -*-
"""
韩国收盘去杠杆监控 · 静态站数据构建
抓取 KIM Premium 公开 JSON（meta / series / etf），按“三道门 + 三级风险”框架
计算信用融资出清进度，并把 artifact 写入 korea-deleveraging/data.js：
    window.SNAPSHOT = { ...artifact... };
原则：数据滞后明确标注；二级/三级缺数据只标“未核验”，不从股市下跌推导金融危机。
"""
import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    SEOUL = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover
    SEOUL = None

BASE = "https://kimpremium.com/data/"
UA = {"User-Agent": "Mozilla/5.0 (delev-monitor)"}

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "korea-deleveraging" / "data.js"


def fetch(name):
    req = urllib.request.Request(BASE + name, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.load(resp)


def fmt_date(raw):
    s = str(raw)
    return "%s-%s-%s" % (s[0:4], s[4:6], s[6:8])


def seoul_now():
    if SEOUL is not None:
        return datetime.now(SEOUL)
    return datetime.utcnow() + timedelta(hours=9)


def expected_trading_day(now):
    """最近一个应当已发布数据的工作日。
    网站约首尔 21:30 生成：早于该时刻则期待前一工作日；周末回退到周五。
    韩国假日无法得知，滞后统一以“或遇休市”标注。"""
    d = now.date()
    if now.hour < 21 or (now.hour == 21 and now.minute < 30):
        d -= timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat 6=Sun
        d -= timedelta(days=1)
    return d


def rolling5(values):
    out = []
    for i in range(len(values)):
        window = [x for x in values[max(0, i - 4):i + 1] if x is not None]
        out.append(round(sum(window) / len(window), 1) if window else None)
    return out


def build_artifact():
    meta = fetch("meta.json")
    series = fetch("series.json")
    etf = fetch("etf.json")

    asof = fmt_date(meta["asof"])
    generated = meta.get("generated", "")

    d = series["d"]
    fin_s = series["fin"]
    dep_s = series["dep"]
    liq_s = series["liq"]
    liqR_s = series["liqR"]
    kospi_s = series["kospi"]

    kpi_m = meta["kpi"]
    fin = fin_s[-1]
    dep = dep_s[-1]
    r2 = kpi_m["r2"]
    liq_today = liq_s[-1]
    liqR = liqR_s[-1]
    liq5d = kpi_m["liq5d"]
    liqPct = kpi_m["liqPct"]
    kospi = kospi_s[-1]
    kospi_chg = (kospi_s[-1] / kospi_s[-2] - 1) * 100

    # 2026 年以来峰值（本轮去杠杆起点）
    idx_2026 = next(i for i, x in enumerate(d) if str(x) >= "20260101")
    peak = max(fin_s[idx_2026:])
    peak_date = fmt_date(d[idx_2026 + fin_s[idx_2026:].index(peak)])

    fin_dd = (fin / peak - 1) * 100
    dist30 = (fin - 30) / fin * 100
    dist28 = (fin - 28) / fin * 100
    progress = max(0.0, min(100.0, (peak - fin) / (peak - 28) * 100))

    # ETF
    etf_flow = etf["flow"]            # 亿韩元 / 日
    etf_flow5 = sum(etf_flow[-5:]) / 10000.0   # -> 万亿
    etf_aum = etf["kpi"]["aum"]

    # 存管金 5 日变化（万亿）
    dep_chg5 = dep_s[-1] - dep_s[-6]
    # KOSPI 5 日未创新低
    kospi_ok = kospi_s[-1] > min(kospi_s[-6:-1])

    # ---- 数据滞后检查（首尔时间）----
    now = seoul_now()
    exp = expected_trading_day(now)
    asof_d = datetime.strptime(asof, "%Y-%m-%d").date()
    stale = asof_d < exp
    stale_note = (
        "网站数据停留在 %s；%s 读数尚未更新（或遇韩国休市）。以下为 %s 数据，请勿当作最新交易日读数。"
        % (asof, exp.isoformat(), asof)
    ) if stale else ""

    # ---- 三道门 ----
    quantity_items = [
        {"label": "信用融资进入 30 万亿以下", "value": "%.2f 万亿" % fin, "pass": fin < 30, "verified": True},
        {"label": "融资/存管金降到 28% 以下", "value": "%.2f%%" % r2, "pass": r2 < 28, "verified": True},
        {"label": "杠杆ETF近5日净申赎转负", "value": "%+.2f 万亿" % etf_flow5, "pass": etf_flow5 < 0, "verified": True},
    ]
    capitulation_items = [
        {"label": "强平5日均值退出90分位以上", "value": "%.0f 分位" % liqPct, "pass": liqPct < 90, "verified": True},
        {"label": "散户停止逆势申购杠杆ETF", "value": "%+.2f 万亿/5日" % etf_flow5, "pass": etf_flow5 <= 0, "verified": True},
        {"label": "存管金不再快速流失", "value": "5日 %+.2f 万亿" % dep_chg5, "pass": dep_chg5 >= -1.0, "verified": True},
    ]
    price_items = [
        {"label": "KOSPI 波动后不再创新低（5日）", "value": "%.1f" % kospi, "pass": bool(kospi_ok), "verified": True},
        {"label": "外资和机构抛售放缓", "value": "需外资流向数据", "pass": False, "verified": False},
        {"label": "半导体龙头不再承担提款机角色", "value": "需个股资金流数据", "pass": False, "verified": False},
    ]

    def gate(items):
        return {
            "passed": sum(1 for it in items if it["pass"] is True),
            "total": len(items),
            "items": items,
        }

    gates = {
        "quantity": gate(quantity_items),
        "capitulation": gate(capitulation_items),
        "price": gate(price_items),
    }

    # ---- 三级状态 ----
    if fin < 30 and r2 < 28 and etf_flow5 < 0:
        l1 = {"state": "green", "label": "基本出清",
              "note": "融资 %.1f 万亿 · ETF 转净赎回" % fin}
    elif liqPct >= 90 or etf_flow5 > 0:
        bits = ["融资较峰值 %.1f%%" % fin_dd]
        if etf_flow5 > 0:
            bits.append("ETF 仍净申购 %+.2f 万亿" % etf_flow5)
        if liqPct >= 90:
            bits.append("强平 %.0f 分位" % liqPct)
        l1 = {"state": "red", "label": "清算进行中", "note": " · ".join(bits)}
    else:
        l1 = {"state": "amber", "label": "缓和未出清",
              "note": "融资较峰值 %.1f%%" % fin_dd}
    l2 = {"state": "unverified", "label": "未核验",
          "note": "缺券商CP续发/期限/利差数据"}
    l3 = {"state": "grey", "label": "暂未证实",
          "note": "未见外汇掉期·银行间·央行救助证据"}

    # ---- 结论 ----
    if etf_flow5 > 0:
        verdict = ("显性融资较峰值下降 %.1f%%，但杠杆ETF近5日仍净申购 %.2f 万亿——"
                   "嵌入式杠杆继续增加，出清未完成。" % (abs(fin_dd), etf_flow5))
    else:
        verdict = ("显性融资较峰值下降 %.1f%%，杠杆ETF近5日转为净赎回 %.2f 万亿，"
                   "进入出清确认期，仍需数量门与价格门共振。" % (abs(fin_dd), abs(etf_flow5)))
    verdict += " 数量门 %d/3 · 投降门 %d/3 · 价格门 %d/3。" % (
        gates["quantity"]["passed"], gates["capitulation"]["passed"], gates["price"]["passed"])

    # ---- 图表序列 ----
    n90 = min(90, len(d))
    liq5d_s = rolling5(liq_s)
    n60 = min(60, len(etf["d"]))

    return {
        "asof": asof,
        "generated": generated,
        "stale": stale,
        "staleNote": stale_note,
        "verdict": verdict,
        "kpi": {
            "fin": round(fin, 3),
            "finPeak": round(peak, 3),
            "finPeakDate": peak_date,
            "finDdPct": round(fin_dd, 2),
            "dist30Pct": round(dist30, 2),
            "dist28Pct": round(dist28, 2),
            "progressPct": round(progress, 1),
            "r2": r2,
            "dep": round(dep, 3),
            "liqToday": round(liq_today, 1),
            "liq5d": liq5d,
            "liqPct": liqPct,
            "liqR": liqR,
            "etfAum": etf_aum,
            "etfFlow5d": round(etf_flow5, 3),
            "kospi": round(kospi, 2),
            "kospiChgPct": round(kospi_chg, 2),
        },
        "levels": {"l1": l1, "l2": l2, "l3": l3},
        "gates": gates,
        "series": {
            "fin": {"d": [fmt_date(x) for x in d[-n90:]],
                    "v": [round(x, 3) for x in fin_s[-n90:]]},
            "liq5d": {"d": [fmt_date(x) for x in d[-n90:]],
                      "v": liq5d_s[-n90:]},
            "etfFlow": {"d": [fmt_date(x) for x in etf["d"][-n60:]],
                        "v": [round(x, 1) for x in etf_flow[-n60:]]},
        },
    }


def write_data_js(artifact, path=OUT_PATH):
    payload = json.dumps(artifact, ensure_ascii=False, separators=(",", ":"))
    path.write_text("window.SNAPSHOT = " + payload + ";\n", encoding="utf-8")
    return path


def main():
    artifact = build_artifact()
    out = write_data_js(artifact)
    print("wrote %s (asof=%s generated=%s stale=%s)" % (
        out, artifact["asof"], artifact["generated"], artifact["stale"]))


if __name__ == "__main__":
    main()
