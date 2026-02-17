import pytest
import asyncio
from metrics_utils import set_bq_manager, measure_async

@pytest.mark.asyncio
async def test_measure_async_records_metric(fake_bq_manager):
    set_bq_manager(fake_bq_manager)

    @measure_async("test.op")
    async def sample():
        return 42

    result = await sample()
    assert result == 42
    # Aguarda tick do loop para tarefa de log
    await asyncio.sleep(0.01)
    assert any(m["operation"] == "test.op" for m in fake_bq_manager.logged_metrics)
