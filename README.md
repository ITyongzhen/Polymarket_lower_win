# Polymarket Lower Win

这个项目现在按你要求改成了：

- 所有模拟盘参数统一放在 [`.env`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/.env)
- 中文注释优先
- 日志统一放在 [`Logs/`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/Logs)
- 先跑模拟账户，不碰真实下单

## 现在已经有的能力

### 1. 账户缓存

脚本：

- [`cache_polymarket_profile.py`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/scripts/cache_polymarket_profile.py)

当前 `little-dead` 的本地缓存已经整理好了：

- [`cache_summary.json`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/data/raw/polymarket_profiles/little-dead/cache_summary.json)
- [`activity_trades.jsonl`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/data/raw/polymarket_profiles/little-dead/activity_trades.jsonl)
- [`low_price_summary.json`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/data/raw/polymarket_profiles/little-dead/low_price_summary.json)

### 2. 低概率单边模拟盘

脚本：

- [`run_paper_low_win.py`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/scripts/run_paper_low_win.py)

核心逻辑：

- 同币种同周期同一市场最多 `10` 份，可配
- 支持拆单，默认 `10` 份目标，`2` 份一笔慢慢打
- 区分盘前和盘后窗口
- 根据 Binance 外部价格分成 `flat / mild / stress / wild`
- 根据阶段和外部波动决定是允许买 `0.01`、`0.02` 还是 `0.03`
- 用一个启发式“合理低价概率”模型判断是否存在错价
- 默认加入“结算源不一致保护”：临近结算要求更厚 edge，盘后补单默认关闭
- 双边低价错价盘默认剔除，不混进单边策略收益

### 3. Chainlink 官方报告采集

脚本：

- [`collect_chainlink_reports.py`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/scripts/collect_chainlink_reports.py)

用途：

- 通过 Chainlink Data Streams 官方 WebSocket 订阅 `BTC / ETH / SOL / XRP / DOGE / BNB / HYPE`
- 把原始 `fullReport` 按秒级收到时间落盘到 [`Logs/chainlink_streams/`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/Logs/chainlink_streams)
- 给后面做“更接近 Polymarket 结算源”的回放和统计打底

注意：

- 这个采集器需要你自己的 Chainlink API key / secret
- 当前先做“原始报告落盘”，还没有把 `fullReport` 完整解码成价格字段

## 运行方式

先直接看并改 [`.env`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/.env)。

单轮运行：

```bash
PYTHONPATH=src python3 scripts/run_paper_low_win.py --once
```

持续运行：

```bash
PYTHONPATH=src python3 scripts/run_paper_low_win.py
```

采集 Chainlink 官方报告：

```bash
PYTHONPATH=src python3 scripts/collect_chainlink_reports.py
```

或者直接用项目里的启动脚本：

```bash
bash scripts/start_paper_low_win.sh
bash scripts/start_chainlink_collector.sh
```

这两个脚本会优先使用项目里的 [`.venv/`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/.venv) Python；如果没有，再回退到系统 `python3`。

如果你想保留一份模板：

- [`.env.example`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/.env.example)

## 日志目录

模拟盘所有运行日志都会落到：

- [`Logs/paper_low_win/`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/Logs/paper_low_win)

Chainlink 原始报告会落到：

- [`Logs/chainlink_streams/`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/Logs/chainlink_streams)

PM2 总日志会落到：

- [`Logs/pm2/`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/Logs/pm2)

每次 run 一个子目录，主要文件有：

- `snapshots.jsonl`
- `signals.jsonl`
- `trades.jsonl`
- `state.json`
- `summary_latest.json`

## 当前策略怎么决定买不买

当前不是简单“见到 0.01 就买”，而是这样：

1. 先看是不是低价研究带：默认 `0.001 - 0.03`
2. 再看时间：
   - 盘前可交易区间：默认还剩 `30 - 240` 秒
   - 盘后研究区间：默认最多只容忍结算后 `5` 秒
