"""Test data builders shared by the unit and integration tests."""

from modality_simulator.config import Config


def config(**overrides) -> Config:
    return Config(**{"gateway_ae": "TEST_GW", **overrides})
