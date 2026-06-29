from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from time import monotonic, sleep
from typing import Callable

from .protocol import normalize_boot_identifier, u16_le
from .transport import CanTransport


@dataclass(slots=True)
class BootstrapConfig:
    ack_id: int
    data_id: int
    message_count: int = 0
    init_arbitration_id: int = 0x000
    init_interval_s: float = 1.0
    post_ack_delay_s: float = 0.01
    data_interval_s: float = 0.007
    data_send_retry_count: int = 5
    data_send_retry_delay_s: float = 0.002
    ack_timeout_s: float = 0.020
    pad_byte: int = 0x00

    @property
    def ack_can_id(self) -> int:
        return normalize_boot_identifier(self.ack_id)

    @property
    def data_can_id(self) -> int:
        return normalize_boot_identifier(self.data_id)


@dataclass(slots=True)
class BootstrapTransferResult:
    acknowledged: bool
    ack_frame_id: int | None = None
    ack_data: bytes = b""
    bit_timing: int | None = None
    sent_frames: int = 0
    message_count: int = 0
    init_frame: bytes = b""
    first_data_frame: bytes = b""


class BootstrapTransferClient:
    def __init__(self, transport: CanTransport, config: BootstrapConfig) -> None:
        self.transport = transport
        self.config = config

    def build_init_frame(self, message_count: int) -> bytes:
        payload = bytearray(8)
        payload[0] = 0x55
        payload[1] = 0x55
        payload[2:4] = u16_le(self.config.ack_id)
        payload[4:6] = u16_le(message_count)
        payload[6:8] = u16_le(self.config.data_id)
        return bytes(payload)

    def transfer(
        self,
        payload: bytes | bytearray | memoryview,
        *,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> BootstrapTransferResult:
        self.transport.open()

        payload_bytes = bytes(payload)
        total_bytes = self.config.message_count * 8 if self.config.message_count > 0 else ceil(len(payload_bytes) / 8) * 8
        message_count = total_bytes // 8
        init_frame = self.build_init_frame(message_count)
        frames_sent = 0
        ack_frame = None
        data_phase_start = 0.0

        for _ in range(1_000):
            try:
                self.transport.send(self.config.init_arbitration_id, init_frame)
                frames_sent += 1
            except Exception:
                # In one-shot mode before target ACKs, TX can report failure.
                # Keep trying at configured spacing until a valid ACK arrives.
                pass

            ack_deadline = monotonic() + self.config.ack_timeout_s
            expected_ack_word = u16_le(self.config.ack_id)
            while monotonic() < ack_deadline:
                timeout = max(0.0, ack_deadline - monotonic())
                candidate = self.transport.recv(timeout)
                if candidate is None:
                    break
                if getattr(candidate, "arbitration_id", None) != self.config.ack_can_id:
                    continue
                ack_data = bytes(getattr(candidate, "data", b""))
                if len(ack_data) >= 4 and ack_data[2:4] == expected_ack_word:
                    ack_frame = candidate
                    break

            if ack_frame is not None:
                break
            sleep(self.config.init_interval_s)

        if ack_frame is None:
            return BootstrapTransferResult(acknowledged=False, sent_frames=frames_sent)

        ack_data = bytes(getattr(ack_frame, "data", b""))
        bit_timing = int.from_bytes(ack_data[:2], "little") if len(ack_data) >= 2 else None

        sleep(self.config.post_ack_delay_s)
        data_phase_start = monotonic()

        if len(payload_bytes) > total_bytes:
            raise ValueError(f"bootstrap payload exceeds configured transfer size ({total_bytes} bytes)")
        if len(payload_bytes) < total_bytes:
            payload_bytes = payload_bytes + bytes([self.config.pad_byte & 0xFF]) * (total_bytes - len(payload_bytes))

        if progress_cb is not None:
            progress_cb(0, message_count, "bootstrap")

        for offset in range(0, total_bytes, 8):
            chunk = payload_bytes[offset : offset + 8]
            last_exc: Exception | None = None
            for _ in range(max(1, self.config.data_send_retry_count)):
                try:
                    self.transport.send(self.config.data_can_id, chunk)
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    sleep(self.config.data_send_retry_delay_s)
            if last_exc is not None:
                frame_index = offset // 8
                elapsed = monotonic() - data_phase_start if data_phase_start else 0.0
                raise RuntimeError(
                    "bootstrap payload send failed: "
                    f"frame_index={frame_index}/{message_count - 1}, "
                    f"can_id=0x{self.config.data_can_id:03X}, "
                    f"payload={chunk.hex(' ')}, "
                    f"retries={max(1, self.config.data_send_retry_count)}, "
                    f"elapsed_s={elapsed:.3f}, "
                    f"cause={type(last_exc).__name__}: {last_exc}"
                ) from last_exc
            frames_sent += 1
            if progress_cb is not None:
                progress_cb((offset // 8) + 1, message_count, "bootstrap")
            if self.config.data_interval_s > 0:
                sleep(self.config.data_interval_s)

        first_data_frame = payload_bytes[0:8] if total_bytes > 0 else b""

        return BootstrapTransferResult(
            acknowledged=True,
            ack_frame_id=getattr(ack_frame, "arbitration_id", None),
            ack_data=ack_data,
            bit_timing=bit_timing,
            sent_frames=frames_sent,
            message_count=message_count,
            init_frame=init_frame,
            first_data_frame=first_data_frame,
        )
