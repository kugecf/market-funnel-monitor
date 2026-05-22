# 美股漏斗策略 · 指标信号推送器
# Market Funnel Strategy – Signal & Heartbeat Monitor
#
# 双轨制推送：
#   周一至周四：仅触发条件时发送抄底警报
#   周五收盘后：必发每周常规报告（心跳信号）+ 可选紧急警报
#
# 通知方式（参考 kugecf/daily-report）：
#   主通道：Server酱 (SERVER_CHAN_KEY) → 微信推送
#   备通道一：PushPlus (PUSHPLUS_TOKEN) → 微信推送
#   备通道二：通用 WebHook (NOTIFY_URL) → Bark 等
#
# 依赖：requests, yfinance, pandas

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

# ==================== Logging ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("funnel-monitor")

# ==================== Configuration ====================
SERVER_CHAN_KEY = os.environ.get("SERVER_CHAN_KEY", "")       # Server酱 SendKey
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")         # PushPlus Token
NOTIFY_URL = os.environ.get("NOTIFY_URL", "")                 # 通用 WebHook (Bark 等)
MAX_RETRIES = 3
RETRY_DELAY = 5

# 日志文件
LOG_CSV = Path("market_log.csv")

# 指数代码
INDEX_SP500 = "^GSPC"
INDEX_NASDAQ100 = "^NDX"
INDEX_VIX = "^VIX"

# 阈值参数
RSI_OVERSOLD = 40
VIX_ELEVATED = 25
VIX_EXTREME = 35
FG_EXTREME_FEAR = 15
MA200_DEVIATION_BREACH = 0


# ==================== Data Fetching ====================

