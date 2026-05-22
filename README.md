# 美股漏斗策略 · 指标信号推送器

基于级联漏斗计分系统的美股抄底监控机器人。通过 GitHub Actions 定时运行，自动推送市场状态到微信。

> 通知方式参考 [kugecf/daily-report](https://github.com/kugecf/daily-report)

## 双轨推送机制

| 模式 | 触发条件 | 推送内容 |
|------|----------|----------|
| 💓 每周心跳 | 周一晚 8:00（必发） | 确认系统存活 + 常规状态报告 |
| 🔔 抄底警报 | 漏斗得分 > 0（任意交易日） | 实时推送建仓信号 |

## 级联漏斗计分规则

系统从三个梯队打分，满分 6 分：

1. **梯队一（2 分）**：Nasdaq 100 RSI < 40 **且** VIX > 25 → 科技股超卖 + 波动率放大
2. **梯队二（2 分）**：S&P 500 跌破 200 日均线 → 趋势转弱
3. **梯队三（2 分）**：VIX > 35 **或** CNN 恐惧贪婪 < 15 → 极端恐慌 / 流动性危机

得分越高，抄底信号越强。无论起不起飞，每周一都有一条心跳报告告诉你"系统还活着"。

## 通知方式

双通道容灾，主推 Server酱（微信），备选通用 WebHook：

| 通道 | 配置 | 说明 |
|------|------|------|
| Server酱 | `SERVER_CHAN_KEY` | 微信推送（[sct.ftqq.com](https://sct.ftqq.com/) 获取 SendKey） |
| WebHook | `NOTIFY_URL` | 通用通道，支持 Bark / PushPlus / 企业微信等 |

至少配置一个。推荐优先使用 Server酱，消息会直接推送到微信。

## 数据源

- **S&P 500 / Nasdaq 100 / VIX**：Yahoo Finance (`yfinance`)
- **CNN 恐惧贪婪指数**：CNN Business API

## 快速开始

### 1. Fork 或新建仓库

在 GitHub 创建仓库，将本项目代码推送上去。

### 2. 配置 Secrets

在仓库 `Settings → Secrets and variables → Actions` 添加：

```
SERVER_CHAN_KEY = SCT123456...  （Server酱 SendKey，推荐）
NOTIFY_URL     = https://...     （可选备用 WebHook）
```

### 3. 启用 Actions

GitHub Actions 默认对公开仓库启用。如果是私有仓库，确认 Actions 已开启。

### 4. 测试运行

进入 Actions → `Market Funnel Monitor` → `Run workflow`，勾选 `force_weekly = true` 可立即收到一条周报测试消息。

## 本地运行

```bash
pip install -r requirements.txt

# 设置环境变量后运行
$env:SERVER_CHAN_KEY="你的SendKey"    # PowerShell
python main.py
```

## CI 调度

- **自动**：北京时间每周一晚 8:00（UTC 12:00）
- **手动**：Actions 页面 → `Run workflow`，可勾选 `force_weekly` 强制发送周报

## 目录结构

```
├── main.py                        # 核心监控逻辑
├── requirements.txt               # Python 依赖
├── market_log.csv                 # 每次运行的指标日志（自动追加、自动提交）
├── .github/workflows/monitor.yml  # GitHub Actions 定时调度
└── README.md
```