3. 再看 Binance 外部价格：
   - 如果太平，允许更高一点的买价，例如到 `0.03`
   - 如果只是轻微波动，只允许到 `0.02`
   - 如果偏移已经明显，只允许 `0.01`
   - 如果波动太大，直接跳过
4. 再看结算源差异风险：
   - Polymarket 这类 `Up/Down` 市场常写明按 Chainlink 数据流结算，不是按 Binance K 线结算
   - 所以离结算越近，对“看起来只有一点点 edge”的单子越谨慎
   - 默认禁掉盘后补单，避免吃到源差异带来的假优势
5. 再算一个“合理低价概率”：
   - 时间越早，不确定性越高
   - 外部价格越平，越可能错杀
   - 如果市场和外部价格明显不一致，会额外加分
6. 只有当 `市场低价 < 目标买价` 且 `错价幅度 > 最小 edge` 才会下单

## 当前限制

- 结算仍然先用 Binance 同周期 K 线做代理，不是最终 Polymarket 真实结算源
- 官方市场规则里，这类市场通常写的是 Chainlink Data Streams；因此 Binance 更适合做“外部状态参考”，不适合做“精确结算回放”
- 现在已经补了 Chainlink 官方 WebSocket 原始报告采集器，但还没把 `fullReport` 全量解码回价格字段
- 当前外部价格源里，`HYPE` 已切到 Hyperliquid 官方 K 线接口，因为 Binance 现货没有 `HYPEUSDT`
- 双边低价事件当前只做识别和过滤，没有单独做配对套利引擎

## Chainlink Key 怎么拿

官方文档写得很明确：

- Data Streams 的 REST / WebSocket 都需要官方发放的 API credentials
- 申请入口是 Chainlink 的联系页

你需要做的是：

1. 打开 [Chainlink Data Streams 文档](https://docs.chain.link/data-streams)
2. 点里面的 [Contact us](https://chain.link/contact?ref_id=datastreams)
3. 提交需求时直接写清楚：
   - 你要 `Data Streams mainnet access`
   - 你需要 `REST + WebSocket`
   - 你要的 crypto streams 至少包括 `BTC/USD`、`ETH/USD`、`SOL/USD`、`XRP/USD`
   - 你的使用场景是 `Polymarket crypto up/down market research and paper trading`
4. 等官方给你：
   - `API key`
   - `API secret`
   - 确认你有 `wss://ws.dataengine.chain.link` 的访问权限

拿到以后填进 [`.env`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/.env)：

```dotenv
PM_CHAINLINK_API_KEY=你的key
PM_CHAINLINK_API_SECRET=你的secret
```

## 上云服务器要做什么

建议你最终至少做这几件事：

1. 准备一台长期在线的 Linux 云服务器，装好 `Python 3.11+`、`pip`、`nodejs`、`pm2`
2. 把项目上传到服务器
3. 在项目目录执行：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .
```

4. 填好 [`.env`](/Users/eagle/Documents/eagle/币/web3_all/Polymarket_lower_win/.env)
5. 先手工 smoke 一次：

```bash
bash scripts/start_chainlink_collector.sh --max-messages 3
bash scripts/start_paper_low_win.sh --once
```

6. 再用 PM2 托管：

```bash
pm2 start ecosystem.config.cjs
pm2 status
pm2 logs pm-lower-win-chainlink
pm2 logs pm-lower-win-paper
pm2 save
pm2 startup
```

## 服务器上的关键注意点

- **时间同步必须准确**：Chainlink WebSocket 鉴权时间戳默认只容忍约 `5` 秒偏差。服务器务必开启 `chrony` 或 `systemd-timesyncd`
- **先跑 Chainlink，再跑模拟盘**：这样后面替换成官方结算源更平滑
- **网络要能访问外网 HTTPS / WSS**：至少要能访问 Polymarket、Binance、Chainlink
- **先只跑模拟盘**：现在还没接真实下单
