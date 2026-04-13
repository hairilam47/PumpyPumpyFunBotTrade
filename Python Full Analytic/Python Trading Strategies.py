# strategies/sniper.py
import asyncio
from typing import Dict, Optional, Set
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import logging

from .base import BaseStrategy, StrategyConfig, Signal
from analytics.ml_signals import TradingSignal

logger = logging.getLogger(__name__)

@dataclass
class SniperConfig(StrategyConfig):
    """Configuration for sniper strategy."""
    min_liquidity_sol: float = 5.0
    max_creator_rug_risk: float = 0.3
    required_social_presence: bool = True
    buy_amount_sol: float = 0.1
    take_profit_pct: float = 50.0
    stop_loss_pct: float = 20.0
    trailing_stop_pct: float = 10.0
    max_slippage_bps: int = 300
    blacklisted_creators: Set[str] = field(default_factory=set)
    whitelisted_creators: Set[str] = field(default_factory=set)

class PumpFunSniper(BaseStrategy):
    """Sniper strategy for new token launches."""
    
    def __init__(self, config: SniperConfig, **kwargs):
        super().__init__(config, **kwargs)
        self.config: SniperConfig = config
        self.active_snipes: Dict[str, SnipePosition] = {}
        
    async def on_new_token(self, token_data: dict) -> Optional[Signal]:
        """Called when a new token is detected."""
        mint = token_data.get("mint")
        
        # Check if we should snipe this token
        if not await self.should_snipe(token_data):
            return None
        
        logger.info(f"Sniping token: {token_data.get('symbol', mint)} ({mint})")
        
        # Calculate buy amount
        buy_amount_lamports = int(self.config.buy_amount_sol * 1e9)
        max_cost = int(buy_amount_lamports * (1 + self.config.max_slippage_bps / 10000))
        
        # Create buy signal
        signal = Signal(
            mint=mint,
            action="BUY",
            amount=buy_amount_lamports,
            max_cost=max_cost,
            slippage_bps=self.config.max_slippage_bps,
            strategy=self.config.name,
            metadata={
                "entry_price": token_data.get("initial_price", 0),
                "snipe_type": "new_launch",
                "liquidity": token_data.get("initial_liquidity", 0),
            }
        )
        
        # Track position
        self.active_snipes[mint] = SnipePosition(
            mint=mint,
            entry_price=token_data.get("initial_price", 0),
            amount_sol=self.config.buy_amount_sol,
            entry_time=datetime.now(),
            take_profit_price=token_data.get("initial_price", 0) * (1 + self.config.take_profit_pct / 100),
            stop_loss_price=token_data.get("initial_price", 0) * (1 - self.config.stop_loss_pct / 100),
            highest_price=token_data.get("initial_price", 0),
        )
        
        return signal
    
    async def should_snipe(self, token_data: dict) -> bool:
        """Determine if we should snipe this token."""
        mint = token_data.get("mint")
        creator = token_data.get("traderPublicKey", "")
        
        # Check blacklist
        if creator in self.config.blacklisted_creators:
            logger.debug(f"Creator {creator} is blacklisted")
            return False
        
        # Check whitelist (if any)
        if self.config.whitelisted_creators and creator not in self.config.whitelisted_creators:
            logger.debug(f"Creator {creator} not in whitelist")
            return False
        
        # Check liquidity
        liquidity = token_data.get("initial_liquidity", 0)
        if liquidity < self.config.min_liquidity_sol:
            logger.debug(f"Insufficient liquidity: {liquidity} SOL")
            return False
        
        # Check creator history
        creator_risk = await self.get_creator_risk(creator)
        if creator_risk > self.config.max_creator_rug_risk:
            logger.debug(f"Creator risk too high: {creator_risk}")
            return False
        
        # Check social presence
        if self.config.required_social_presence:
            has_social = await self.check_social_presence(token_data)
            if not has_social:
                logger.debug(f"No social presence detected")
                return False
        
        # Check token name/symbol for obvious scams
        name = token_data.get("name", "").lower()
        symbol = token_data.get("symbol", "").lower()
        
        scam_keywords = ["scam", "honeypot", "rug", "test", "fake"]
        for keyword in scam_keywords:
            if keyword in name or keyword in symbol:
                logger.debug(f"Scam keyword '{keyword}' detected")
                return False
        
        return True
    
    async def get_creator_risk(self, creator: str) -> float:
        """Get creator's rugpull risk score."""
        # Check cache
        cache_key = f"creator_risk:{creator}"
        cached = await self.redis.get(cache_key)
        if cached:
            return float(cached)
        
        # Query database for creator history
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    COUNT(*) as total_tokens,
                    COUNT(CASE WHEN rugpulled THEN 1 END) as rugpulls,
                    AVG(CASE WHEN migrated_to_raydium THEN 1 ELSE 0 END) as success_rate
                FROM tokens
                WHERE creator = $1
            """, creator)
            
            if row and row['total_tokens'] > 0:
                risk = row['rugpulls'] / row['total_tokens']
            else:
                risk = 0.5  # Unknown creator, medium risk
        
        # Cache for 1 hour
        await self.redis.setex(cache_key, 3600, str(risk))
        
        return risk
    
    async def check_social_presence(self, token_data: dict) -> bool:
        """Check if token has social media presence."""
        # Check if token has Twitter/Telegram/Discord links
        social_links = token_data.get("social_links", {})
        
        has_twitter = bool(social_links.get("twitter"))
        has_telegram = bool(social_links.get("telegram"))
        has_discord = bool(social_links.get("discord"))
        
        # At least one social link required
        return has_twitter or has_telegram or has_discord
    
    async def on_price_update(self, mint: str, price: float, metrics: dict) -> Optional[Signal]:
        """Called on price updates for active snipes."""
        if mint not in self.active_snipes:
            return None
        
        position = self.active_snipes[mint]
        
        # Update highest price
        if price > position.highest_price:
            position.highest_price = price
        
        # Calculate current PnL
        pnl_pct = (price - position.entry_price) / position.entry_price * 100
        
        # Check take profit
        if price >= position.take_profit_price:
            logger.info(f"Take profit triggered for {mint}: {pnl_pct:.1f}%")
            return self.create_exit_signal(mint, price, "TAKE_PROFIT")
        
        # Check stop loss
        if price <= position.stop_loss_price:
            logger.info(f"Stop loss triggered for {mint}: {pnl_pct:.1f}%")
            return self.create_exit_signal(mint, price, "STOP_LOSS")
        
        # Check trailing stop
        if position.highest_price > position.entry_price * 1.2:  # Only trail after 20% gain
            trailing_stop = position.highest_price * (1 - self.config.trailing_stop_pct / 100)
            if price <= trailing_stop:
                logger.info(f"Trailing stop triggered for {mint}: {pnl_pct:.1f}%")
                return self.create_exit_signal(mint, price, "TRAILING_STOP")
        
        # Check for rugpull signals
        rugpull_risk = metrics.get("rugpull_probability", 0)
        if rugpull_risk > 0.7:
            logger.warning(f"High rugpull risk detected for {mint}: {rugpull_risk}")
            return self.create_exit_signal(mint, price, "RUGPULL_RISK")
        
        return None
    
    def create_exit_signal(self, mint: str, price: float, reason: str) -> Signal:
        """Create exit signal."""
        position = self.active_snipes[mint]
        
        # Calculate minimum output with slippage
        expected_output = price * position.amount_sol
        min_output = int(expected_output * (1 - self.config.max_slippage_bps / 10000) * 1e9)
        
        signal = Signal(
            mint=mint,
            action="SELL",
            amount=int(position.amount_sol * 1e9),  # Sell entire position
            min_output=min_output,
            slippage_bps=self.config.max_slippage_bps,
            strategy=self.config.name,
            metadata={
                "entry_price": position.entry_price,
                "exit_price": price,
                "pnl_pct": (price - position.entry_price) / position.entry_price * 100,
                "exit_reason": reason,
                "hold_time_seconds": (datetime.now() - position.entry_time).total_seconds(),
            }
        )
        
        # Remove from active snipes
        del self.active_snipes[mint]
        
        return signal
    
    async def on_trade_complete(self, mint: str, trade_result: dict):
        """Called when a trade completes."""
        logger.info(f"Snipe completed for {mint}: {trade_result}")
        
        # Update statistics
        await self.update_stats(trade_result)
    
    async def update_stats(self, trade_result: dict):
        """Update strategy statistics."""
        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO strategy_stats (
                    strategy_name, mint, entry_price, exit_price,
                    pnl_sol, pnl_pct, exit_reason, hold_time_seconds
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
                self.config.name,
                trade_result.get("mint"),
                trade_result.get("entry_price"),
                trade_result.get("exit_price"),
                trade_result.get("pnl_sol"),
                trade_result.get("pnl_pct"),
                trade_result.get("exit_reason"),
                trade_result.get("hold_time_seconds")
            )

@dataclass
class SnipePosition:
    """Active snipe position."""
    mint: str
    entry_price: float
    amount_sol: float
    entry_time: datetime
    take_profit_price: float
    stop_loss_price: float
    highest_price: float

# strategies/momentum.py
class MomentumTrader(BaseStrategy):
    """Momentum-based trading strategy."""
    
    def __init__(self, config: dict, **kwargs):
        super().__init__(config, **kwargs)
        self.lookback_periods = config.get("lookback_periods", 20)
        self.momentum_threshold = config.get("momentum_threshold", 70)
        self.volume_multiplier = config.get("volume_multiplier", 2.0)
        self.active_positions: Dict[str, MomentumPosition] = {}
    
    async def on_signal(self, ml_signal: TradingSignal) -> Optional[Signal]:
        """Process ML signal."""
        mint = ml_signal.mint
        
        # Check if we already have a position
        if mint in self.active_positions:
            return await self.handle_existing_position(mint, ml_signal)
        
        # Check for entry conditions
        if ml_signal.action == "BUY" and ml_signal.confidence > 0.7:
            # Additional momentum checks
            momentum_score = ml_signal.features_used.get("momentum_score", 0)
            if momentum_score < self.momentum_threshold:
                return None
            
            # Create entry signal
            return self.create_entry_signal(mint, ml_signal)
        
        return None
    
    def create_entry_signal(self, mint: str, ml_signal: TradingSignal) -> Signal:
        """Create momentum entry signal."""
        position_size = ml_signal.position_size_pct * self.config.max_trade_size_sol
        amount_lamports = int(position_size * 1e9)
        
        self.active_positions[mint] = MomentumPosition(
            mint=mint,
            entry_price=ml_signal.price_prediction or 0,
            amount_sol=position_size,
            entry_time=datetime.now(),
            momentum_score=ml_signal.features_used.get("momentum_score", 0),
        )
        
        return Signal(
            mint=mint,
            action="BUY",
            amount=amount_lamports,
            max_cost=int(amount_lamports * 1.1),  # 10% slippage
            slippage_bps=100,
            strategy=self.config.name,
            metadata={"momentum_score": ml_signal.features_used.get("momentum_score", 0)}
        )
    
    async def handle_existing_position(self, mint: str, ml_signal: TradingSignal) -> Optional[Signal]:
        """Handle existing momentum position."""
        position = self.active_positions[mint]
        
        # Check exit conditions
        if ml_signal.action == "SELL" and ml_signal.confidence > 0.6:
            return self.create_exit_signal(mint, ml_signal, "SIGNAL_SELL")
        
        # Check momentum decay
        current_momentum = ml_signal.features_used.get("momentum_score", 0)
        momentum_decay = current_momentum - position.momentum_score
        
        if momentum_decay < -20:  # Significant momentum loss
            return self.create_exit_signal(mint, ml_signal, "MOMENTUM_DECAY")
        
        # Update position momentum
        position.momentum_score = current_momentum
        
        return None

@dataclass
class MomentumPosition:
    """Active momentum position."""
    mint: str
    entry_price: float
    amount_sol: float
    entry_time: datetime
    momentum_score: float

# strategies/base.py
from abc import ABC, abstractmethod
from typing import Dict, Optional, Any
from dataclasses import dataclass
import asyncpg
import redis.asyncio as redis

@dataclass
class StrategyConfig:
    """Base strategy configuration."""
    name: str
    enabled: bool = True
    min_trade_size_sol: float = 0.01
    max_trade_size_sol: float = 10.0
    max_positions: int = 5
    max_daily_trades: int = 50

@dataclass
class Signal:
    """Trading signal."""
    mint: str
    action: str  # BUY, SELL, HOLD
    amount: int  # in lamports/tokens
    max_cost: Optional[int] = None
    min_output: Optional[int] = None
    slippage_bps: int = 100
    strategy: str = ""
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

class BaseStrategy(ABC):
    """Base class for all trading strategies."""
    
    def __init__(self, config: StrategyConfig, db_pool: asyncpg.Pool = None, redis_client: redis.Redis = None):
        self.config = config
        self.db = db_pool
        self.redis = redis_client
        self.daily_trades = 0
        self.last_reset = datetime.now().date()
    
    @abstractmethod
    async def on_new_token(self, token_data: dict) -> Optional[Signal]:
        """Called when a new token is detected."""
        pass
    
    @abstractmethod
    async def on_price_update(self, mint: str, price: float, metrics: dict) -> Optional[Signal]:
        """Called on price updates."""
        pass
    
    @abstractmethod
    async def on_signal(self, ml_signal: Any) -> Optional[Signal]:
        """Called when ML signal is generated."""
        pass
    
    async def on_trade_complete(self, mint: str, trade_result: dict):
        """Called when a trade completes."""
        pass
    
    def can_trade(self) -> bool:
        """Check if strategy can place more trades."""
        today = datetime.now().date()
        if today != self.last_reset:
            self.daily_trades = 0
            self.last_reset = today
        
        return (
            self.config.enabled and
            self.daily_trades < self.config.max_daily_trades
        )
    
    def increment_trade_count(self):
        """Increment daily trade count."""
        self.daily_trades += 1