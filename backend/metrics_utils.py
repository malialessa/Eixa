"""Utilidades de métricas internas (latência, sucesso) para EIXA.

Evita import circular com BigQuery usando injeção tardia de bq_manager.
"""
import time
import functools
import logging
import asyncio
from typing import Callable, Any, Dict, Awaitable

logger = logging.getLogger(__name__)

_bq_manager = None  # será configurado após inicialização do BigQuery

def set_bq_manager(manager):
    """Injeta instância de BigQuery manager para envio de métricas."""
    global _bq_manager
    _bq_manager = manager

def record_latency(operation: str, duration_ms: float, success: bool, extra: Dict[str, Any] | None = None):
    """Encapsula envio para BigQuery se _bq_manager estiver definido."""
    if _bq_manager:
        try:
            asyncio.create_task(_bq_manager.log_operation_metric(operation, duration_ms, success, extra))
        except Exception as e:
            logger.warning(f"Falha ao agendar log de métrica {operation}: {e}")
    else:
        logger.debug(f"Metric (sem BQ) {operation}={duration_ms:.2f}ms success={success}")

def measure_async(operation: str):
    """Decorador para medir corrotinas e registrar métricas."""
    def wrapper(func: Callable[..., Awaitable[Any]]):
        @functools.wraps(func)
        async def inner(*args, **kwargs):
            start = time.perf_counter()
            success = True
            try:
                return await func(*args, **kwargs)
            except Exception:
                success = False
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000.0
                record_latency(operation, duration_ms, success)
        return inner
    return wrapper
