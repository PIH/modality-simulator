"""Opening associations to the gateway, with errors a person can act on."""

from __future__ import annotations

from pynetdicom import AE
from pynetdicom.association import Association

from modality_simulator.config import Config
from modality_simulator.errors import GatewayError

CONNECT_TIMEOUT_SECONDS = 10
RESPONSE_TIMEOUT_SECONDS = 30


def associate(cfg: Config, abstract_syntaxes: list[str]) -> Association:
    """An established association from cfg.calling_ae to the gateway, proposing each syntax."""
    ae = AE(ae_title=cfg.calling_ae)
    ae.connection_timeout = CONNECT_TIMEOUT_SECONDS
    ae.acse_timeout = CONNECT_TIMEOUT_SECONDS
    ae.dimse_timeout = RESPONSE_TIMEOUT_SECONDS
    ae.network_timeout = RESPONSE_TIMEOUT_SECONDS
    for uid in abstract_syntaxes:
        ae.add_requested_context(uid)
    assoc = ae.associate(cfg.gateway_host, cfg.gateway_port, ae_title=cfg.gateway_ae)
    if assoc.is_established:
        return assoc
    where = f"{cfg.gateway_ae} at {cfg.gateway_host}:{cfg.gateway_port}"
    if assoc.is_rejected:
        raise GatewayError(
            f"The gateway ({where}) rejected the association from {cfg.calling_ae}: check that "
            f"{cfg.calling_ae} is set up as a Remote AE in AdvaPACS, and that {cfg.gateway_ae} is "
            f"the gateway's Local AE title"
        )
    raise GatewayError(
        f"The gateway ({where}) couldn't be reached, or closed the connection: check the host "
        f"and port, and that the gateway is running (it opens its DICOM port only after AdvaPACS "
        f"has sent it its configuration)"
    )
