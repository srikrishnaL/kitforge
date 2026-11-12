import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakedt import KEY, FakeDT  # noqa: E402

from bomscout import DependencyTrack, Scout  # noqa: E402


@pytest.fixture
def fake():
    return FakeDT()


@pytest.fixture
def dt(fake):
    return DependencyTrack("https://dtrack.example.com", KEY, transport=fake, page_size=2)


@pytest.fixture
def scout(dt):
    return Scout(dt)
