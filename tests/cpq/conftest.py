"""Engine-only fixtures."""
import pytest
from console.cpq.models import GateContext


@pytest.fixture
def engine_ctx() -> GateContext:
    return GateContext(images_ok={'MODEL-1': True})
