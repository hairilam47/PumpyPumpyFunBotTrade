# PumpyPumpyFunBotTrade — System Architecture

> Last updated: April 2026  
> A high-performance, three-engine automated trading bot for [Pump.fun](https://pump.fun) on the Solana blockchain.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Map](#2-component-map)
3. [Architecture Diagram](#3-architecture-diagram)
4. [Component Deep-Dives](#4-component-deep-dives)
   - 4.1 [Rust Execution Engine](#41-rust-execution-engine)
   - 4.2 [Python Strategy Engine](#42-python-strategy-engine)
   - 4.3 [Node.js API Server](#43-nodejs-api-server)
   - 4.4 [React Management Dashboard](#44-react-management-dashboard)
5. [Data Flow](#5-data-flow)
6. [Protocol & Port Reference](#6-protocol--port-reference)
7. [Database Schema](#7-database-schema)
8. [Configuration System](#8-configuration-system)
9. [Wallet Management & Auto-Pause](#9-wallet-management--auto-pause)
10. [Strategy Preset System](#10-strategy-preset-system)
11. [MEV Protection](#11-mev-protection)
12. [Security Model](#12-security-model)
13. [Key File Reference](#13-key-file-reference)
14. [Technology Stack](#14-technology-stack)

---

## 1. System Overview

PumpyPumpyFunBotTrade is a distributed, multi-process trading system composed of four tightly coupled services:

| Layer | Technology | Purpose |
|---|---|---|
| Execution Engine | Rust | Solana transaction execution, MEV, wallet workers |
| Strategy Engine | Python | ML signal generation, risk presets, order routing |
| API Server | Node.js / Express | REST API, WebSocket bridge, DB management |
| Dashboard | React / Vite | Real-time monitoring, wallet control, configuration UI |

The system is designed for **low-latency execution** (Rust), **intelligent signal generation** (Python ML), and **operator visibility** (React dashboard), all communicating via gRPC and REST.

---

## 2. Component Map

```
┌─────────────────────────────────────────────────────────────────────┐
│                         OPERATOR BROWSER                            │
│                    React Dashboard  :23183                          │
└────────────────────────────┬────────────────────────────────────────┘
                             │ REST + WebSocket
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Node.js / Express  :8080                         │
│            REST API · WebSocket Bridge · Drizzle ORM                │
└──────────┬──────────────────────────────────────┬───────────────────┘
           │ gRPC :50051                           │ SQL
           ▼                                       ▼
┌──────────────────────┐              ┌────────────────────────┐
│  Rust Trading Engine │              │   PostgreSQL Database  │
│      :50051 gRPC     │              │   (Replit managed)     │
│      :9091 metrics   │              └────────────────────────┘
│                      │
│  ┌─────────────────┐ │
│  │  Wallet Workers │ │
│  │  (per-wallet)   │ │
│  └────────┬────────┘ │
│           │          │
│  ┌────────▼────────┐ │
│  │  Order Manager  │ │
│  │ DecisionEngine  │ │
│  └────────┬────────┘ │
└──────────┬┴──────────┘
           │ JSON-RPC / WebSocket
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Solana Blockchain                                │
│          RPC :8899  ·  WebSocket :8900  ·  Jito Block Engine        │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                  Python Strategy Engine  :8001                      │
│          FastAPI · SniperStrategy · MomentumStrategy                │
│          :9092 Prometheus metrics                                   │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ gRPC :50051
                           └──────────────────► Rust Engine
```

---

## 3. Architecture Diagram

```
Token Launch on Pump.fun
         │
         ▼
  [Solana WebSocket]
         │  TokenDiscoveredEvent
         ▼
  [Rust Engine — PumpFunClient]
         │  gRPC stream (TokenDiscoveredEvent)
         ▼
  [Python Strategy Engine]
         │
         ├── SniperStrategy ──────┐
         │                        ├─► TradeSignal (BUY/SELL/HOLD)
         └── MomentumStrategy ───┘
         │
         │  gRPC SubmitOrder RPC
         ▼
  [Rust Engine — OrderManager]
         │
         ├── DecisionEngine (risk check, auto-pause logic)
         ├── SandwichDetector (MEV risk scoring)
         ├── RPC Manager (failover across multiple endpoints)
         │
         ├── [Jito Bundle] ──► Jito Block Engine ──► Solana
         └── [Direct RPC]  ──► Solana RPC         ──► Solana
         │
         │  Order status stream (gRPC)
         ▼
  [Node.js API Server]
         │
         ├── Stores trade in PostgreSQL (trades table)
         │
         │  WebSocket push
         ▼
  [React Dashboard — Live Feed]
```

---

## 4. Component Deep-Dives

### 4.1 Rust Execution Engine

**Directory:** `rust-engine/`  
**Binary:** `trading-engine`  
**gRPC port:** `50051`  
**Metrics port:** `9091`

The Rust engine is the performance-critical core. It runs as a standalone process exposing a tonic gRPC server that both the Python engine and the Node.js API server connect to.

#### Sub-modules

| Module | Path | Responsibility |
|---|---|---|
| `main` | `src/main.rs` | Bootstrap, DB connect, wallet resolution, gRPC server start |
| `order::manager` | `src/order/manager.rs` | Order lifecycle, MEV routing, status tracking |
| `order::minimal` | `src/order/minimal.rs` | Lightweight order manager for secondary wallets |
| `decision` | `src/decision/mod.rs` | Risk gating, consecutive-reject counter, auto-pause |
| `wallet_worker` | `src/wallet_worker.rs` | Per-wallet goroutine, owns OrderManager + DecisionEngine |
| `orchestrator` | `src/orchestrator.rs` | Multi-wallet orchestration, HALT propagation |
| `pumpfun` | `src/pumpfun/mod.rs` | Pump.fun WebSocket listener, bonding curve math |
| `mev::jito` | `src/mev/jito.rs` | Jito bundle construction and submission |
| `mev::sandwich` | `src/mev/sandwich.rs` | Sandwich risk detection and scoring |
| `mev::mempool` | `src/mev/mempool.rs` | Pending transaction cache |
| `database` | `src/database.rs` | PostgreSQL pool, all DB queries |
| `rpc` | `src/rpc/mod.rs` | RPC endpoint pool with health-checked failover |
| `grpc` | `src/grpc/mod.rs` | tonic server implementation (SubmitOrder, StreamOrders, etc.) |
| `metrics` | `src/metrics.rs` | Prometheus counters/gauges (Prometheus on :9091) |
| `config` | `src/config.rs` | Config loading from env + DB overrides |
| `websocket` | `src/websocket.rs` | Solana WebSocket subscription |

#### DecisionEngine (auto-pause)

The `DecisionEngine` (`src/decision/mod.rs`) is stateful per-wallet:

- Tracks **consecutive order rejections** in memory
- When rejects exceed `auto_pause_threshold` (read from `system_config` DB table at startup), it:
  1. Sets internal `paused` flag
  2. Writes a row to `wallet_alerts` with the reject count and reason
  3. Sets wallet `status = 'paused'` in `wallet_registry`
- On `submit_order()`, if the engine is paused but the DB shows `status = 'enabled'` (operator manually resumed), it calls `reset_pause()` to self-heal
- `with_threshold(n)` constructor sets a custom threshold; `::new()` defaults to 10

#### Wallet Worker Architecture

Each wallet gets its own `WalletWorker` task:
- Owns an `OrderManager` (primary wallet) or `OrderManagerMinimal` (secondary wallets)
- Owns a `DecisionEngine` with the configured threshold
- Receives orders from the gRPC `SubmitOrder` endpoint
- Sends status updates back via the streaming `StreamOrders` response

### 4.2 Python Strategy Engine

**Directory:** `python-strategy/`  
**HTTP port:** `8001` (FastAPI)  
**Metrics port:** `9092` (Prometheus)

#### Strategies

| Strategy | File | Signal Type |
|---|---|---|
| `SniperStrategy` | `strategies/sniper.py` | Buys new tokens within milliseconds of launch |
| `MomentumStrategy` | `strategies/momentum.py` | Rides tokens with positive price momentum |

Both strategies implement a common interface:
```python
async def analyze(token_event: TokenDiscoveredEvent) -> TradeSignal
```
A `TradeSignal` carries: `action` (BUY/SELL/HOLD), `confidence`, `amount_sol`, `token_mint`.

#### Strategy Engine Core (`strategy_engine.py`)

- `StrategyEngine` class orchestrates all strategies
- Reads wallet preset from DB on startup and every 12 processing cycles via `_refresh_preset_from_wallet_config()`
- Applies the active `PRESET` to all strategies immediately (no restart required)
- Connects to the Rust gRPC server; falls back to standalone mode if unavailable

#### Preset Parameter Mapping

```python
PRESETS = {
    "conservative": {"buy_amount_sol": 0.1, "stop_loss_pct": 0.10, "take_profit_pct": 0.30, "max_positions": 3},
    "balanced":     {"buy_amount_sol": 0.25, "stop_loss_pct": 0.15, "take_profit_pct": 0.50, "max_positions": 5},
    "aggressive":   {"buy_amount_sol": 0.5, "stop_loss_pct": 0.20, "take_profit_pct": 1.00, "max_positions": 10},
}
```

#### FastAPI Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/health` | None | Liveness check |
| `GET` | `/api/strategies` | None | List active strategies + metrics |
| `GET` | `/api/metrics` | None | Strategy performance metrics |
| `GET` | `/api/portfolio` | None | Current open positions |
| `POST` | `/api/order` | None | Manual order injection |
| `PUT` | `/api/strategy/preset` | `X-Admin-Key` | Change risk preset (persists to DB via Express) |
| `GET` | `/api/strategy/preset` | None | Get current active preset |

### 4.3 Node.js API Server

**Directory:** `artifacts/api-server/`  
**HTTP + WebSocket port:** `8080`  
**ORM:** Drizzle (PostgreSQL)

The Express API server is the management plane. It does **not** execute trades directly — it proxies commands to the Rust engine via gRPC, stores history in PostgreSQL, and streams real-time updates to the dashboard over WebSocket.

#### REST Routes

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/bot/status` | None | Engine connectivity, uptime, mode |
| `GET` | `/api/bot/trades` | None | Trade history from DB |
| `GET` | `/api/bot/portfolio` | None | Open positions |
| `GET` | `/api/bot/metrics` | None | PnL, win rate, counters |
| `GET` | `/api/bot/mev-stats` | None | MEV bundle stats |
| `GET` | `/api/settings/status` | None | Env var config check |
| `PUT` | `/api/settings` | `X-Admin-Key` | Update runtime config |
| `GET` | `/api/wallets` | None | All wallets with status |
| `GET` | `/api/wallets/:id/config` | None | Wallet risk config |
| `PUT` | `/api/wallets/:id/config` | `X-Admin-Key` | Update wallet config |
| `PUT` | `/api/wallets/:id/pause` | `X-Admin-Key` | Pause a wallet |
| `PUT` | `/api/wallets/:id/resume` | `X-Admin-Key` | Resume a paused wallet |
| `PUT` | `/api/wallets/:id/halt` | `X-Admin-Key` | Halt a wallet (hard stop) |
| `GET` | `/api/strategy/preset` | None | Get active preset |
| `PUT` | `/api/strategy/preset` | `X-Admin-Key` | Set strategy preset |

#### WebSocket (`/api/bot/stream`)

The server opens a **gRPC server-streaming** connection to the Rust engine (`StreamOrders`) on each WebSocket client connect. Rust order events are forwarded in real time to the browser.

#### gRPC Client

The API server uses a generated gRPC client (`src/lib/grpc-client.ts`) to call:
- `SubmitOrder` — place an order
- `StreamOrders` — receive live order status updates
- `GetPortfolio` — fetch open positions
- `GetMetrics` — fetch engine-level stats

### 4.4 React Management Dashboard

**Directory:** `artifacts/dashboard/`  
**Port:** `23183`  
**Build tool:** Vite 7 + React 18 + TypeScript  
**UI:** shadcn/ui + Tailwind CSS  
**State:** TanStack Query (React Query)

#### Pages

| Page | Route | Description |
|---|---|---|
| Dashboard | `/dashboard/` | Real-time PnL, live trade feed, MEV stats |
| Wallets | `/dashboard/wallets` | Wallet status, pause/resume/halt controls |
| Strategies | `/dashboard/strategies` | Per-wallet preset selector (Conservative / Balanced / Aggressive) |
| Settings | `/dashboard/settings` | Service URLs, env config, global preset, admin config |

#### Admin Actions (require admin key entry)

All state-changing actions require the operator to enter the `ADMIN_API_KEY` inline:
- Pause / Resume / Halt wallet
- Change strategy preset (both on Strategies page and Settings page)
- Update runtime configuration

The admin key is sent as `X-Admin-Key` HTTP header and is **never stored** in browser state after the request completes.

---

## 5. Data Flow

### Token Discovery → Trade Execution

```
1. DETECT
   Solana WebSocket → Rust PumpFunClient
   Event: new token mint + bonding curve address

2. BROADCAST
   Rust → Python gRPC stream (TokenDiscoveredEvent)
   Fields: mint, symbol, virtual_sol_reserves, virtual_token_reserves, creator

3. ANALYZE
   Python SniperStrategy / MomentumStrategy
   Output: TradeSignal { action: BUY, amount_sol: 0.25, confidence: 0.87 }

4. ROUTE
   Python → Rust gRPC SubmitOrder
   DecisionEngine checks: daily loss limit, paused state, sandwich risk

5. EXECUTE
   If Jito available → bundle + tip → Jito Block Engine → Solana
   Otherwise       → direct RPC transaction → Solana

6. CONFIRM
   Rust polls for transaction signature confirmation
   Updates order status: PENDING → CONFIRMED / FAILED

7. STREAM
   Rust gRPC StreamOrders → Node.js API Server → WebSocket → Dashboard

8. PERSIST
   Node.js writes confirmed trade to PostgreSQL (trades table)
```

### Operator Control Flow

```
Operator clicks "Pause Wallet" in Dashboard
  → Dashboard prompts for admin key (inline, not stored)
  → PUT /api/wallets/:id/pause  (X-Admin-Key header)
  → Express validates key, writes wallet_registry.status = 'paused'
  → Next order attempt in Rust hits DB check, rejects with "wallet paused"
```

---

## 6. Protocol & Port Reference

| Service | Port | Protocol | Purpose |
|---|---|---|---|
| React Dashboard | 23183 | HTTP | Operator UI |
| Node.js API Server | 8080 | HTTP / WebSocket | Management REST API + live feed |
| Rust gRPC Server | 50051 | gRPC (tonic) | Trade execution, order streaming |
| Rust Prometheus | 9091 | HTTP | Engine metrics scraping |
| Python FastAPI | 8001 | HTTP | Strategy REST API |
| Python Prometheus | 9092 | HTTP | Strategy metrics scraping |
| PostgreSQL | 5432 | TCP | Persistent storage |
| Solana RPC | 8899 | JSON-RPC/HTTP | Blockchain queries |
| Solana WebSocket | 8900 | WebSocket | Real-time on-chain events |
| Jito Block Engine | 443 | HTTPS | MEV bundle submission |

---

## 7. Database Schema

All tables are managed by **Drizzle ORM** (`lib/db/src/schema/`). The PostgreSQL database is provisioned by Replit and accessed via `DATABASE_URL`.

### `trades`

Stores every order that has been submitted.

| Column | Type | Description |
|---|---|---|
| `id` | `serial` PK | Auto-increment trade ID |
| `wallet_id` | `varchar` | Owning wallet identifier |
| `mint` | `varchar` | Solana token mint address |
| `side` | `varchar` | `'buy'` or `'sell'` |
| `amount_sol` | `numeric` | SOL amount |
| `pnl_sol` | `numeric` | Realized PnL (null until closed) |
| `signature` | `varchar` | Solana transaction signature |
| `status` | `varchar` | `pending`, `confirmed`, `failed` |
| `strategy` | `varchar` | Strategy that generated the signal |
| `created_at` | `timestamp` | Submission time |

### `strategies`

Performance tracking per strategy instance.

| Column | Type | Description |
|---|---|---|
| `id` | `serial` PK | |
| `name` | `varchar` | Strategy name (e.g. `sniper`, `momentum`) |
| `enabled` | `boolean` | Whether the strategy is active |
| `trades_executed` | `integer` | Lifetime trade count |
| `total_pnl_sol` | `numeric` | Lifetime PnL |
| `win_rate` | `numeric` | Win percentage |

### `wallet_registry`

Registry of all managed Solana wallets.

| Column | Type | Description |
|---|---|---|
| `id` | `varchar` PK | e.g. `wallet_001`, `wallet_002` |
| `pubkey` | `varchar` | Solana public key (base58) |
| `keypair_path` | `varchar` | Path to keypair file (**never returned in API responses**) |
| `status` | `varchar` | `enabled`, `paused`, `halted` |
| `label` | `varchar` | Human-readable name |
| `created_at` | `timestamp` | |

### `wallet_config`

Per-wallet risk parameters and strategy preset.

| Column | Type | Description |
|---|---|---|
| `wallet_id` | `varchar` FK | References `wallet_registry.id` |
| `risk_per_trade_sol` | `numeric` | Max SOL per trade |
| `daily_loss_limit_sol` | `numeric` | Daily drawdown limit |
| `max_positions` | `integer` | Max concurrent open positions |
| `strategy_preset` | `varchar` | `conservative`, `balanced`, or `aggressive` |
| `updated_at` | `timestamp` | Last config change |

### `wallet_alerts`

Audit log of auto-pause events.

| Column | Type | Description |
|---|---|---|
| `id` | `serial` PK | |
| `wallet_id` | `varchar` FK | Affected wallet |
| `reason` | `varchar` | Human-readable cause (e.g. `consecutive_rejects`) |
| `count` | `integer` | Number of consecutive rejects that triggered the pause |
| `created_at` | `timestamp` | When the pause was triggered |

### `bot_config`

Runtime configuration key-value store (overrides env vars without restart).

| Column | Type | Description |
|---|---|---|
| `key` | `varchar` PK | Config key (e.g. `SOLANA_RPC_URL`) |
| `value` | `text` | Current value |
| `updated_at` | `timestamp` | |

### `system_config`

Global system settings read once at engine startup.

| Column | Type | Description |
|---|---|---|
| `key` | `varchar` PK | Setting name (e.g. `auto_pause_threshold`) |
| `value` | `text` | Setting value |

---

## 8. Configuration System

Configuration follows a three-tier hierarchy (higher tier overrides lower):

```
Tier 1 (lowest)  — Compiled defaults (in Rust config.rs / Python settings.py)
Tier 2           — Environment variables (.env / Replit Secrets)
Tier 3 (highest) — bot_config DB table (operator-editable at runtime via dashboard)
```

### Key Environment Variables

| Variable | Component | Description |
|---|---|---|
| `DATABASE_URL` | All | PostgreSQL connection string |
| `SOLANA_RPC_URL` | Rust | Primary Solana RPC endpoint |
| `SOLANA_WS_URL` | Rust | Solana WebSocket endpoint |
| `WALLET_PRIVATE_KEY` | Rust | Base58 private key (dev/testing only) |
| `KEYPAIR_PATH` | Rust | Path to keypair JSON file (production) |
| `JITO_BUNDLE_URL` | Rust | Jito block engine URL (optional) |
| `JITO_TIP_LAMPORTS` | Rust | MEV tip amount (default: 10000) |
| `ADMIN_API_KEY` | Express + Python | Secret for admin-gated routes |
| `EXPRESS_API_URL` | Python | URL of the Express API (default: `http://localhost:8080`) |
| `PYTHON_STRATEGY_URL` | Node.js | URL of Python FastAPI (default: `http://localhost:8001`) |
| `PORT` | All | Service port (Replit-assigned per artifact) |

### Runtime Config Keys (bot_config table)

| Key | Description |
|---|---|
| `SOLANA_RPC_URL` | Override RPC without restart |
| `MAX_POSITION_SIZE_SOL` | Global position size cap |
| `DAILY_LOSS_LIMIT_SOL` | Global daily drawdown limit |
| `JITO_BUNDLE_URL` | Toggle MEV protection endpoint |

### System Config Keys (system_config table)

| Key | Default | Description |
|---|---|---|
| `auto_pause_threshold` | `10` | Consecutive order rejections before auto-pause |

---

## 9. Wallet Management & Auto-Pause

### Wallet States

```
 enabled ──► paused ──► enabled   (operator resumes)
    │
    └──► halted                    (hard stop — requires DB edit to recover)
```

| State | Trading | Resumable from UI |
|---|---|---|
| `enabled` | Yes | N/A |
| `paused` | No | Yes (admin key) |
| `halted` | No | No (manual DB intervention) |

### Auto-Pause Flow

The Rust `DecisionEngine` tracks consecutive order rejections in memory:

```
Order rejected by RPC/risk check
  → consecutive_rejects_count += 1
  → if count >= auto_pause_threshold:
      → take_needs_db_pause() returns true
      → OrderManager calls pause_wallet(pool, wallet_id, reason, count)
      → wallet_registry.status = 'paused'
      → wallet_alerts row inserted
      → DecisionEngine.paused = true
      → All subsequent orders immediately rejected
```

### Self-Healing Resume

If an operator resumes a wallet via the dashboard (`PUT /api/wallets/:id/resume`), the next order attempt in the Rust engine:

1. Detects in-memory `paused = true`
2. Queries `get_wallet_status()` from DB
3. If DB returns `enabled` → calls `decision_engine.reset_pause()`
4. Order proceeds normally

---

## 10. Strategy Preset System

Presets provide a one-click risk profile change without editing raw parameters.

| Preset | Position Size | Stop Loss | Take Profit | Max Positions |
|---|---|---|---|---|
| **Conservative** | 0.10 SOL | 10% | 30% | 3 |
| **Balanced** | 0.25 SOL | 15% | 50% | 5 |
| **Aggressive** | 0.50 SOL | 20% | 100% | 10 |

### Preset Change Flow

```
Operator selects preset in Dashboard (Strategies or Settings page)
  → Admin key prompt shown inline
  → PUT /api/strategy/preset  { preset, wallet_id }  (X-Admin-Key)
  → Express validates key
  → Writes wallet_config.strategy_preset to PostgreSQL
  → Calls PUT /api/wallets/:id/config on Express (same process, internal)
  → Notifies Python strategy engine: PUT http://python:8001/api/strategy/preset
  → Python applies new PRESET parameters to all active strategies immediately
  → Python also refreshes from DB every 12 processing cycles as safety net
```

No engine restarts are required for preset changes to take effect.

---

## 11. MEV Protection

### Jito Bundle Execution

When `JITO_BUNDLE_URL` is configured:

1. Orders are packaged into a **Jito bundle** (up to 5 transactions)
2. A **tip transaction** is appended (default: 10,000 lamports to a Jito tip account)
3. The bundle is submitted to the Jito block engine for priority inclusion
4. If the bundle fails or times out, the engine falls back to direct RPC execution

### Sandwich Attack Detection

The `SandwichDetector` (`src/mev/sandwich.rs`) scores each order before execution:

- Scans `MempoolMonitor` cache for suspicious transaction patterns around the target token
- Assigns a `risk_score` (0–100)
- If `risk_score > risk_threshold` → order is rejected with `OrderError::SandwichRiskTooHigh`

### RPC Failover

`RpcManager` (`src/rpc/mod.rs`) maintains a pool of Solana RPC endpoints:
- Round-robins across healthy endpoints
- Marks endpoints as degraded after errors
- Falls back transparently without order cancellation

---

## 12. Security Model

### Admin Key Enforcement

All state-mutating endpoints require `X-Admin-Key` matching `ADMIN_API_KEY` from environment:

| Layer | Protection |
|---|---|
| Express (`requireAdminKey` middleware) | All wallet pause/resume/halt/config routes + `PUT /strategy/preset` |
| Python FastAPI | `PUT /strategy/preset` checks `X-Admin-Key` header |
| Dashboard | Inline key prompt — key cleared from React state after each request |

### Keypair Security

- Wallet private keys are **never returned** in any API response
- The `keypair_path` field in `wallet_registry` is excluded from all API serialization
- Production wallets use `KEYPAIR_PATH` (file on disk) — pasting raw keys is explicitly discouraged in the UI
- Demo mode: if no key is configured, the Rust engine generates an **ephemeral keypair** at startup (trading disabled, logs a prominent warning)

### Network Boundaries

| Exposure | Visibility |
|---|---|
| Dashboard (:23183) | Public (Replit proxy) |
| API Server (:8080) | Public (Replit proxy, gated by admin key for mutations) |
| Python FastAPI (:8001) | Internal (loopback only in production) |
| Rust gRPC (:50051) | Internal (loopback only) |
| PostgreSQL (:5432) | Internal (Replit managed) |

---

## 13. Key File Reference

### Rust Engine (`rust-engine/`)

| File | Role |
|---|---|
| `src/main.rs` | Entry point: DB connect, config load, wallet resolution, gRPC server |
| `src/config.rs` | Config struct, env var parsing, DB override loading |
| `src/database.rs` | All PostgreSQL queries (Drizzle-compatible raw SQL) |
| `src/decision/mod.rs` | `DecisionEngine`: consecutive reject tracking, auto-pause logic |
| `src/order/manager.rs` | `OrderManager`: full order lifecycle for primary wallet |
| `src/order/minimal.rs` | `OrderManagerMinimal`: lighter version for secondary wallets |
| `src/wallet_worker.rs` | Per-wallet async task wrapper |
| `src/orchestrator.rs` | Multi-wallet coordination, HALT propagation |
| `src/grpc/mod.rs` | tonic gRPC service implementation |
| `src/pumpfun/mod.rs` | Pump.fun WebSocket listener, token event parsing |
| `src/pumpfun/bonding_curve.rs` | AMM math (buy/sell price calculation) |
| `src/mev/jito.rs` | Jito bundle construction and submission |
| `src/mev/sandwich.rs` | Sandwich attack risk scoring |
| `src/mev/mempool.rs` | Pending transaction cache |
| `src/rpc/mod.rs` | Multi-endpoint RPC pool with health checking |
| `src/metrics.rs` | Prometheus metrics definitions |
| `src/websocket.rs` | Solana WebSocket subscription management |
| `proto/trading.proto` | gRPC service and message definitions |

### Python Strategy Engine (`python-strategy/`)

| File | Role |
|---|---|
| `main.py` | FastAPI app factory, startup hooks |
| `strategy_engine.py` | `StrategyEngine`: orchestration, preset refresh, gRPC bridge |
| `strategies/sniper.py` | `SniperStrategy`: launch sniper logic |
| `strategies/momentum.py` | `MomentumStrategy`: momentum signal generation |
| `api/routes.py` | All FastAPI route handlers |
| `config.py` | `settings` object (Pydantic BaseSettings) |

### Node.js API Server (`artifacts/api-server/src/`)

| File | Role |
|---|---|
| `index.ts` | Express + WebSocket server bootstrap |
| `routes/bot.ts` | `/api/bot/*` endpoints |
| `routes/wallets.ts` | `/api/wallets/*` + `/api/strategy/preset` endpoints |
| `routes/settings.ts` | `/api/settings/*` endpoints |
| `middleware/auth.ts` | `requireAdminKey` middleware |
| `lib/grpc-client.ts` | Generated gRPC client wrapper |
| `db/schema.ts` | Drizzle schema definitions |

### React Dashboard (`artifacts/dashboard/src/`)

| File | Role |
|---|---|
| `App.tsx` | Router, layout, theme |
| `pages/Dashboard.tsx` | Main monitoring view |
| `pages/Wallets.tsx` | Wallet management (pause/resume/halt) |
| `pages/Strategies.tsx` | Per-wallet preset selector |
| `pages/Settings.tsx` | Global config, service URLs |

### Shared Library (`lib/db/src/`)

| File | Role |
|---|---|
| `schema/index.ts` | Drizzle table definitions (all services share this schema) |
| `migrations/` | SQL migration files |

---

## 14. Technology Stack

| Category | Technology | Version |
|---|---|---|
| **Execution Engine** | Rust | stable (2024 edition) |
| **Async Runtime** | Tokio | 1.x |
| **gRPC** | tonic + prost | 0.12.x |
| **Solana SDK** | solana-sdk | 1.18.x |
| **Strategy Engine** | Python | 3.11+ |
| **HTTP Framework** | FastAPI + Uvicorn | 0.110.x |
| **gRPC Client (Python)** | grpcio | 1.62.x |
| **HTTP Client (Python)** | aiohttp | 3.9.x |
| **API Server** | Node.js + Express | 20 LTS + 4.x |
| **ORM** | Drizzle ORM | 0.30.x |
| **gRPC Client (Node)** | @grpc/grpc-js | 1.10.x |
| **Database** | PostgreSQL | 15 (Replit managed) |
| **Frontend** | React + Vite | 18 + 7.x |
| **UI Components** | shadcn/ui + Tailwind | latest |
| **State Management** | TanStack Query | 5.x |
| **Metrics** | Prometheus | — |
| **Build System** | pnpm workspaces | 10.x |
| **Monorepo** | pnpm workspace | — |

---

*Generated from live codebase — April 2026*
