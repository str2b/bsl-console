from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class Target:
    name: str
    flash_entry_address: int
    spram_entry_address: int
    bootstrap_init_id: int
    bootstrap_ack_id: int
    bootstrap_data_id: int
    resolve_flash_sector: Callable[[int], tuple[int, int]]

def resolve_tc1796_flash_sector(address: int) -> tuple[int, int]:
    addr = int(address)

    # PFlash0: 0xA0000000..0xA01FFFFF
    if 0xA0000000 <= addr < 0xA0200000:
        if addr < 0xA0020000:
            size = 0x4000
        elif addr < 0xA0040000:
            size = 0x20000
        else:
            size = 0x40000
        start = addr - (addr % size)
        return start, size

    # PFlash1: 0xA0200000..0xA03FFFFF
    if 0xA0200000 <= addr < 0xA0400000:
        if addr < 0xA0220000:
            size = 0x4000
        elif addr < 0xA0240000:
            size = 0x20000
        else:
            size = 0x40000
        start = addr - (addr % size)
        return start, size

    # DFlash sectors accepted by firmware erase path.
    if 0xAFE00000 <= addr < 0xAFE08000:
        return 0xAFE00000, 0x8000
    if 0xAFE10000 <= addr < 0xAFE18000:
        return 0xAFE10000, 0x8000

    raise ValueError(f"address 0x{addr:08X} is not in an erasable sector range")

def resolve_tc1766_flash_sector(address: int) -> tuple[int, int]:
    addr = int(address)

    # PFlash: 0xA0000000..0xA017FFFF
    if 0xA0000000 <= addr < 0xA0180000:
        if addr < 0xA0020000:
            size = 0x4000
        elif addr < 0xA0040000:
            size = 0x20000
        elif addr < 0xA0080000:
            size = 0x40000
        else:
            size = 0x80000
        start = addr - (addr % size)
        return start, size

    # DFlash: 32 KB (two 16 KB sectors/banks)
    if 0xAFE00000 <= addr < 0xAFE04000:
        return 0xAFE00000, 0x4000
    if 0xAFE10000 <= addr < 0xAFE14000:
        return 0xAFE10000, 0x4000

    raise ValueError(f"address 0x{addr:08X} is not in an erasable sector range")


TC1796 = Target(
    name="tc1796",
    flash_entry_address=0xA0000000,
    spram_entry_address=0xD4001400,
    bootstrap_init_id=0x123,
    bootstrap_ack_id=0x0C85,
    bootstrap_data_id=0x048F,
    resolve_flash_sector=resolve_tc1796_flash_sector,
)

TC1792 = Target(
    name="tc1792",
    flash_entry_address=0xA0000000,
    spram_entry_address=0xD4001400,
    bootstrap_init_id=0x123,
    bootstrap_ack_id=0x0C85,
    bootstrap_data_id=0x048F,
    resolve_flash_sector=resolve_tc1796_flash_sector,
)

TC1766 = Target(
    name="tc1766",
    flash_entry_address=0xA0000000,
    spram_entry_address=0xD4001400,
    bootstrap_init_id=0x123,
    bootstrap_ack_id=0x0C85,
    bootstrap_data_id=0x048F,
    resolve_flash_sector=resolve_tc1766_flash_sector,
)

TARGETS: dict[str, Target] = {
    "tc1796": TC1796,
    "tc1792": TC1792,
    "tc1766": TC1766,
}


def get_target(name: str) -> Target:
    target = TARGETS.get(name.lower())
    if target is None:
        raise ValueError(f"unknown target '{name}', available targets: {list(TARGETS.keys())}")
    return target

