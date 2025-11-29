import asyncio
import types
import pytest

class FakeBQManager:
    def __init__(self):
        self.logged_metrics = []
    async def log_operation_metric(self, operation, duration_ms, success, extra=None):
        self.logged_metrics.append({
            "operation": operation,
            "duration_ms": duration_ms,
            "success": success,
            "extra": extra
        })

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture()
def fake_bq_manager():
    return FakeBQManager()
