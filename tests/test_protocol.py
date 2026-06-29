from bsl_console.protocol import DataBlock, EotBlock, HeaderBlock, checksum_xor


def test_checksum_xor() -> None:
    assert checksum_xor(b"\x01\x02\x03") == 0x00


def test_header_block_layout() -> None:
    block = HeaderBlock(0x10, address=0xAABBCCDD, size=0x11223344, param0=0x55, param1=0x66, param2=0x77)
    raw = block.to_bytes()
    assert len(raw) == 16
    assert raw[0] == 0x00
    assert raw[1] == 0x10
    assert raw[2:6] == b"\xAA\xBB\xCC\xDD"
    assert raw[6:10] == b"\x11\x22\x33\x44"
    assert raw[15] == checksum_xor(raw[1:15])


def test_data_block_layout() -> None:
    payload = bytes(range(256))
    block = DataBlock(payload, verify=True)
    raw = block.to_bytes()
    assert len(raw) == 264
    assert raw[0] == 0x01
    assert raw[1] == 0x01
    assert raw[2:258] == payload
    assert raw[258:263] == b"\x00" * 5
    assert raw[263] == checksum_xor(raw[1:263])


def test_eot_block_layout() -> None:
    raw = EotBlock().to_bytes()
    assert len(raw) == 16
    assert raw[0] == 0x02
    assert raw[15] == checksum_xor(raw[1:15])
