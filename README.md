# Polymarket Lower Win

Research toolkit and staged execution infrastructure for low-probability Polymarket crypto up/down markets.

This repository focuses on a specific question: can extremely low-priced outcomes in short-horizon Polymarket crypto markets be traded systematically, instead of randomly buying "lottery tickets" that slowly bleed capital.

The current public version is centered on research, data collection, and paper trading. The intended deployment path is staged:

- validate entry logic with replay and paper trading
- improve settlement-quality market data collection with Chainlink
- deploy continuously on a cloud VM with PM2
- only consider live execution after the data and risk controls are sufficiently validated

The current implementation combines:

- public Polymarket market/profile data collection
- paper trading with configurable risk controls
- Chainlink Data Streams raw report collection
- external market context from Binance and Hyperliquid
- local logging for replay, review, and later model improvement

## 中文简介

这是一个围绕 Polymarket 加密货币 `5m / 15m` `Up / Down` 市场的研究项目，目标不是盲目买极低概率事件，而是把“低价是否真的有 edge”拆成可验证的数据问题。

当前公开仓库主要包含：

- 公开账户与市场数据采集
- 低概率单边策略的模拟盘
- Chainlink Data Streams 原始报告采集
- 云服务器与 PM2 持续运行准备

当前阶段以研究、回放、模拟盘为主；后续如果数据验证充分，才会进入更接近生产执行的阶段。

## What This Project Tries To Solve

The original trading idea was based on buying very low-priced outcomes in `5m` and `15m` crypto up/down markets. In practice, that approach can lose money if entries happen:

- too close to settlement
- after the market is already effectively decided
- when the external price has already moved too far
- when the apparent low price is only a false discount caused by market structure, stale quotes, or one-sided liquidity

This project turns that idea into a measurable workflow:

1. collect and analyze historical public trading behavior
2. compare Polymarket prices with external price context
3. simulate entries with configurable filters
4. log every signal and paper trade
5. move settlement research closer to the actual Polymarket resolution source

## Current Scope

Supported symbols currently include:

- `btc`
- `eth`
- `sol`
- `xrp`
- `doge`
- `bnb`
- `hype`

Supported market windows:

- `5m`
- `15m`

Important note:

- `HYPE` does not use Binance spot as its external reference in this repo. It uses Hyperliquid candle data instead, because Binance spot does not provide `HYPEUSDT`.

## Main Components

### 1. Profile Research

Scripts:

- [`scripts/cache_polymarket_profile.py`](scripts/cache_polymarket_profile.py)
- [`scripts/analyze_polymarket_profile.py`](scripts/analyze_polymarket_profile.py)

Purpose:

- cache a public Polymarket profile locally
- inspect low-price entries
- compare entry timing with external price movement
- separate "single-side low-probability bets" from "dual-side low-price pair trades"

### 2. Paper Trading Engine

Script:

- [`scripts/run_paper_low_win.py`](scripts/run_paper_low_win.py)

Core behavior:

- configurable symbols and timeframes
- split-order paper entries
- max shares per market
- pre-close vs post-close logic
- low-price band filters
- volatility and external-price filters
- source-mismatch guard near settlement

Main logic:

- [`src/polymarket_lower_win/paper.py`](src/polymarket_lower_win/paper.py)

### 3. Chainlink Data Streams Collector

Script:

- [`scripts/collect_chainlink_reports.py`](scripts/collect_chainlink_reports.py)

Purpose:

- subscribe to official Chainlink Data Streams via WebSocket
- store raw `fullReport` payloads with local receive timestamps
- build a local archive for later settlement-quality replay and decoding

Main logic:

- [`src/polymarket_lower_win/chainlink_streams.py`](src/polymarket_lower_win/chainlink_streams.py)

### 4. External Price Context

Current external sources:

- Binance spot candles for `btc`, `eth`, `sol`, `xrp`, `doge`, `bnb`
- Hyperliquid candles for `hype`

