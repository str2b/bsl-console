from bsl_console.bootstrap import BootstrapConfig, BootstrapTransferClient


class DummyAck:
    arbitration_id = 0x008
    data = b"\x41\x3A\x20\x40"


class DummyTransport:
    def __init__(self) -> None:
        self.sent = []
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def send(self, arbitration_id, payload, *, extended_id=False):
        self.sent.append((arbitration_id, bytes(payload), extended_id))

    def recv(self, timeout=None):
        return DummyAck()


def test_bootstrap_transfer_builds_init_frame_and_sends_payload() -> None:
    transport = DummyTransport()
    client = BootstrapTransferClient(transport, BootstrapConfig(ack_id=0x4020, data_id=0x4000))
    result = client.transfer(b"abcd")
    assert result.acknowledged is True
    assert transport.opened is True
    assert transport.sent[0][1] == b"\x55\x55\x20\x40\x01\x00\x00\x40"
    assert transport.sent[1][0] == 0x000
    assert len(transport.sent) == 2

