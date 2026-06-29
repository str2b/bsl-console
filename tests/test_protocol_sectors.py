import pytest
from bsl_console.targets import resolve_tc1796_flash_sector as resolve_flash_sector


def test_resolve_flash_sector_pflash0() -> None:
    # PFlash0 small sectors (< 0xA0020000)
    assert resolve_flash_sector(0xA0000000) == (0xA0000000, 0x4000)
    assert resolve_flash_sector(0xA0003FFF) == (0xA0000000, 0x4000)
    assert resolve_flash_sector(0xA001FFFF) == (0xA001C000, 0x4000)

    # PFlash0 medium sectors (< 0xA0040000)
    assert resolve_flash_sector(0xA0020000) == (0xA0020000, 0x20000)
    assert resolve_flash_sector(0xA003FFFF) == (0xA0020000, 0x20000)

    # PFlash0 large sectors (< 0xA0200000)
    assert resolve_flash_sector(0xA0040000) == (0xA0040000, 0x40000)
    assert resolve_flash_sector(0xA01FFFFF) == (0xA01C0000, 0x40000)


def test_resolve_flash_sector_pflash1() -> None:
    # PFlash1 small sectors (< 0xA0220000)
    assert resolve_flash_sector(0xA0200000) == (0xA0200000, 0x4000)
    assert resolve_flash_sector(0xA021FFFF) == (0xA021C000, 0x4000)

    # PFlash1 medium sectors (< 0xA0240000)
    assert resolve_flash_sector(0xA0220000) == (0xA0220000, 0x20000)
    assert resolve_flash_sector(0xA023FFFF) == (0xA0220000, 0x20000)

    # PFlash1 large sectors (< 0xA0400000)
    assert resolve_flash_sector(0xA0240000) == (0xA0240000, 0x40000)
    assert resolve_flash_sector(0xA03FFFFF) == (0xA03C0000, 0x40000)


def test_resolve_flash_sector_dflash() -> None:
    # DFlash bank 0
    assert resolve_flash_sector(0xAFE00000) == (0xAFE00000, 0x8000)
    assert resolve_flash_sector(0xAFE07FFF) == (0xAFE00000, 0x8000)

    # DFlash bank 1
    assert resolve_flash_sector(0xAFE10000) == (0xAFE10000, 0x8000)
    assert resolve_flash_sector(0xAFE17FFF) == (0xAFE10000, 0x8000)


def test_resolve_flash_sector_invalid() -> None:
    with pytest.raises(ValueError, match="is not in an erasable sector range"):
        resolve_flash_sector(0x9FFFFFFF)

    with pytest.raises(ValueError, match="is not in an erasable sector range"):
        resolve_flash_sector(0xA0400000)

    with pytest.raises(ValueError, match="is not in an erasable sector range"):
        resolve_flash_sector(0xAFDF0000)

    with pytest.raises(ValueError, match="is not in an erasable sector range"):
        resolve_flash_sector(0xAFE08000)

    with pytest.raises(ValueError, match="is not in an erasable sector range"):
        resolve_flash_sector(0xAFE18000)


def test_resolve_flash_sector_tc1766() -> None:
    from bsl_console.targets import resolve_tc1766_flash_sector
    
    # 8 sectors of 16 KB
    assert resolve_tc1766_flash_sector(0xA0000000) == (0xA0000000, 0x4000)
    assert resolve_tc1766_flash_sector(0xA001FFFF) == (0xA001C000, 0x4000)
    
    # 1 sector of 128 KB
    assert resolve_tc1766_flash_sector(0xA0020000) == (0xA0020000, 0x20000)
    assert resolve_tc1766_flash_sector(0xA003FFFF) == (0xA0020000, 0x20000)
    
    # 1 sector of 256 KB
    assert resolve_tc1766_flash_sector(0xA0040000) == (0xA0040000, 0x40000)
    assert resolve_tc1766_flash_sector(0xA007FFFF) == (0xA0040000, 0x40000)
    
    # 1 sector of 512 KB
    assert resolve_tc1766_flash_sector(0xA0080000) == (0xA0080000, 0x80000)
    assert resolve_tc1766_flash_sector(0xA00FFFFF) == (0xA0080000, 0x80000)

    # 1 sector of 512 KB (up to 0xA017FFFF)
    assert resolve_tc1766_flash_sector(0xA0100000) == (0xA0100000, 0x80000)
    assert resolve_tc1766_flash_sector(0xA017FFFF) == (0xA0100000, 0x80000)

    # DFlash sectors (2 sectors of 16 KB each)
    assert resolve_tc1766_flash_sector(0xAFE00000) == (0xAFE00000, 0x4000)
    assert resolve_tc1766_flash_sector(0xAFE10000) == (0xAFE10000, 0x4000)

    # Invalid check
    with pytest.raises(ValueError, match="is not in an erasable sector range"):
        resolve_tc1766_flash_sector(0xA0180000)

