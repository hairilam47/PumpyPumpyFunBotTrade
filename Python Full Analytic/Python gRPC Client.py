# grpc_client/async_client.py
import asyncio
import grpc
from typing import Optional, Dict, Any
from dataclasses import dataclass
import logging

# Generated protobuf code
import bot_pb2
import bot_pb2_grpc

logger = logging.getLogger(__name__)

@dataclass
class GRPCConfig:
    host: str = "localhost"
    port: int = 50051
    use_tls: bool = False
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_delay_ms: int = 1000

class AsyncGRPCClient:
    """Asynchronous gRPC client for bot communication."""
    
    def __init__(self, config: GRPCConfig):
        self.config = config
        self.channel = None
        self.stub = None
        self._lock = asyncio.Lock()
    
    async def connect(self):
        """Establish gRPC connection."""
        async with self._lock:
            if self.channel is None:
                target = f"{self.config.host}:{self.config.port}"
                
                if self.config.use_tls:
                    credentials = grpc.ssl_channel_credentials()
                    self.channel = grpc.aio.secure_channel(target, credentials)
                else:
                    self.channel = grpc.aio.insecure_channel(target)
                
                self.stub = bot_pb2_grpc.BotStub(self.channel)
                logger.info(f"Connected to gRPC server at {target}")
    
    async def close(self):
        """Close gRPC connection."""
        if self.channel:
            await self.channel.close()
            self.channel = None
            self.stub = None
    
    async def submit_order(
        self,
        mint: str,
        order_type: str,
        side: str,
        amount: int,
        max_cost: Optional[int] = None,
        min_output: Optional[int] = None,
        slippage_bps: int = 100,
        strategy_name: str = "default",
        metadata: Dict[str, str] = None
    ) -> bot_pb2.SubmitOrderResponse:
        """Submit a trading order."""
        await self.connect()
        
        request = bot_pb2.SubmitOrderRequest(
            token_mint=mint,
            order_type=order_type,
            side=side,
            amount=amount,
            slippage_bps=slippage_bps,
            strategy_name=strategy_name,
            metadata=metadata or {}
        )
        
        if max_cost is not None:
            request.max_sol_cost = max_cost
        if min_output is not None:
            request.min_sol_output = min_output
        
        return await self._retry_call(
            self.stub.SubmitOrder,
            request,
            timeout=self.config.timeout_seconds
        )
    
    async def cancel_order(self, order_id: str) -> bot_pb2.CancelOrderResponse:
        """Cancel an existing order."""
        await self.connect()
        
        request = bot_pb2.CancelOrderRequest(order_id=order_id)
        
        return await self._retry_call(
            self.stub.CancelOrder,
            request,
            timeout=self.config.timeout_seconds
        )
    
    async def get_order_status(self, order_id: str) -> bot_pb2.OrderStatusResponse:
        """Get order status."""
        await self.connect()
        
        request = bot_pb2.GetOrderStatusRequest(order_id=order_id)
        
        return await self._retry_call(
            self.stub.GetOrderStatus,
            request,
            timeout=self.config.timeout_seconds
        )
    
    async def get_token_info(self, mint: str) -> bot_pb2.TokenInfoResponse:
        """Get token information."""
        await self.connect()
        
        request = bot_pb2.GetTokenInfoRequest(token_mint=mint)
        
        return await self._retry_call(
            self.stub.GetTokenInfo,
            request,
            timeout=self.config.timeout_seconds
        )
    
    async def get_portfolio_summary(self) -> bot_pb2.PortfolioSummaryResponse:
        """Get portfolio summary."""
        await self.connect()
        
        request = bot_pb2.Empty()
        
        return await self._retry_call(
            self.stub.GetPortfolioSummary,
            request,
            timeout=self.config.timeout_seconds
        )
    
    async def stream_orders(self, order_ids: list):
        """Stream order updates."""
        await self.connect()
        
        request = bot_pb2.StreamOrdersRequest(order_ids=order_ids)
        
        try:
            async for update in self.stub.StreamOrders(request):
                yield update
        except grpc.RpcError as e:
            logger.error(f"Order stream error: {e}")
            raise
    
    async def _retry_call(self, method, request, timeout: int):
        """Retry gRPC call with exponential backoff."""
        for attempt in range(self.config.max_retries):
            try:
                return await method(request, timeout=timeout)
            except grpc.RpcError as e:
                if attempt == self.config.max_retries - 1:
                    raise
                
                wait_time = self.config.retry_delay_ms * (2 ** attempt) / 1000
                logger.warning(f"gRPC call failed (attempt {attempt + 1}), retrying in {wait_time}s: {e}")
                await asyncio.sleep(wait_time)
        
        raise RuntimeError("Max retries exceeded")
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()