"""The optional-session dependency.

`get_optional_session` exists so `/query` still answers from the synthetic workspace when Postgres
is deliberately offline. Its original form wrapped the `yield` in the same `try` as the connection
check, so an exception raised by the endpoint was thrown back into the generator, caught, and
answered with a second `yield` — reported as `RuntimeError: generator didn't stop after athrow()`
and surfaced to the client as a 500.

Nothing raised from `/query` until multi-turn added a rejected conversation id, so the fault sat
unexercised. The integration tests cannot catch it either: they override this dependency.
"""

import pytest
from fastapi import HTTPException

from app.db import session as session_module


class FakeSession:
    def __init__(self, *, reachable: bool) -> None:
        self.reachable = reachable
        self.closed = False

    async def execute(self, *args, **kwargs) -> None:
        if not self.reachable:
            raise RuntimeError("connection refused")

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_factory(monkeypatch):
    def _install(*, reachable: bool) -> FakeSession:
        fake = FakeSession(reachable=reachable)
        monkeypatch.setattr(session_module, "SessionFactory", lambda: fake)
        return fake

    return _install


async def test_endpoint_exceptions_propagate_instead_of_becoming_a_500(fake_factory) -> None:
    """A deliberate 404 must reach the client as a 404."""
    fake = fake_factory(reachable=True)
    generator = session_module.get_optional_session()
    assert await anext(generator) is fake

    with pytest.raises(HTTPException):
        await generator.athrow(HTTPException(status_code=404, detail="not found"))

    assert fake.closed, "The session must still be released when the endpoint raised."


async def test_an_unreachable_database_still_yields_none(fake_factory) -> None:
    """The behaviour the dependency exists for, unchanged by the fix."""
    fake = fake_factory(reachable=False)
    generator = session_module.get_optional_session()

    assert await anext(generator) is None
    assert fake.closed


async def test_the_session_is_released_on_the_normal_path(fake_factory) -> None:
    fake = fake_factory(reachable=True)
    generator = session_module.get_optional_session()
    await anext(generator)

    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    assert fake.closed
