# config/__init__.py
import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

class DatabaseConfig(BaseModel):
    host: str = Field(default="localhost")
    port: int = Field(default=5432)
    database: str = Field(default="pumpfun")
    username: str = Field(default="postgres")
    password: str = Field(default="")
    pool_size: int = Field(default=10)
    max_overflow: int = Field(default=20)

class RedisConfig(BaseModel):
    host: str = Field(default="localhost")
    port: int = Field(default=6379)
    db: int = Field(default=0)
    password: Optional[str] = None
    ssl: bool = Field(default=False)

class GRPCConfig(BaseModel):
    host: str = Field(default="localhost")
    port: int = Field(default=50051)
    use_tls: bool = Field(default=False)
    timeout_seconds: int = Field(default=30)

class TradingConfig(BaseModel):
    min_trade_size_sol: float = Field(default=0.01)
    max_trade_size_sol: float = Field(default=10.0)
    default_slippage_bps: int = Field(default=100)
    max_slippage_bps: int = Field(default=500)
    max_positions: int = Field(default=10)
    max_daily_trades: int = Field(default=100)

class RiskConfig(BaseModel):
    max_position_size_sol: float = Field(default=5.0)
    max_portfolio_exposure_sol: float = Field(default=50.0)
    max_daily_loss_sol: float = Field(default=10.0)
    max_drawdown_pct: float = Field(default=20.0)
    stop_loss_pct: float = Field(default=15.0)
    take_profit_pct: float = Field(default=30.0)

class StrategyConfig(BaseModel):
    name: str
    enabled: bool = Field(default=True)
    parameters: Dict[str, Any] = Field(default_factory=dict)

class MonitoringConfig(BaseModel):
    prometheus_port: int = Field(default=9092)
    log_level: str = Field(default="INFO")
    sentry_dsn: Optional[str] = None
    slack_webhook: Optional[str] = None

class Config(BaseModel):
    environment: str = Field(default="development")
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    grpc: GRPCConfig = Field(default_factory=GRPCConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategies: Dict[str, StrategyConfig] = Field(default_factory=dict)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)

    @classmethod
    def load(cls, environment: Optional[str] = None) -> "Config":
        env = environment or os.getenv("ENVIRONMENT", "development")
        config_path = Path(__file__).parent / f"{env}.yaml"
        
        config_data = {}
        if config_path.exists():
            with open(config_path) as f:
                config_data = yaml.safe_load(f)
        
        # Override with environment variables
        config_data = cls._apply_env_overrides(config_data)
        
        return cls(**config_data)
    
    @classmethod
    def _apply_env_overrides(cls, data: Dict) -> Dict:
        """Apply environment variable overrides."""
        for key, value in os.environ.items():
            if key.startswith("PUMPFUN_"):
                parts = key.lower().replace("pumpfun_", "").split("__")
                current = data
                for part in parts[:-1]:
                    current = current.setdefault(part, {})
                current[parts[-1]] = cls._parse_value(value)
        return data
    
    @staticmethod
    def _parse_value(value: str) -> Any:
        """Parse environment variable value."""
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value

# config/dev.yaml
environment: development

database:
  host: localhost
  port: 5432
  database: pumpfun_dev
  username: postgres
  password: ""
  pool_size: 5

redis:
  host: localhost
  port: 6379
  db: 0

grpc:
  host: localhost
  port: 50051
  use_tls: false

trading:
  min_trade_size_sol: 0.01
  max_trade_size_sol: 1.0
  default_slippage_bps: 100
  max_positions: 5
  max_daily_trades: 50

risk:
  max_position_size_sol: 1.0
  max_portfolio_exposure_sol: 10.0
  max_daily_loss_sol: 2.0
  stop_loss_pct: 10.0
  take_profit_pct: 20.0

strategies:
  sniper:
    enabled: true
    parameters:
      min_liquidity_sol: 5.0
      max_creator_rug_risk: 0.3
      buy_amount_sol: 0.1
      take_profit_pct: 50
      stop_loss_pct: 20
  
  momentum:
    enabled: true
    parameters:
      lookback_periods: 20
      momentum_threshold: 70
      volume_multiplier: 2.0

monitoring:
  prometheus_port: 9092
  log_level: DEBUG