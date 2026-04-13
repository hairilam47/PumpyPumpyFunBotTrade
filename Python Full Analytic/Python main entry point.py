# main.py
import asyncio
import signal
import sys
from pathlib import Path

from config import Config
from analytics.data_collector import PumpFunDataCollector
from analytics.ml_signals import MLSignalGenerator
from strategies.sniper import PumpFunSniper, SniperConfig
from strategies.momentum import MomentumTrader
from grpc_client.async_client import AsyncGRPCClient, GRPCConfig
from utils.logger import setup_logging
from utils.metrics import MetricsServer
from utils.database import create_db_pool, create_redis_client

class PumpFunBot:
    """Main bot application."""
    
    def __init__(self):
        self.config = Config.load()
        self.logger = setup_logging(self.config.monitoring.log_level)
        self.running = True
        self.tasks = []
        
        # Components
        self.db_pool = None
        self.redis = None
        self.grpc_client = None
        self.data_collector = None
        self.ml_generator = None
        self.strategies = []
        self.metrics_server = None
    
    async def initialize(self):
        """Initialize all components."""
        self.logger.info(f"Initializing PumpFun Bot - Environment: {self.config.environment}")
        
        # Initialize database
        self.db_pool = await create_db_pool(self.config.database)
        self.logger.info("Database connected")
        
        # Initialize Redis
        self.redis = await create_redis_client(self.config.redis)
        self.logger.info("Redis connected")
        
        # Initialize gRPC client
        grpc_config = GRPCConfig(
            host=self.config.grpc.host,
            port=self.config.grpc.port,
            use_tls=self.config.grpc.use_tls,
            timeout_seconds=self.config.grpc.timeout_seconds
        )
        self.grpc_client = AsyncGRPCClient(grpc_config)
        await self.grpc_client.connect()
        self.logger.info("gRPC client connected")
        
        # Initialize data collector
        self.data_collector = PumpFunDataCollector(self.db_pool, self.redis)
        
        # Initialize ML signal generator
        self.ml_generator = MLSignalGenerator(self.db_pool, self.redis)
        await self.ml_generator.initialize_models()
        
        # Load or train models
        if not await self.ml_generator.load_models():
            self.logger.info("No saved models found, training new models...")
            await self.ml_generator.train_models(days_history=30)
        
        # Initialize strategies
        await self.init_strategies()
        
        # Initialize metrics server
        self.metrics_server = MetricsServer(self.config.monitoring.prometheus_port)
        
        self.logger.info("Initialization complete")
    
    async def init_strategies(self):
        """Initialize trading strategies from config."""
        for name, strategy_config in self.config.strategies.items():
            if not strategy_config.enabled:
                continue
            
            if name == "sniper":
                config = SniperConfig(
                    name=name,
                    **strategy_config.parameters
                )
                strategy = PumpFunSniper(
                    config=config,
                    db_pool=self.db_pool,
                    redis_client=self.redis,
                    grpc_client=self.grpc_client
                )
                self.strategies.append(strategy)
                self.logger.info(f"Initialized sniper strategy")
            
            elif name == "momentum":
                strategy = MomentumTrader(
                    config=strategy_config,
                    db_pool=self.db_pool,
                    redis_client=self.redis,
                    grpc_client=self.grpc_client
                )
                self.strategies.append(strategy)
                self.logger.info(f"Initialized momentum strategy")
    
    async def run(self):
        """Run the bot."""
        self.logger.info("Starting bot main loop")
        
        # Start metrics server
        metrics_task = asyncio.create_task(self.metrics_server.start())
        self.tasks.append(metrics_task)
        
        # Start data collection
        collector_task = asyncio.create_task(self.data_collector.start_collection())
        self.tasks.append(collector_task)
        
        # Main processing loop
        signal_queue = asyncio.Queue()
        
        # Subscribe to new token events
        async def handle_new_token(token_data):
            for strategy in self.strategies:
                if strategy.can_trade():
                    signal = await strategy.on_new_token(token_data)
                    if signal:
                        await self.execute_signal(signal)
        
        # Subscribe to price updates
        async def handle_price_update(mint, price, metrics):
            for strategy in self.strategies:
                signal = await strategy.on_price_update(mint, price, metrics)
                if signal:
                    await self.execute_signal(signal)
        
        # Subscribe to ML signals
        async def handle_ml_signals():
            while self.running:
                try:
                    # Get token data for all monitored tokens
                    token_data = await self.get_monitored_token_data()
                    
                    # Generate ML signals
                    signals = await self.ml_generator.generate_signals(token_data)
                    
                    # Process signals through strategies
                    for mint, ml_signal in signals.items():
                        for strategy in self.strategies:
                            signal = await strategy.on_signal(ml_signal)
                            if signal:
                                await self.execute_signal(signal)
                    
                except Exception as e:
                    self.logger.error(f"Error in ML signal handler: {e}")
                
                await asyncio.sleep(5)  # Run every 5 seconds
        
        ml_task = asyncio.create_task(handle_ml_signals())
        self.tasks.append(ml_task)
        
        # Wait for shutdown
        while self.running:
            await asyncio.sleep(1)
    
    async def execute_signal(self, signal):
        """Execute a trading signal."""
        self.logger.info(f"Executing signal: {signal.action} {signal.mint}")
        
        try:
            response = await self.grpc_client.submit_order(
                mint=signal.mint,
                order_type="MARKET",
                side=signal.action,
                amount=signal.amount,
                max_cost=signal.max_cost,
                min_output=signal.min_output,
                slippage_bps=signal.slippage_bps,
                strategy_name=signal.strategy,
                metadata=signal.metadata
            )
            
            if response.success:
                self.logger.info(f"Order submitted: {response.order_id}")
                
                # Notify strategy
                for strategy in self.strategies:
                    if strategy.config.name == signal.strategy:
                        await strategy.on_trade_complete(signal.mint, {
                            "order_id": response.order_id,
                            "action": signal.action,
                            "amount": signal.amount,
                        })
            else:
                self.logger.error(f"Order failed: {response.message}")
                
        except Exception as e:
            self.logger.error(f"Error executing signal: {e}")
    
    async def get_monitored_token_data(self):
        """Get data for all monitored tokens."""
        token_data = {}
        
        for mint in self.data_collector.monitored_tokens:
            if mint in self.data_collector.metrics_buffer:
                buffer = list(self.data_collector.metrics_buffer[mint])
                if buffer:
                    import pandas as pd
                    df = pd.DataFrame([m.__dict__ for m in buffer])
                    token_data[mint] = df
        
        return token_data
    
    async def shutdown(self):
        """Graceful shutdown."""
        self.logger.info("Shutting down...")
        self.running = False
        
        # Cancel all tasks
        for task in self.tasks:
            task.cancel()
        
        # Wait for tasks to complete
        await asyncio.gather(*self.tasks, return_exceptions=True)
        
        # Close connections
        if self.grpc_client:
            await self.grpc_client.close()
        
        if self.db_pool:
            await self.db_pool.close()
        
        if self.redis:
            await self.redis.close()
        
        self.logger.info("Shutdown complete")

async def main():
    """Main entry point."""
    bot = PumpFunBot()
    
    # Setup signal handlers
    loop = asyncio.get_running_loop()
    
    def signal_handler():
        asyncio.create_task(bot.shutdown())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        await bot.initialize()
        await bot.run()
    except Exception as e:
        bot.logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())