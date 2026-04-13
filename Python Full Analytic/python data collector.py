# analytics/data_collector.py
import asyncio
import asyncpg
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Set, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import aiohttp
import websockets
import json
import logging
from collections import deque

logger = logging.getLogger(__name__)

@dataclass
class TokenMetrics:
    """Real-time token metrics."""
    mint: str
    timestamp: datetime
    price: float
    volume_1h: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    market_cap: float = 0.0
    holder_count: int = 0
    buy_count_1h: int = 0
    sell_count_1h: int = 0
    unique_buyers_1h: int = 0
    unique_sellers_1h: int = 0
    whale_transactions_1h: int = 0
    social_score: float = 0.0
    momentum_score: float = 0.0
    risk_score: float = 0.0
    volatility: float = 0.0

@dataclass
class Trade:
    """Individual trade data."""
    signature: str
    mint: str
    timestamp: datetime
    is_buy: bool
    token_amount: float
    sol_amount: float
    price: float
    trader: str
    program_id: str

class PumpFunDataCollector:
    """Collects and processes Pump.fun market data."""
    
    def __init__(self, db_pool: asyncpg.Pool, redis_client):
        self.db = db_pool
        self.redis = redis_client
        self.ws_connections: Dict[str, websockets.WebSocketClientProtocol] = {}
        self.monitored_tokens: Set[str] = set()
        self.metrics_buffer: Dict[str, deque] = {}
        self.trade_buffer: Dict[str, deque] = {}
        self.max_buffer_size = 1000
        
    async def start_collection(self):
        """Start all data collection tasks."""
        tasks = [
            self.collect_new_tokens(),
            self.collect_price_updates(),
            self.collect_trade_history(),
            self.collect_holder_data(),
            self.collect_social_signals(),
            self.process_metrics_buffer(),
        ]
        await asyncio.gather(*tasks)
    
    async def collect_new_tokens(self):
        """Monitor WebSocket for new token launches."""
        retry_count = 0
        max_retries = 5
        
        while retry_count < max_retries:
            try:
                async with websockets.connect(
                    "wss://pumpportal.fun/api/data",
                    ping_interval=20,
                    ping_timeout=10
                ) as ws:
                    # Subscribe to new token events
                    await ws.send(json.dumps({
                        "method": "subscribeNewToken",
                    }))
                    
                    logger.info("Connected to Pump.fun WebSocket")
                    retry_count = 0  # Reset on successful connection
                    
                    async for message in ws:
                        data = json.loads(message)
                        if "mint" in data:
                            await self.process_new_token(data)
                            
            except Exception as e:
                retry_count += 1
                logger.error(f"WebSocket error (attempt {retry_count}/{max_retries}): {e}")
                await asyncio.sleep(2 ** retry_count)  # Exponential backoff
    
    async def process_new_token(self, token_data: dict):
        """Process newly launched token."""
        mint = token_data["mint"]
        
        # Add to monitored set
        self.monitored_tokens.add(mint)
        
        # Initialize buffers
        self.metrics_buffer[mint] = deque(maxlen=self.max_buffer_size)
        self.trade_buffer[mint] = deque(maxlen=1000)
        
        # Cache in Redis with 1 hour TTL
        await self.redis.setex(
            f"token:{mint}:info",
            3600,
            json.dumps(token_data)
        )
        
        # Store in database
        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO tokens (mint, name, symbol, creator, launch_time, initial_price, initial_liquidity)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (mint) DO UPDATE SET
                    name = EXCLUDED.name,
                    symbol = EXCLUDED.symbol,
                    initial_price = EXCLUDED.initial_price,
                    initial_liquidity = EXCLUDED.initial_liquidity
            """,
                mint,
                token_data.get("name", ""),
                token_data.get("symbol", ""),
                token_data.get("traderPublicKey", ""),
                datetime.now(),
                token_data.get("initialBuy", 0) / 1e9,
                token_data.get("vTokensInBondingCurve", 0) / 1e6
            )
        
        logger.info(f"New token detected: {token_data.get('symbol', mint)} ({mint})")
    
    async def collect_price_updates(self):
        """Collect real-time price updates for monitored tokens."""
        while True:
            for mint in list(self.monitored_tokens)[:100]:  # Limit batch size
                try:
                    metrics = await self.fetch_token_metrics(mint)
                    
                    if metrics:
                        self.metrics_buffer[mint].append(metrics)
                        
                        # Update Redis cache
                        await self.redis.setex(
                            f"token:{mint}:metrics",
                            60,  # 1 minute TTL
                            json.dumps(metrics.__dict__, default=str)
                        )
                        
                        # Calculate derived metrics
                        if len(self.metrics_buffer[mint]) >= 10:
                            await self.calculate_derived_metrics(mint)
                        
                except Exception as e:
                    logger.error(f"Error collecting metrics for {mint}: {e}")
            
            await asyncio.sleep(1)  # Update every second
    
    async def fetch_token_metrics(self, mint: str) -> Optional[TokenMetrics]:
        """Fetch current metrics for a token."""
        try:
            # Get bonding curve state from RPC
            # This would call the gRPC service
            info = await self.get_token_info(mint)
            
            return TokenMetrics(
                mint=mint,
                timestamp=datetime.now(),
                price=info.get("price", 0),
                liquidity=info.get("liquidity", 0),
                market_cap=info.get("market_cap", 0),
                holder_count=info.get("holder_count", 0),
            )
        except Exception as e:
            logger.debug(f"Failed to fetch metrics for {mint}: {e}")
            return None
    
    async def calculate_derived_metrics(self, mint: str):
        """Calculate derived metrics from buffer."""
        buffer = list(self.metrics_buffer[mint])
        if len(buffer) < 10:
            return
        
        df = pd.DataFrame([m.__dict__ for m in buffer])
        
        # Calculate volatility (standard deviation of returns)
        df['returns'] = df['price'].pct_change()
        volatility = df['returns'].std() * np.sqrt(365 * 24 * 60 * 60)
        
        # Calculate momentum score
        momentum_score = self.calculate_momentum_score(df)
        
        # Calculate risk score
        risk_score = self.calculate_risk_score(df)
        
        # Update latest metrics
        latest = buffer[-1]
        latest.volatility = volatility
        latest.momentum_score = momentum_score
        latest.risk_score = risk_score
        
        # Store in database periodically
        if len(buffer) % 60 == 0:  # Every minute
            await self.store_metrics(latest)
    
    def calculate_momentum_score(self, df: pd.DataFrame) -> float:
        """Calculate momentum score (0-100)."""
        if len(df) < 10:
            return 50.0
        
        score = 0.0
        
        # Price momentum (40% weight)
        if len(df) >= 10:
            price_change = (df['price'].iloc[-1] - df['price'].iloc[-10]) / df['price'].iloc[-10]
            price_score = min(100, max(0, 50 + price_change * 1000))
            score += price_score * 0.4
        
        # Volume momentum (30% weight)
        if 'volume_1h' in df.columns:
            recent_volume = df['volume_1h'].iloc[-5:].mean()
            older_volume = df['volume_1h'].iloc[-10:-5].mean()
            if older_volume > 0:
                volume_ratio = recent_volume / older_volume
                volume_score = min(100, max(0, volume_ratio * 50))
                score += volume_score * 0.3
        
        # Price acceleration (30% weight)
        if len(df) >= 20:
            short_ma = df['price'].rolling(5).mean()
            long_ma = df['price'].rolling(20).mean()
            if long_ma.iloc[-1] > 0:
                ma_diff = (short_ma.iloc[-1] - long_ma.iloc[-1]) / long_ma.iloc[-1]
                accel_score = min(100, max(0, 50 + ma_diff * 1000))
                score += accel_score * 0.3
        
        return score
    
    def calculate_risk_score(self, df: pd.DataFrame) -> float:
        """Calculate risk score (0-100, higher = riskier)."""
        risk = 0.0
        
        # Volatility risk (30%)
        returns = df['price'].pct_change().dropna()
        if len(returns) > 0:
            volatility = returns.std() * np.sqrt(365 * 24 * 60 * 60)
            risk += min(30, volatility * 100)
        
        # Liquidity risk (30%)
        if 'liquidity' in df.columns and 'market_cap' in df.columns:
            liquidity_ratio = df['liquidity'].iloc[-1] / max(df['market_cap'].iloc[-1], 1)
            if liquidity_ratio < 0.05:
                risk += 30
            elif liquidity_ratio < 0.1:
                risk += 20
            elif liquidity_ratio < 0.2:
                risk += 10
        
        # Holder concentration (20%)
        if 'holder_count' in df.columns:
            if df['holder_count'].iloc[-1] < 50:
                risk += 20
            elif df['holder_count'].iloc[-1] < 100:
                risk += 10
        
        # Whale activity (20%)
        if 'whale_transactions_1h' in df.columns:
            whale_ratio = df['whale_transactions_1h'].iloc[-1] / max(df['volume_1h'].iloc[-1], 1)
            risk += min(20, whale_ratio * 100)
        
        return min(100, risk)
    
    async def store_metrics(self, metrics: TokenMetrics):
        """Store metrics in database."""
        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO token_metrics (
                    mint, timestamp, price, volume_1h, volume_24h,
                    liquidity, market_cap, holder_count,
                    momentum_score, risk_score, social_score, volatility
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
                metrics.mint,
                metrics.timestamp,
                metrics.price,
                metrics.volume_1h,
                metrics.volume_24h,
                metrics.liquidity,
                metrics.market_cap,
                metrics.holder_count,
                metrics.momentum_score,
                metrics.risk_score,
                metrics.social_score,
                metrics.volatility
            )
    
    async def collect_trade_history(self):
        """Collect trade history for monitored tokens."""
        while True:
            for mint in list(self.monitored_tokens):
                try:
                    trades = await self.fetch_recent_trades(mint)
                    
                    for trade in trades:
                        self.trade_buffer[mint].append(trade)
                        
                        # Update volume metrics
                        await self.update_volume_metrics(mint, trade)
                        
                except Exception as e:
                    logger.error(f"Error collecting trades for {mint}: {e}")
            
            await asyncio.sleep(5)  # Update every 5 seconds
    
    async def fetch_recent_trades(self, mint: str, limit: int = 100) -> List[Trade]:
        """Fetch recent trades for a token."""
        # This would query the blockchain or a database
        # Placeholder implementation
        return []
    
    async def update_volume_metrics(self, mint: str, trade: Trade):
        """Update rolling volume metrics."""
        key = f"token:{mint}:volume"
        
        # Add to Redis sorted set with timestamp as score
        await self.redis.zadd(
            key,
            {json.dumps(trade.__dict__, default=str): trade.timestamp.timestamp()}
        )
        
        # Remove old entries (older than 24 hours)
        cutoff = (datetime.now() - timedelta(hours=24)).timestamp()
        await self.redis.zremrangebyscore(key, 0, cutoff)
        
        # Calculate volume metrics
        now = datetime.now().timestamp()
        hour_ago = now - 3600
        
        volume_1h = await self.calculate_volume(mint, hour_ago, now)
        volume_24h = await self.calculate_volume(mint, cutoff, now)
        
        # Update in buffer if exists
        if mint in self.metrics_buffer and self.metrics_buffer[mint]:
            latest = self.metrics_buffer[mint][-1]
            latest.volume_1h = volume_1h
            latest.volume_24h = volume_24h
    
    async def calculate_volume(self, mint: str, start: float, end: float) -> float:
        """Calculate volume between timestamps."""
        key = f"token:{mint}:volume"
        trades = await self.redis.zrangebyscore(key, start, end)
        
        volume = 0.0
        for trade_json in trades:
            trade = json.loads(trade_json)
            volume += trade.get("sol_amount", 0)
        
        return volume
    
    async def collect_holder_data(self):
        """Collect holder distribution data."""
        while True:
            for mint in list(self.monitored_tokens):
                try:
                    holders = await self.fetch_holder_data(mint)
                    
                    if holders:
                        # Cache holder data
                        await self.redis.setex(
                            f"token:{mint}:holders",
                            300,  # 5 minutes TTL
                            json.dumps(holders)
                        )
                        
                        # Update holder count in buffer
                        if mint in self.metrics_buffer and self.metrics_buffer[mint]:
                            self.metrics_buffer[mint][-1].holder_count = holders["total_holders"]
                            
                except Exception as e:
                    logger.error(f"Error collecting holder data for {mint}: {e}")
            
            await asyncio.sleep(60)  # Update every minute
    
    async def fetch_holder_data(self, mint: str) -> Optional[Dict]:
        """Fetch holder distribution data."""
        # This would query Solana RPC for token accounts
        # Placeholder implementation
        return {"total_holders": 0, "top_10_percentage": 0}
    
    async def collect_social_signals(self):
        """Monitor social media signals."""
        async with aiohttp.ClientSession() as session:
            while True:
                for mint in list(self.monitored_tokens):
                    try:
                        social_score = 0.0
                        
                        # Check Twitter mentions
                        twitter_score = await self.check_twitter_mentions(session, mint)
                        social_score += twitter_score * 0.5
                        
                        # Check Telegram activity
                        telegram_score = await self.check_telegram_activity(session, mint)
                        social_score += telegram_score * 0.3
                        
                        # Check Discord presence
                        discord_score = await self.check_discord_mentions(session, mint)
                        social_score += discord_score * 0.2
                        
                        # Update in buffer
                        if mint in self.metrics_buffer and self.metrics_buffer[mint]:
                            self.metrics_buffer[mint][-1].social_score = social_score
                            
                    except Exception as e:
                        logger.error(f"Error collecting social signals for {mint}: {e}")
                
                await asyncio.sleep(60)  # Check every minute
    
    async def check_twitter_mentions(self, session: aiohttp.ClientSession, mint: str) -> float:
        """Check Twitter/X mentions."""
        # Integration with Twitter API
        return 0.0
    
    async def check_telegram_activity(self, session: aiohttp.ClientSession, mint: str) -> float:
        """Check Telegram group activity."""
        return 0.0
    
    async def check_discord_mentions(self, session: aiohttp.ClientSession, mint: str) -> float:
        """Check Discord mentions."""
        return 0.0
    
    async def process_metrics_buffer(self):
        """Process and flush metrics buffer periodically."""
        while True:
            await asyncio.sleep(60)  # Every minute
            
            for mint, buffer in self.metrics_buffer.items():
                if len(buffer) >= 60:
                    # Calculate minute-level aggregates
                    minute_data = list(buffer)[-60:]
                    df = pd.DataFrame([m.__dict__ for m in minute_data])
                    
                    aggregates = {
                        "mint": mint,
                        "timestamp": datetime.now(),
                        "price_open": df['price'].iloc[0],
                        "price_high": df['price'].max(),
                        "price_low": df['price'].min(),
                        "price_close": df['price'].iloc[-1],
                        "volume": df['volume_1h'].iloc[-1] - df['volume_1h'].iloc[0],
                        "avg_momentum": df['momentum_score'].mean(),
                        "avg_risk": df['risk_score'].mean(),
                    }
                    
                    # Store minute aggregates
                    await self.store_minute_aggregates(aggregates)
    
    async def store_minute_aggregates(self, aggregates: Dict):
        """Store minute-level aggregates."""
        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO token_metrics_1m (
                    mint, timestamp, price_open, price_high, price_low, price_close,
                    volume, avg_momentum, avg_risk
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
                aggregates["mint"],
                aggregates["timestamp"],
                aggregates["price_open"],
                aggregates["price_high"],
                aggregates["price_low"],
                aggregates["price_close"],
                aggregates["volume"],
                aggregates["avg_momentum"],
                aggregates["avg_risk"]
            )
    
    async def get_token_info(self, mint: str) -> Dict:
        """Get token info from gRPC service."""
        # This would call the Rust/Go gRPC service
        # Placeholder implementation
        return {
            "price": 0.0,
            "liquidity": 0.0,
            "market_cap": 0.0,
            "holder_count": 0,
        }