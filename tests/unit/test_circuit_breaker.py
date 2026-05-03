"""
Unit tests for engine/core/circuit_breaker.py
Tests the pure logic — should_trip() — without AWS.
The check() function requires execution_log (Athena) so is tested in integration.
"""
from config.settings import CIRCUIT_BREAKER_THRESHOLD
from engine.core.circuit_breaker import CLOSED, OPEN, should_trip


def test_should_not_trip_zero_failures():
    assert should_trip(0) is False


def test_should_not_trip_below_threshold():
    assert should_trip(CIRCUIT_BREAKER_THRESHOLD - 1) is False


def test_should_trip_at_threshold():
    assert should_trip(CIRCUIT_BREAKER_THRESHOLD) is True


def test_should_trip_above_threshold():
    assert should_trip(CIRCUIT_BREAKER_THRESHOLD + 5) is True


def test_threshold_is_positive():
    # Threshold must be at least 1
    assert CIRCUIT_BREAKER_THRESHOLD >= 1


def test_states_are_strings():
    assert isinstance(CLOSED, str)
    assert isinstance(OPEN, str)
    assert CLOSED != OPEN