def fetch_fear_greed() -> float | None:
    """抓取恐惧贪婪指数 (0-100)。多源回退：CNN 新版 -> CNN 旧版 -> Alternative.me"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # 源 1: CNN 新版 API (graphdata)
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers=headers, timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            score = float(data["fear_and_greed"]["score"])
            log.info("恐惧贪婪指数 (CNN): %.1f", score)
            return score
    except Exception:
        log.debug("CNN graphdata API 失败")

    # 源 2: CNN 旧版 API (graph/current)
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graph/current",
            headers=headers, timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            score = float(data["fear_and_greed"]["score"])
            log.info("恐惧贪婪指数 (CNN legacy): %.1f", score)
            return score
    except Exception:
        log.debug("CNN legacy API 失败")

    # 源 3: Alternative.me 加密货币版 (近似参考)
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            headers=headers, timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            score = float(data["data"][0]["value"])
            log.info("恐惧贪婪指数 (Alternative.me): %.1f", score)
            return score
    except Exception:
        log.debug("Alternative.me 失败")

    log.warning("恐惧贪婪指数全部数据源失败")
    return None

def calc_indicators(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1y")
        if df.empty:
            log.warning("%s: 无历史数据", symbol)
            return None

        close = df["Close"]

        ma200 = close.rolling(window=200).mean()
        price = close.iloc[-1]
        ma200_val = ma200.iloc[-1]
        ma200_dev = ((price - ma200_val) / ma200_val) * 100 if not pd.isna(ma200_val) else None

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss
        rsi_val = (100 - (100 / (1 + rs))).iloc[-1]

        result = {"price": price, "rsi": rsi_val, "ma200_dev": ma200_dev}
        log.info("%s: price=%.2f  RSI=%.2f  MA200_dev=%s", symbol, price, rsi_val, f"{ma200_dev:.2f}%" if ma200_dev is not None else "N/A")
        return result
    except Exception:
        log.exception("%s: 指标计算失败", symbol)
        return None


def fetch_vix() -> float | None:
    try:
        vix_ticker = yf.Ticker(INDEX_VIX)
        df = vix_ticker.history(period="5d")
        if df.empty:
            log.warning("VIX 数据为空")
            return None
        vix_val = float(df["Close"].iloc[-1])
        log.info("VIX: %.2f", vix_val)
        return vix_val
    except Exception:
        log.exception("VIX 抓取失败")
        return None


# ==================== Notification (参考 kugecf/daily-report) ====================

def send_via_server_chan(title: str, content: str) -> bool:
    """通过 Server酱 发送微信推送（主通道）"""
    if not SERVER_CHAN_KEY:
        return False

    url = f"https://sctapi.ftqq.com/{SERVER_CHAN_KEY}.send"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(url, data={"title": title, "desp": content}, timeout=15)
            if r.status_code < 400:
                log.info("Server酱发送成功 (HTTP %d)", r.status_code)
                return True
            log.warning("Server酱返回非 2xx: HTTP %d", r.status_code)
        except Exception:
            log.exception("Server酱发送异常 (attempt %d/%d)", attempt, MAX_RETRIES)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    log.error("Server酱发送最终失败")
    return False


def send_via_pushplus(title: str, content: str) -> bool:
    """通过 PushPlus 发送微信推送（备通道一）"""
    if not PUSHPLUS_TOKEN:
        return False

    url = "http://www.pushplus.plus/send"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(url, json={
                "token": PUSHPLUS_TOKEN,
                "title": title,
                "content": content,
                "template": "txt",
            }, timeout=15)
            if r.status_code < 400:
                log.info("PushPlus 发送成功 (HTTP %d)", r.status_code)
                return True
            log.warning("PushPlus 返回非 2xx: HTTP %d", r.status_code)
        except Exception:
            log.exception("PushPlus 发送异常 (attempt %d/%d)", attempt, MAX_RETRIES)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    log.error("PushPlus 发送最终失败")
    return False


def send_via_webhook(msg: str) -> bool:
    """通过通用 WebHook 发送（备通道二，如 Bark）"""

    if not NOTIFY_URL:
        return False

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(NOTIFY_URL, json={"text": msg}, timeout=15)
            if r.status_code < 400:
                log.info("WebHook 发送成功 (HTTP %d)", r.status_code)
                return True
            log.warning("WebHook 返回非 2xx: HTTP %d", r.status_code)
        except Exception:
            log.exception("WebHook 发送异常 (attempt %d/%d)", attempt, MAX_RETRIES)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    log.error("WebHook 发送最终失败")
    return False


def send_notification(title: str, msg: str) -> bool:
    """双通道通知：优先 Server酱，回退 WebHook，均失败则打印"""
    if SERVER_CHAN_KEY:
        ok = send_via_server_chan(title, msg)
        if ok:
            return True
        log.warning("Server酱失败，尝试 PushPlus 备通道...")

    if PUSHPLUS_TOKEN:
        ok = send_via_pushplus(title, msg)
        if ok:
            return True
        log.warning("PushPlus 失败，尝试 WebHook 备通道...")

    if NOTIFY_URL:
        ok = send_via_webhook(msg)
        if ok:
            return True

    # 两个通道都不可用或都失败，打印到控制台
    if not SERVER_CHAN_KEY and not NOTIFY_URL:
        log.warning("未配置任何通知通道 (SERVER_CHAN_KEY / PUSHPLUS_TOKEN / NOTIFY_URL)")
    print(f"\n{'='*50}\n[{title}]\n{msg}\n{'='*50}\n")
    return False


# ==================== Logging to CSV ====================

def append_log(spy: dict, qqq: dict, vix: float, fg: float, score: int, triggers: list[str]) -> None:
    """追加运行日志到 market_log.csv（参考 kugecf/daily-report 的持久化模式）"""
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "spy_price": round(spy["price"], 2),
        "spy_rsi": round(spy["rsi"], 2),
        "spy_ma200_dev": f"{spy['ma200_dev']:.2f}" if spy.get("ma200_dev") is not None else "",
        "qqq_price": round(qqq["price"], 2),
        "qqq_rsi": round(qqq["rsi"], 2),
        "qqq_ma200_dev": f"{qqq['ma200_dev']:.2f}" if qqq.get("ma200_dev") is not None else "",
        "vix": round(vix, 2),
        "fear_greed": round(fg, 1),
        "funnel_score": score,
        "triggers": " | ".join(triggers) if triggers else "",
    }
    df = pd.DataFrame([row])
    write_header = not LOG_CSV.exists()
    df.to_csv(LOG_CSV, mode="a", header=write_header, index=False, encoding="utf-8")
    log.info("日志已写入 %s", LOG_CSV)


# ==================== Scoring Engine ====================

def compute_score(spy: dict, qqq: dict, vix: float, fg: float) -> tuple[int, list[str]]:
    score = 0
    triggers: list[str] = []

    if qqq["rsi"] < RSI_OVERSOLD and vix > VIX_ELEVATED:
        score += 2
        triggers.append(f"梯队一: QQQ RSI {qqq['rsi']:.1f}<{RSI_OVERSOLD} & VIX {vix:.1f}>{VIX_ELEVATED}")

    if spy.get("ma200_dev") is not None and spy["ma200_dev"] <= MA200_DEVIATION_BREACH:
        score += 2
        triggers.append(f"梯队二: S&P500 跌破200MA (偏离度 {spy['ma200_dev']:.2f}%)")

    if vix > VIX_EXTREME or fg < FG_EXTREME_FEAR:
        score += 2
        triggers.append(f"梯队三: VIX {vix:.1f}>{VIX_EXTREME} 或 FG {fg:.1f}<{FG_EXTREME_FEAR}")

    return score, triggers


# ==================== Message Builder ====================

def build_snapshot(spy: dict, qqq: dict, vix: float, fg: float) -> str:
    ma200_str = f"{spy['ma200_dev']:.2f}%" if spy.get("ma200_dev") is not None else "数据不足"
    return (
        "---------------------------\n"
        "📊 实时数据快照:\n"
        f"- S&P 500 距 200MA 偏离: {ma200_str}\n"
        f"- Nasdaq 100 日线 RSI: {qqq['rsi']:.2f}\n"
        f"- VIX 恐慌指数: {vix:.2f}\n"
        f"- CNN 恐惧贪婪指数: {fg:.1f}\n"
        "---------------------------\n"
        "🔗 手动复核链接:\n"
        "- S&P 500: https://www.tradingview.com/symbols/SPX/\n"
        "- Nasdaq 100: https://www.tradingview.com/symbols/NASDAQ-NDX/\n"
        "- VIX: https://www.tradingview.com/symbols/TVC-VIX/\n"
        "- CNN 恐惧贪婪: https://www.cnn.com/markets/fear-and-greed\n"
        "---------------------------"
    )


# ==================== Main

def main() -> None:
    log.info("===== 美股漏斗监控启动 =====")

    # 1. 数据采集
    spy = calc_indicators(INDEX_SP500)
    qqq = calc_indicators(INDEX_NASDAQ100)
    vix = fetch_vix()
    fg = fetch_fear_greed()

    # 完整性检查
    missing = []
    if spy is None:
        missing.append("S&P 500")
    if qqq is None:
        missing.append("Nasdaq 100")
    if vix is None:
        missing.append("VIX")
    if fg is None:
        missing.append("Fear & Greed")

    if missing:
        msg = f"⚠️ 美股漏斗监控异常：以下数据抓取失败 → {', '.join(missing)}"
        log.error(msg)
        send_notification("漏斗监控异常", msg)
        sys.exit(1)

    assert spy and qqq and vix is not None and fg is not None

    # 2. 计分
    score, triggers = compute_score(spy, qqq, vix, fg)
    log.info("漏斗综合得分: %d / 6, 触发条件: %d 个", score, len(triggers))

    # 3. 时间判断
    now_utc = datetime.now(timezone.utc)
    is_friday = now_utc.weekday() == 4
    is_weekly_report = is_friday or os.environ.get("FORCE_WEEKLY", "").lower() == "true"

    snapshot = build_snapshot(spy, qqq, vix, fg)

    # 4. 持久化日志
    append_log(spy, qqq, vix, fg, score, triggers)

    # ==================== 消息分发 ====================

    # A) 周五常规报告（心跳信号）
    if is_weekly_report:
        market_status = (
            "🟢 市场整体安全，未触发抄底信号。继续持有现金拿固收利息。"
            if score == 0
            else "🟡 市场出现波动，部分或全部抄底阀门已打开！"
        )
        weekly_msg = (
            f"📅 【美股策略 · 每周常规报告】\n"
            f"报告时间: {now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"漏斗综合得分: {score} / 6\n"
            f"状态评估: {market_status}\n"
            f"{snapshot}\n"
            f"💡 本报告每周五收盘后准时发送，代表监控系统运行正常。"
        )
        log.info("发送每周常规报告...")
        send_notification("美股策略·每周报告", weekly_msg)

    # B) 抄底警报
    if score > 0:
        alert_msg = (
            f"🚨 【紧急提示：美股漏斗策略抄底警报】\n"
            f"漏斗综合得分: {score} / 6\n"
            f"{snapshot}\n"
            f"🛠 已触发建仓指令:\n" + "\n".join(f"  - {t}" for t in triggers) + "\n"
            f"---------------------------\n"
            f"💡 建议操作: 核对对应梯队，释放现金，买入对应权重的 QQQ / SPY / MDY 进攻资产。"
        )
        log.info("发送抄底警报 (score=%d)...", score)
        send_notification(f"🚨 抄底警报·得分{score}", alert_msg)

    if score == 0 and not is_weekly_report:
        log.info("得分 0/6，非周五，静默观望（不发送消息）")

    log.info("===== 美股漏斗监控完成 =====")


if __name__ == "__main__":
    main()
