from dataclasses import dataclass

import pytest
from pynetdicom.sop_class import ModalityPerformedProcedureStep, Verification

from factories import config
from modality_simulator import probe
from modality_simulator.errors import GatewayError


@dataclass
class Context:
    abstract_syntax: str
    result: int = 0x00


class FakeAssociation:
    def __init__(self, accepted=(), rejected=()):
        self.accepted_contexts = list(accepted)
        self.rejected_contexts = list(rejected)
        self.released = False

    def release(self):
        self.released = True


def connect(monkeypatch, assoc):
    proposed = []

    def associate(cfg, syntaxes):
        proposed.extend(syntaxes)
        if isinstance(assoc, Exception):
            raise assoc
        return assoc
    monkeypatch.setattr(probe.dicom_net, "associate", associate)
    return proposed


def test_reports_mpps_accepted(monkeypatch, caplog):
    assoc = FakeAssociation(accepted=[Context(Verification), Context(ModalityPerformedProcedureStep)])
    proposed = connect(monkeypatch, assoc)
    with caplog.at_level("INFO", logger="modality_simulator.probe"):
        message = probe.probe_mpps(config())
    assert "accepted" in message and "TEST_GW" in message
    assert message in caplog.text
    # Verification too, so a refused MPPS context doesn't leave nothing to accept and abort the association.
    assert set(proposed) == {Verification, ModalityPerformedProcedureStep}
    assert assoc.released


def test_reports_mpps_rejected_with_the_reason(monkeypatch, caplog):
    assoc = FakeAssociation(accepted=[Context(Verification)],
                            rejected=[Context(ModalityPerformedProcedureStep, result=0x03)])
    connect(monkeypatch, assoc)
    with caplog.at_level("INFO", logger="modality_simulator.probe"):
        message = probe.probe_mpps(config())
    assert "does not accept MPPS" in message
    assert "abstract syntax not supported" in message
    assert message in caplog.text
    assert assoc.released


def test_reports_a_gateway_it_cannot_connect_to(monkeypatch, caplog):
    connect(monkeypatch, GatewayError("The gateway couldn't be reached"))
    with caplog.at_level("INFO", logger="modality_simulator.probe"):
        message = probe.probe_mpps(config())
    assert "couldn't be reached" in message
    assert message in caplog.text
