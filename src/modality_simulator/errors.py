"""Errors the simulator reports to a person, with messages they can act on."""


class ConfigError(Exception):
    """A setting is missing or invalid; the message names the MODALITY_SIMULATOR_* variable."""


class GatewayError(Exception):
    """The gateway couldn't be reached, refused us, or answered with a failure."""
