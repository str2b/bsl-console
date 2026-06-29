from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


BLOCK_TYPE_HEADER = 0x00
BLOCK_TYPE_DATA = 0x01
BLOCK_TYPE_EOT = 0x02

HEADER_BLOCK_SIZE = 16
DATA_BLOCK_SIZE = 264
DATA_PAYLOAD_SIZE = 256
DATA_PADDING_SIZE = 5


def checksum_xor(payload: bytes | bytearray | memoryview) -> int:
    value = 0
    for byte in payload:
        value ^= byte
    return value


def u16_le(value: int) -> bytes:
    return int(value).to_bytes(2, "little", signed=False)


def u32_be(value: int) -> bytes:
    return int(value).to_bytes(4, "big", signed=False)


def normalize_boot_identifier(identifier_word: int) -> int:
    """Convert the 16-bit bootstrap word into the 11-bit CAN identifier.

    The TC1796 stores only the upper 11 effective bits after discarding the
    upper 3 bits of the 16-bit word and right-shifting by 2.
    """

    return (int(identifier_word) >> 2) & 0x7FF





class BootloaderCommand(IntEnum):
    PROGRAM_FLASH = 0x00
    RUN_FROM_FLASH = 0x01
    PROGRAM_SPRAM = 0x02
    RUN_FROM_SPRAM = 0x03
    ERASE_FLASH = 0x04
    PROTECT_FLASH = 0x06
    READ_CMPRSSD = 0x07
    READ_MEM32 = 0x08
    READ_UNCMPRSSD = 0x0A
    SEND_PSSWD = 0x10
    KEEP_ALIVE = 0x3E


@dataclass(slots=True)
class BootloaderBlock:
    block_type: int

    def to_bytes(self) -> bytes:
        raise NotImplementedError


@dataclass(slots=True)
class HeaderBlock(BootloaderBlock):
    command: int
    address: int = 0
    size: int = 0
    param0: int = 0
    param1: int = 0
    param2: int = 0
    param3: int = 0
    param4: int = 0

    def __init__(self, command: int, address: int = 0, size: int = 0, *, param0: int = 0, param1: int = 0, param2: int = 0, param3: int = 0, param4: int = 0) -> None:
        object.__setattr__(self, "block_type", BLOCK_TYPE_HEADER)
        object.__setattr__(self, "command", int(command))
        object.__setattr__(self, "address", int(address))
        object.__setattr__(self, "size", int(size))
        object.__setattr__(self, "param0", int(param0))
        object.__setattr__(self, "param1", int(param1))
        object.__setattr__(self, "param2", int(param2))
        object.__setattr__(self, "param3", int(param3))
        object.__setattr__(self, "param4", int(param4))

    def to_bytes(self) -> bytes:
        payload = bytearray(HEADER_BLOCK_SIZE)
        payload[0] = self.block_type
        payload[1] = self.command & 0xFF
        payload[2:6] = u32_be(self.address)
        payload[6:10] = u32_be(self.size)
        payload[10] = self.param0 & 0xFF
        payload[11] = self.param1 & 0xFF
        payload[12] = self.param2 & 0xFF
        payload[13] = self.param3 & 0xFF
        payload[14] = self.param4 & 0xFF
        payload[15] = checksum_xor(payload[1:15])
        return bytes(payload)


@dataclass(slots=True)
class DataBlock(BootloaderBlock):
    payload: bytes
    verify: bool = True

    def __init__(self, payload: bytes | bytearray | memoryview, verify: bool = True) -> None:
        payload_bytes = bytes(payload)
        if len(payload_bytes) > DATA_PAYLOAD_SIZE:
            raise ValueError(f"payload must be at most {DATA_PAYLOAD_SIZE} bytes")
        if len(payload_bytes) < DATA_PAYLOAD_SIZE:
            payload_bytes = payload_bytes + b"\x00" * (DATA_PAYLOAD_SIZE - len(payload_bytes))
        object.__setattr__(self, "block_type", BLOCK_TYPE_DATA)
        object.__setattr__(self, "payload", payload_bytes)
        object.__setattr__(self, "verify", bool(verify))

    def to_bytes(self) -> bytes:
        frame = bytearray(DATA_BLOCK_SIZE)
        frame[0] = self.block_type
        frame[1] = 0x01 if self.verify else 0x00
        frame[2:258] = self.payload
        frame[258:263] = b"\x00" * DATA_PADDING_SIZE
        frame[263] = checksum_xor(frame[1:263])
        return bytes(frame)


@dataclass(slots=True)
class EotBlock(BootloaderBlock):
    def __init__(self) -> None:
        object.__setattr__(self, "block_type", BLOCK_TYPE_EOT)

    def to_bytes(self) -> bytes:
        frame = bytearray(HEADER_BLOCK_SIZE)
        frame[0] = self.block_type
        frame[15] = checksum_xor(frame[1:15])
        return bytes(frame)


@dataclass(slots=True)
class BootloaderResponse:
    code: int
    data: bytes = b""
