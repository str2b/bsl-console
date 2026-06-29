import time
from unittest.mock import MagicMock
import pytest
from bsl_console.bootloader import BootloaderClient
from bsl_console.protocol import HeaderBlock, DataBlock, EotBlock, BootloaderResponse


class FakeMessage:
    def __init__(self, arbitration_id, data=b"", is_error_frame=False):
        self.arbitration_id = arbitration_id
        self.data = data
        self.is_error_frame = is_error_frame


class FakeTransport:
    def __init__(self, messages=None):
        self.messages = messages or []
        self.sent = []
        self.drained = 0

    def open(self):
        pass

    def close(self):
        pass

    def drain_rx(self):
        self.drained += 1

    def send(self, arbitration_id, payload, *, extended_id=False):
        self.sent.append((arbitration_id, payload, extended_id))

    def recv(self, timeout=None):
        if self.messages:
            return self.messages.pop(0)
        return None


def test_await_response_skips_error_frames() -> None:
    # Queue up an error frame, then a message with command_arbitration_id (which should be skipped),
    # then a mismatching response arbitration ID (if configured), then the valid response.
    msg_err = FakeMessage(0x00, data=b"\x00\x00\x00\x7e", is_error_frame=True)
    msg_echo = FakeMessage(0x123, data=b"\x00\x07\xa0", is_error_frame=False)
    msg_mismatch = FakeMessage(0x999, data=b"\x55", is_error_frame=False)
    msg_ok = FakeMessage(0x321, data=b"\x55\x11\x22", is_error_frame=False)

    transport = FakeTransport([msg_err, msg_echo, msg_mismatch, msg_ok])
    client = BootloaderClient(
        transport=transport,
        command_arbitration_id=0x123,
        response_arbitration_id=0x321,
    )

    response = client._await_response()
    assert response.code == 0x55
    assert response.data == b"\x55\x11\x22"
    # Ensure remaining messages are only None
    assert transport.messages == []


def test_await_response_without_response_id_accepts_any_non_command() -> None:
    # If response_arbitration_id is None, it should accept the mismatch message (0x999) as the response.
    msg_echo = FakeMessage(0x123, data=b"\x00\x07\xa0")
    msg_mismatch = FakeMessage(0x999, data=b"\x55")

    transport = FakeTransport([msg_echo, msg_mismatch])
    client = BootloaderClient(
        transport=transport,
        command_arbitration_id=0x123,
        response_arbitration_id=None,
    )

    response = client._await_response()
    assert response.code == 0x55


def test_send_header_with_inter_frame_delay(monkeypatch) -> None:
    sleeps = []
    monkeypatch.setattr("bsl_console.bootloader.sleep", lambda x: sleeps.append(x))

    transport = FakeTransport()
    client = BootloaderClient(
        transport=transport,
        command_arbitration_id=0x123,
        inter_frame_delay_s=0.005,
    )

    header = HeaderBlock(0x07)
    client._send_header(header)

    assert len(transport.sent) == 2
    assert sleeps == [0.005]


def test_send_block_with_inter_frame_delay(monkeypatch) -> None:
    sleeps = []
    monkeypatch.setattr("bsl_console.bootloader.sleep", lambda x: sleeps.append(x))

    transport = FakeTransport()
    client = BootloaderClient(
        transport=transport,
        command_arbitration_id=0x123,
        inter_frame_delay_s=0.002,
    )

    # DataBlock is 264 bytes = 33 chunks of 8 bytes
    block = DataBlock(b"\x00" * 256)
    client._send_block(block)

    assert len(transport.sent) == 33
    # First frame doesn't trigger sleep, subsequent 32 frames do.
    assert len(sleeps) == 32
    assert all(s == 0.002 for s in sleeps)


def test_transport_send_retries_on_queue_full(monkeypatch) -> None:
    from bsl_console.transport import CanTransport, CanTransportConfig
    import time

    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda x: sleeps.append(x))

    transport = CanTransport(CanTransportConfig(interface="pcan", channel="PCAN_USBBUS1"))

    mock_bus = MagicMock()
    call_count = 0
    def mock_send(msg):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("PCAN_ERROR_QXMTFULL: The transmit queue is full")
        # succeed
    mock_bus.send = mock_send
    transport._bus = mock_bus

    transport.send(0x123, b"\x11\x22")

    assert call_count == 3
    assert sleeps == [0.001, 0.001]

