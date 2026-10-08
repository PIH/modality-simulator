"""A temporary check of whether the gateway accepts MPPS (Modality Performed Procedure Step).

It only negotiates: it never sends an MPPS message, so no order's status changes.
"""

from __future__ import annotations

import logging

from pynetdicom.sop_class import ModalityPerformedProcedureStep, Verification

from modality_simulator import dicom_net
from modality_simulator.config import Config
from modality_simulator.errors import GatewayError

log = logging.getLogger(__name__)

# Presentation context results, PS3.8 9.3.3.2.
REASONS = {
    0x01: "user rejection",
    0x02: "no reason given",
    0x03: "abstract syntax not supported",
    0x04: "transfer syntaxes not supported",
}


def probe_mpps(cfg: Config) -> str:
    """Whether the gateway accepts MPPS, as a message that is also logged."""
    gateway = f"The gateway ({cfg.gateway_ae} at {cfg.gateway_host}:{cfg.gateway_port})"
    try:
        # Verification too: if MPPS were the only context and the gateway refused it, pynetdicom would
        # abort the association, which reads as an unreachable gateway rather than an answer.
        assoc = dicom_net.associate(cfg, [ModalityPerformedProcedureStep, Verification])
    except GatewayError as e:
        message = f"MPPS check: couldn't ask the gateway. {e}"
        log.warning(message)
        return message
    try:
        if any(cx.abstract_syntax == ModalityPerformedProcedureStep for cx in assoc.accepted_contexts):
            message = f"MPPS check: {gateway} accepted MPPS for {cfg.calling_ae}."
        else:
            refused = [cx for cx in assoc.rejected_contexts if cx.abstract_syntax == ModalityPerformedProcedureStep]
            reason = REASONS.get(refused[0].result, f"result 0x{refused[0].result:02X}") if refused else "no answer"
            message = f"MPPS check: {gateway} does not accept MPPS from {cfg.calling_ae} ({reason})."
    finally:
        assoc.release()
    log.info(message)
    return message
