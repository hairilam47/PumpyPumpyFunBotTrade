# tests/test_strategies.py
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from strategies.sniper import PumpFunSniper, SniperConfig
from strategies.momentum import MomentumTrader
from analytics.ml_signals import MLSignalGenerator, TradingSignal

@pytest.fixture
def mock_db_pool():
    return AsyncMock()

@pytest.fixture
def mock_redis():
    return AsyncMock()

@pytest.fixture
def mock_grpc_client():
    return AsyncMock()

@pytest.fixture
def sniper_config():
    return SniperConfig(
        name="test_sniper",
        min_liquidity_sol=5.0,
        buy_amount_sol=0.1,
        take_profit_pct=50.0,
        stop_loss_pct=20.0
    )

@pytest.fixture
def sniper(sniper_config, mock_db_pool, mock_redis, mock_grpc_client):
    return PumpFunSniper(
        config=sniper_config,
        db_pool=mock_db_pool,
        redis_client=mock_redis,
        grpc_client=mock_grpc_client
    )

class TestPumpFunSniper:
    
    @pytest.mark.asyncio
    async def test_should_snipe_valid_token(self, sniper):
        token_data = {
            "mint": "test_mint",
            "name": "Test Token",
            "symbol": "TEST",
            "traderPublicKey": "good_creator",
            "initial_liquidity": 10.0,
            "initial_price": 0.000001
        }
        
        # Mock creator risk check
        sniper.get_creator_risk = AsyncMock(return_value=0.1)
        sniper.check_social_presence = AsyncMock(return_value=True)
        
        result = await sniper.should_snipe(token_data)
        assert result == True
    
    @pytest.mark.asyncio
    async def test_should_not_snipe_low_liquidity(self, sniper):
        token_data = {
            "mint": "test_mint",
            "initial_liquidity": 1.0,  # Below minimum
        }
        
        result = await sniper.should_snipe(token_data)
        assert result == False
    
    @pytest.mark.asyncio
    async def test_should_not_snipe_high_risk_creator(self, sniper):
        token_data = {
            "mint": "test_mint",
            "traderPublicKey": "bad_creator",
            "initial_liquidity": 10.0,
        }
        
        sniper.get_creator_risk = AsyncMock(return_value=0.8)  # High risk
        
        result = await sniper.should_snipe(token_data)
        assert result == False
    
    @pytest.mark.asyncio
    async def test_take_profit_triggered(self, sniper):
        mint = "test_mint"
        
        # Setup active position
        sniper.active_snipes[mint] = SnipePosition(
            mint=mint,
            entry_price=0.000001,
            amount_sol=0.1,
            entry_time=datetime.now(),
            take_profit_price=0.0000015,  # 50% profit
            stop_loss_price=0.0000008,    # 20% loss
            highest_price=0.000001
        )
        
        # Price update that triggers take profit
        signal = await sniper.on_price_update(mint, 0.0000016, {})
        
        assert signal is not None
        assert signal.action == "SELL"
        assert signal.metadata["exit_reason"] == "TAKE_PROFIT"
    
    @pytest.mark.asyncio
    async def test_stop_loss_triggered(self, sniper):
        mint = "test_mint"
        
        sniper.active_snipes[mint] = SnipePosition(
            mint=mint,
            entry_price=0.000001,
            amount_sol=0.1,
            entry_time=datetime.now(),
            take_profit_price=0.0000015,
            stop_loss_price=0.0000008,
            highest_price=0.000001
        )
        
        # Price update that triggers stop loss
        signal = await sniper.on_price_update(mint, 0.0000007, {})
        
        assert signal is not None
        assert signal.action == "SELL"
        assert signal.metadata["exit_reason"] == "STOP_LOSS"

class TestMLSignalGenerator:
    
    @pytest.fixture
    def ml_generator(self, mock_db_pool, mock_redis):
        return MLSignalGenerator(mock_db_pool, mock_redis)
    
    @pytest.mark.asyncio
    async def test_feature_engineering(self, ml_generator):
        # Create test DataFrame
        dates = pd.date_range('2024-01-01', periods=100, freq='1s')
        df = pd.DataFrame({
            'price': np.random.randn(100).cumsum() + 0.000001,
            'volume_1h': np.random.randn(100) * 100 + 1000,
            'momentum_score': np.random.uniform(0, 100, 100),
            'liquidity': np.random.randn(100) * 100 + 5000,
            'market_cap': np.random.randn(100) * 10000 + 100000,
            'holder_count': np.cumsum(np.random.randint(1, 5, 100)),
        })
        
        features = ml_generator.engineer_features(df)
        
        assert features is not None
        assert len(features) > 0
    
    def test_calculate_rsi(self, ml_generator):
        prices = np.array([100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 110, 108, 107, 109])
        rsi = ml_generator._calculate_rsi(prices)
        
        assert 0 <= rsi <= 100
    
    def test_calculate_bb_position(self, ml_generator):
        prices = np.array([100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 110, 108, 107, 109, 110, 112, 111, 113, 115, 114])
        position = ml_generator._calculate_bb_position(prices)
        
        assert 0 <= position <= 1

# tests/test_integration.py
@pytest.mark.integration
class TestIntegration:
    
    @pytest.mark.asyncio
    async def test_full_signal_to_execution_flow(self):
        """Test complete flow from signal generation to execution."""
        # Setup real database connection for integration test
        config = Config.load("test")
        db_pool = await create_db_pool(config.database)
        redis_client = await create_redis_client(config.redis)
        grpc_client = AsyncGRPCClient(GRPCConfig(host="localhost", port=50051))
        
        try:
            await grpc_client.connect()
            
            # Create test signal
            signal = Signal(
                mint="TestMint1111111111111111111111111111111111",
                action="BUY",
                amount=1_000_000,
                max_cost=2_000_000,
                slippage_bps=100,
                strategy="test_strategy"
            )
            
            # Submit order via gRPC
            response = await grpc_client.submit_order(
                mint=signal.mint,
                order_type="MARKET",
                side=signal.action,
                amount=signal.amount,
                max_cost=signal.max_cost,
                slippage_bps=signal.slippage_bps,
                strategy_name=signal.strategy
            )
            
            assert response.success
            assert response.order_id
            
            # Check order status
            status = await grpc_client.get_order_status(response.order_id)
            assert status.order_id == response.order_id
            
        finally:
            await grpc_client.close()
            await db_pool.close()
            await redis_client.close()