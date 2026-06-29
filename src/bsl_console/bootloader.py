from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from time import monotonic, sleep
from typing import Callable

from .protocol import BootloaderCommand, BootloaderResponse, DataBlock, EotBlock, HeaderBlock
from .transport import CanTransport


READ_COMPRESSED_INPUT_BLOCK_SIZE = 4096


@dataclass(slots=True)
class BootloaderClient:
    transport: CanTransport
    command_arbitration_id: int
    response_arbitration_id: int | None = None
    response_timeout_s: float = 0.5
    inter_block_delay_s: float = 0.0
    inter_frame_delay_s: float = 0.0

    def _send_header(self, header: HeaderBlock) -> None:
        # Drop stale frames (for example bootstrap or echoed frames) so the
        # following response wait corresponds to this command only.
        self.transport.drain_rx()
        frame = header.to_bytes()
        self.transport.send(self.command_arbitration_id, frame[:8])
        if self.inter_frame_delay_s > 0:
            sleep(self.inter_frame_delay_s)
        self.transport.send(self.command_arbitration_id, frame[8:16])

    def _send_block(self, block: DataBlock | EotBlock) -> None:
        frame = block.to_bytes()
        for i, offset in enumerate(range(0, len(frame), 8)):
            if i > 0 and self.inter_frame_delay_s > 0:
                sleep(self.inter_frame_delay_s)
            self.transport.send(self.command_arbitration_id, frame[offset : offset + 8])

    def _await_response(self) -> BootloaderResponse:
        deadline = monotonic() + self.response_timeout_s
        while monotonic() < deadline:
            timeout_s = max(0.0, deadline - monotonic())
            message = self.transport.recv(timeout_s)
            if message is None:
                continue
            if getattr(message, "is_error_frame", False):
                continue

            arbitration_id = getattr(message, "arbitration_id", None)
            if arbitration_id == self.command_arbitration_id:
                continue
            if self.response_arbitration_id is not None and arbitration_id != self.response_arbitration_id:
                continue

            payload = bytes(getattr(message, "data", b""))
            return BootloaderResponse(code=payload[0] if payload else 0x00, data=payload)

        raise TimeoutError("no bootloader response received")

    def _await_stream_frame(self, expected_command: int) -> bytes:
        response = self._await_response()
        payload = response.data
        if len(payload) != 8:
            raise RuntimeError(f"invalid stream frame size: expected 8, got {len(payload)}")
        if payload[0] != (expected_command & 0xFF):
            raise RuntimeError(
                f"unexpected stream command byte 0x{payload[0]:02X}, expected 0x{expected_command & 0xFF:02X}"
            )
        return payload

    def _send_stream_ack(self, command: int) -> None:
        self.transport.send(self.command_arbitration_id, bytes([command & 0xFF, 0xAC, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))

    def run_from_flash(self) -> BootloaderResponse:
        self._send_header(HeaderBlock(BootloaderCommand.RUN_FROM_FLASH))
        return self._await_response()

    def run_from_spram(self) -> BootloaderResponse:
        self._send_header(HeaderBlock(BootloaderCommand.RUN_FROM_SPRAM))
        return self._await_response()

    def protect_flash(self, password0: int, password1: int, *, flash_bank: int = 0, sector_mask: int = 0) -> BootloaderResponse:
        header = HeaderBlock(
            BootloaderCommand.PROTECT_FLASH,
            address=password0,
            size=password1,
            param0=flash_bank & 0xFF,
            param1=(sector_mask >> 8) & 0xFF,
            param2=sector_mask & 0xFF,
        )
        self._send_header(header)
        return self._await_response()

    def read_uncompressed(
        self,
        address: int,
        size: int,
        *,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> bytes:
        if size < 0:
            raise ValueError("size must be non-negative")

        self._send_header(HeaderBlock(BootloaderCommand.READ_UNCMPRSSD, address=address, size=size))
        _ = self._await_stream_frame(BootloaderCommand.READ_UNCMPRSSD)

        output = bytearray()
        remaining = int(size)
        if progress_cb is not None:
            progress_cb(0, int(size), "read_uncompressed")

        while remaining > 0:
            meta = self._await_stream_frame(BootloaderCommand.READ_UNCMPRSSD)
            block_size = int.from_bytes(meta[5:8], "big", signed=False)
            if block_size <= 0:
                raise RuntimeError(f"invalid uncompressed block size: {block_size}")

            frame_count = (block_size + 5) // 6
            block = bytearray()
            expected_seq = 1
            for _ in range(frame_count):
                frame = self._await_stream_frame(BootloaderCommand.READ_UNCMPRSSD)
                if frame[1] != (expected_seq & 0xFF):
                    raise RuntimeError(
                        f"unexpected uncompressed sequence value: got {frame[1]}, expected {expected_seq & 0xFF}"
                    )
                block.extend(frame[2:8])
                expected_seq += 1

            # Firmware MAIN_waitAckOrTimeout currently checks for 0x07 (compressed)
            # in both compressed and uncompressed read paths.
            self._send_stream_ack(BootloaderCommand.READ_CMPRSSD)
            status = self._await_response()
            if status.code != 0x55:
                raise RuntimeError(f"uncompressed read failed after ACK with status 0x{status.code:02X}")

            used = min(remaining, block_size)
            output.extend(block[:used])
            remaining -= used
            if progress_cb is not None:
                progress_cb(len(output), int(size), "read_uncompressed")

        return bytes(output)

    def read_compressed(
        self,
        address: int,
        size: int,
        *,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> bytes:
        if size < 0:
            raise ValueError("size must be non-negative")

        try:
            import lz4.block as lz4_block
        except Exception as exc:
            raise RuntimeError("compressed read requires the Python package 'lz4'") from exc

        self._send_header(HeaderBlock(BootloaderCommand.READ_CMPRSSD, address=address, size=size))
        _ = self._await_stream_frame(BootloaderCommand.READ_CMPRSSD)

        output = bytearray()
        remaining = int(size)
        base_address = int(address)
        if progress_cb is not None:
            progress_cb(0, int(size), "read_compressed")

        while remaining > 0:
            meta = self._await_stream_frame(BootloaderCommand.READ_CMPRSSD)
            block_address = int.from_bytes(meta[1:5], "big", signed=False)
            compressed_size = int.from_bytes(meta[5:8], "big", signed=False)
            if compressed_size <= 0:
                raise RuntimeError(f"invalid compressed block size: {compressed_size}")

            frame_count = (compressed_size + 5) // 6
            compressed_block = bytearray()
            expected_seq = 1
            for _ in range(frame_count):
                frame = self._await_stream_frame(BootloaderCommand.READ_CMPRSSD)
                if frame[1] != (expected_seq & 0xFF):
                    raise RuntimeError(
                        f"unexpected compressed sequence value: got {frame[1]}, expected {expected_seq & 0xFF}"
                    )
                compressed_block.extend(frame[2:8])
                expected_seq += 1
            compressed_payload = bytes(compressed_block[:compressed_size])

            self._send_stream_ack(BootloaderCommand.READ_CMPRSSD)
            status = self._await_response()
            if status.code != 0x55:
                raise RuntimeError(f"compressed read failed after ACK with status 0x{status.code:02X}")

            relative = max(0, block_address - base_address)
            expected_uncompressed_size = min(READ_COMPRESSED_INPUT_BLOCK_SIZE, max(0, int(size) - relative))
            if expected_uncompressed_size <= 0:
                break

            decompressed = lz4_block.decompress(compressed_payload, uncompressed_size=expected_uncompressed_size)
            used = min(remaining, len(decompressed), expected_uncompressed_size)
            output.extend(decompressed[:used])
            remaining -= used
            if progress_cb is not None:
                progress_cb(len(output), int(size), "read_compressed")

        return bytes(output)

    def keep_alive(self) -> BootloaderResponse:
        self._send_header(HeaderBlock(BootloaderCommand.KEEP_ALIVE))
        return self._await_response()

    def read_mem32(self, address: int) -> BootloaderResponse:
        self._send_header(HeaderBlock(BootloaderCommand.READ_MEM32, address=address))
        return self._await_response()

    def erase_flash(self, address: int, size: int) -> BootloaderResponse:
        self._send_header(HeaderBlock(BootloaderCommand.ERASE_FLASH, address=address, size=size))
        return self._await_response()

    def send_passwords(self, password0: int, password1: int, *, flash_bank: int = 0, protection: int = 0, ucb: int = 0) -> BootloaderResponse:
        header = HeaderBlock(
            BootloaderCommand.SEND_PSSWD,
            address=password0,
            size=password1,
            param0=flash_bank & 0xFF,
            param1=protection & 0xFF,
            param2=ucb & 0xFF,
        )
        self._send_header(header)
        return self._await_response()

    def program_spram(
        self,
        address: int,
        payload: bytes | bytearray | memoryview,
        *,
        verify: bool = True,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> list[BootloaderResponse]:
        self._send_header(HeaderBlock(BootloaderCommand.PROGRAM_SPRAM, address=address))
        total_steps = ceil(len(payload) / 256) + 2
        replies = [self._await_response()]
        if progress_cb is not None:
            progress_cb(1, total_steps, "program_spram")
        step = 1
        for offset in range(0, len(payload), 256):
            self._send_block(DataBlock(payload[offset : offset + 256], verify=verify))
            replies.append(self._await_response())
            step += 1
            if progress_cb is not None:
                progress_cb(step, total_steps, "program_spram")
            if self.inter_block_delay_s > 0:
                sleep(self.inter_block_delay_s)
        self._send_block(EotBlock())
        replies.append(self._await_response())
        if progress_cb is not None:
            progress_cb(total_steps, total_steps, "program_spram")
        return replies

    def program_flash(
        self,
        address: int,
        payload: bytes | bytearray | memoryview,
        *,
        verify: bool = True,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> list[BootloaderResponse]:
        self._send_header(HeaderBlock(BootloaderCommand.PROGRAM_FLASH, address=address))
        total_steps = ceil(len(payload) / 256) + 2
        replies = [self._await_response()]
        if progress_cb is not None:
            progress_cb(1, total_steps, "program_flash")
        step = 1
        for offset in range(0, len(payload), 256):
            self._send_block(DataBlock(payload[offset : offset + 256], verify=verify))
            replies.append(self._await_response())
            step += 1
            if progress_cb is not None:
                progress_cb(step, total_steps, "program_flash")
            if self.inter_block_delay_s > 0:
                sleep(self.inter_block_delay_s)
        self._send_block(EotBlock())
        replies.append(self._await_response())
        if progress_cb is not None:
            progress_cb(total_steps, total_steps, "program_flash")
        return replies