Main logic:

- [`src/polymarket_lower_win/binance.py`](src/polymarket_lower_win/binance.py)

## Why Chainlink Matters Here

One of the key findings behind this repo is that Binance candles are useful as an external market reference, but they are not always reliable as a precise proxy for Polymarket settlement.

For short-horizon crypto up/down markets, Polymarket rules often reference Chainlink Data Streams. That means near-settlement strategies can be distorted if they assume "Binance close = market resolution".

Because of that, this repo now includes:

- a near-close source mismatch guard in the paper strategy
- a dedicated Chainlink raw report collector
- a path toward replacing Binance settlement proxy logic with Chainlink-based replay

## Configuration

All runtime parameters are stored in:

- [`.env.example`](.env.example)

Your local runtime file should be:

- `.env`

The project intentionally keeps parameters explicit in env variables, including:

- symbols
- timeframes
- share caps
- split-order sizing
- price bands
- timing windows
- Chainlink collector settings
- log locations

## Quick Start

Create a virtual environment and install the project:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .
```

Copy the environment template:

```bash
cp .env.example .env
```

Run one paper-trading cycle:

```bash
PYTHONPATH=src python3 scripts/run_paper_low_win.py --once
```

Run the Chainlink collector:

```bash
PYTHONPATH=src python3 scripts/collect_chainlink_reports.py
```

Convenience wrappers are also provided:

```bash
bash scripts/start_paper_low_win.sh
bash scripts/start_chainlink_collector.sh
```

These wrappers prefer the local `.venv` Python automatically.

If `.env` does not exist yet, these wrappers will auto-create it from `.env.example`.

For a one-line PM2 start on a cloud VM:

```bash
bash scripts/pm2_start_lower_win.sh polymarket-lower-win .env
```

## PM2 Deployment

For cloud deployment, the repository includes:

- [`ecosystem.config.cjs`](ecosystem.config.cjs)
- [`scripts/start_paper_low_win.sh`](scripts/start_paper_low_win.sh)
- [`scripts/start_chainlink_collector.sh`](scripts/start_chainlink_collector.sh)
- [`scripts/pm2_start_lower_win.sh`](scripts/pm2_start_lower_win.sh)
- [`docs/云服务器部署.md`](docs/%E4%BA%91%E6%9C%8D%E5%8A%A1%E5%99%A8%E9%83%A8%E7%BD%B2.md)

Recommended one-line PM2 usage:

```bash
bash scripts/pm2_start_lower_win.sh polymarket-lower-win .env
```

Manual PM2 usage is also available if you prefer the ecosystem file:

```bash
pm2 start ecosystem.config.cjs
pm2 status
pm2 logs pm-lower-win-paper
pm2 logs pm-lower-win-chainlink
pm2 save
pm2 startup
```

Important production note:

- the server clock must stay accurate
- Chainlink authentication is timestamp-sensitive
- enabling `chrony` or `systemd-timesyncd` is strongly recommended

## Logs And Local Data

By default, runtime logs are written under:

- `Logs/paper_low_win/`
- `Logs/chainlink_streams/`
- `Logs/pm2/`

Local raw profile caches and research artifacts are intentionally kept out of version control.

## Current Limitations

- Chainlink raw reports are collected, but `fullReport` is not yet fully decoded into price fields inside the main trading loop
- the paper engine still uses a proxy settlement path in some flows
- the project is still research-oriented and should not be treated as production auto-trading software
- no live trading is enabled in this repository

## Repository Status

This is an active research repo, not a finished trading product.

The main near-term goals are:

- decode Chainlink reports into settlement-usable fields
- replace proxy settlement assumptions where possible
- improve replay analysis for low-probability entries
- keep the strategy in paper mode until the evidence is strong enough

## Notes

- code comments are primarily written in Chinese
- repository presentation is kept clear in English so external reviewers can understand the project quickly
