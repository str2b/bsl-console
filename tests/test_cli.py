from unittest.mock import MagicMock
from pathlib import Path
import pytest

from bsl_console.cli import BootConsole
from bsl_console.bootloader import BootloaderClient
from bsl_console.protocol import BootloaderResponse
from bsl_console.targets import get_target
from bsl_console.protocol import normalize_boot_identifier


class FakeTransport:
    def open(self):
        pass

    def close(self):
        pass

    def drain_rx(self):
        pass


@pytest.fixture
def mock_send_passwords(monkeypatch):
    mock = MagicMock(return_value=BootloaderResponse(code=0x55, data=b"\x55"))
    monkeypatch.setattr(BootloaderClient, "send_passwords", mock)
    return mock


@pytest.fixture
def boot_console(mock_send_passwords) -> BootConsole:
    transport = FakeTransport()
    # Construct BootConsole with dummy args
    console = BootConsole(
        transport=transport,  # type: ignore
        target=get_target("tc1796"),
        command_arbitration_id=0x123,
        response_arbitration_id=0x456,
        bootstrap_init_arbitration_id=0x123,
        bootstrap_ack_id=0x0C85,
        bootstrap_data_id=0x048F,
        bootstrap_init_interval_s=1.0,
        bootstrap_post_ack_delay_s=0.01,
        bootstrap_data_interval_s=0.007,
        bootstrap_data_send_retry_count=5,
        bootstrap_data_send_retry_delay_s=0.002,
        bootloader_inter_block_delay_s=0.007,
        erase_timeout_s=30.0,
        program_verify=True,
    )
    return console


def test_unlock_with_integers(boot_console, mock_send_passwords, capsys) -> None:
    # Test valid integers unlock
    boot_console.do_unlock("0x12345678 0x9ABCDEF0")
    mock_send_passwords.assert_called_once_with(
        0x12345678, 0x9ABCDEF0, flash_bank=0, protection=0, ucb=0
    )
    captured = capsys.readouterr()
    assert "unlock: BSL_SUCCESS" in captured.out


def test_unlock_with_integers_and_options(boot_console, mock_send_passwords, capsys) -> None:
    # Test valid integers with optional params
    boot_console.do_unlock("0x12345678 0x9ABCDEF0 1 1 5")
    mock_send_passwords.assert_called_once_with(
        0x12345678, 0x9ABCDEF0, flash_bank=1, protection=1, ucb=5
    )


def test_unlock_with_insufficient_args(boot_console, mock_send_passwords, capsys) -> None:
    # Test with insufficient arguments (not a file and < 2 args)
    boot_console.do_unlock("0x12345678")
    captured = capsys.readouterr()
    assert "usage: unlock" in captured.out
    assert not mock_send_passwords.called


def test_unlock_with_invalid_integer_format(boot_console, mock_send_passwords, capsys) -> None:
    # Test with invalid format
    boot_console.do_unlock("0x12345678 invalid_int")
    captured = capsys.readouterr()
    assert "unlock failed: passwords must be valid integers" in captured.out
    assert not mock_send_passwords.called


def test_unlock_with_missing_file(boot_console, mock_send_passwords, capsys) -> None:
    # Test when argument is a non-existent file path
    boot_console.do_unlock("non_existent_file.bin")
    captured = capsys.readouterr()
    assert "unlock failed: password file 'non_existent_file.bin' not found" in captured.out
    assert not mock_send_passwords.called


def test_unlock_with_valid_file(boot_console, mock_send_passwords, tmp_path, capsys) -> None:
    # Create an 8-byte file: 0x01020304 and 0x05060708
    pw_bytes = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    pw_file = tmp_path / "password.bin"
    pw_file.write_bytes(pw_bytes)

    # Test loading from this file
    boot_console.do_unlock(pw_file.as_posix())
    mock_send_passwords.assert_called_once_with(
        0x01020304, 0x05060708, flash_bank=0, protection=0, ucb=0
    )
    captured = capsys.readouterr()
    assert "unlock: BSL_SUCCESS" in captured.out


def test_unlock_with_valid_file_and_options(boot_console, mock_send_passwords, tmp_path, capsys) -> None:
    # Create an 8-byte file: 0x01020304 and 0x05060708
    pw_bytes = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    pw_file = tmp_path / "password.bin"
    pw_file.write_bytes(pw_bytes)

    # Test loading from this file with optional params: flash_bank=1, protection=1, ucb=5
    boot_console.do_unlock(f"{pw_file.as_posix()} 1 1 5")
    mock_send_passwords.assert_called_once_with(
        0x01020304, 0x05060708, flash_bank=1, protection=1, ucb=5
    )


def test_unlock_with_invalid_file_size(boot_console, mock_send_passwords, tmp_path, capsys) -> None:
    # Create a 7-byte file
    pw_bytes = b"\x01\x02\x03\x04\x05\x06\x07"
    pw_file = tmp_path / "password.bin"
    pw_file.write_bytes(pw_bytes)

    boot_console.do_unlock(pw_file.as_posix())
    captured = capsys.readouterr()
    assert "unlock failed: password file must be exactly 8 bytes (got 7 bytes)" in captured.out
    assert not mock_send_passwords.called


def test_ping_command(boot_console, monkeypatch, capsys) -> None:
    mock_keep_alive = MagicMock(
        return_value=BootloaderResponse(code=0xDE, data=b"\xDE\xAD\xBE\xEF\xD0\x0D\xBA\xAD")
    )
    monkeypatch.setattr(BootloaderClient, "keep_alive", mock_keep_alive)

    boot_console.do_ping("")
    mock_keep_alive.assert_called_once()
    captured = capsys.readouterr()
    assert "ping: success data=de ad be ef d0 0d ba ad" in captured.out


def test_ping_command_failure(boot_console, monkeypatch, capsys) -> None:
    mock_keep_alive = MagicMock(
        return_value=BootloaderResponse(code=0xFF, data=b"\xFF")
    )
    monkeypatch.setattr(BootloaderClient, "keep_alive", mock_keep_alive)

    boot_console.do_ping("")
    mock_keep_alive.assert_called_once()
    captured = capsys.readouterr()
    assert "ping: data=ff len=1 code=BSL_BLOCK_TYPE_ERROR (0xFF)" in captured.out


def test_program_flash_srec(boot_console, monkeypatch, tmp_path, capsys) -> None:
    from hexrec import SrecFile
    srec = SrecFile.from_blocks([
        (0xA0000000, b"flash_data"),
    ])
    srec_file = tmp_path / "test.srec"
    srec.save(str(srec_file))

    mock_program_flash = MagicMock(return_value=[BootloaderResponse(code=0x55, data=b"\x55")])
    monkeypatch.setattr(BootloaderClient, "program_flash", mock_program_flash)

    # Program SREC with no filters (all blocks, address=0)
    boot_console.do_program_flash(f"0 {srec_file.as_posix()} srec")
    mock_program_flash.assert_called_once_with(
        0xA0000000, b"flash_data", verify=True, progress_cb=boot_console._progress
    )

    # Test SREC programming with address filter
    mock_program_flash.reset_mock()
    boot_console.do_program_flash(f"0xA0000000 {srec_file.as_posix()} srec 10")
    mock_program_flash.assert_called_once()
    assert mock_program_flash.call_args[0][0] == 0xA0000000
    assert mock_program_flash.call_args[0][1] == b"flash_data"


def test_program_spram_srec(boot_console, monkeypatch, tmp_path, capsys) -> None:
    from hexrec import SrecFile
    srec = SrecFile.from_blocks([
        (0xD4000000, b"spram_data"),
    ])
    srec_file = tmp_path / "test.srec"
    srec.save(str(srec_file))

    mock_program_spram = MagicMock(return_value=[BootloaderResponse(code=0x55, data=b"\x55")])
    monkeypatch.setattr(BootloaderClient, "program_spram", mock_program_spram)

    # Program SREC with no filters (all blocks, address=0)
    boot_console.do_program_spram(f"0 {srec_file.as_posix()} srec")
    mock_program_spram.assert_called_once_with(
        0xD4000000, b"spram_data", verify=True, progress_cb=boot_console._progress
    )


def test_read_uncompressed_srec(boot_console, monkeypatch, tmp_path) -> None:
    from hexrec import SrecFile
    mock_read = MagicMock(return_value=b"some_uncompressed_data")
    monkeypatch.setattr(BootloaderClient, "read_uncompressed", mock_read)

    outfile = tmp_path / "out.srec"
    boot_console.do_read_uncompressed(f"0xA0000000 22 {outfile.as_posix()} srec")

    mock_read.assert_called_once_with(0xA0000000, 22, progress_cb=boot_console._progress)
    
    assert outfile.is_file()
    srec = SrecFile.load(str(outfile))
    blocks = list(srec.memory.to_blocks())
    assert len(blocks) == 1
    assert blocks[0] == (0xA0000000, b"some_uncompressed_data")


def test_read_compressed_srec(boot_console, monkeypatch, tmp_path) -> None:
    from hexrec import SrecFile
    mock_read = MagicMock(return_value=b"some_compressed_data")
    monkeypatch.setattr(BootloaderClient, "read_compressed", mock_read)

    outfile = tmp_path / "out_comp.srec"
    boot_console.do_read_compressed(f"0xA0000000 20 {outfile.as_posix()} srec")

    mock_read.assert_called_once_with(0xA0000000, 20, progress_cb=boot_console._progress)
    
    assert outfile.is_file()
    srec = SrecFile.load(str(outfile))
    blocks = list(srec.memory.to_blocks())
    assert len(blocks) == 1
    assert blocks[0] == (0xA0000000, b"some_compressed_data")


def test_program_flash_srec_shift(boot_console, monkeypatch, tmp_path, capsys) -> None:
    from hexrec import SrecFile
    srec = SrecFile.from_blocks([
        (0x80000000, b"cached_data"),
    ])
    srec_file = tmp_path / "test_shift.srec"
    srec.save(str(srec_file))

    mock_program_flash = MagicMock(return_value=[BootloaderResponse(code=0x55, data=b"\x55")])
    monkeypatch.setattr(BootloaderClient, "program_flash", mock_program_flash)

    # Shift SREC data from 0x80000000 to 0xA0000000
    boot_console.do_program_flash(f"0xA0000000 {srec_file.as_posix()} srec 11 0x80000000")
    
    mock_program_flash.assert_called_once_with(
        0xA0000000, b"cached_data", verify=True, progress_cb=boot_console._progress
    )


def test_main_handles_keyboard_interrupt(monkeypatch) -> None:
    from bsl_console.cli import main
    # Mock BootConsole.cmdloop to raise KeyboardInterrupt
    monkeypatch.setattr(BootConsole, "cmdloop", MagicMock(side_effect=KeyboardInterrupt))
    # Run main with dummy arguments
    ret = main(["--channel", "dummy"])
    assert ret == 130


def test_main_default_channels(monkeypatch) -> None:
    from bsl_console.cli import main
    import bsl_console.cli as cli

    captured_configs = []

    # Intercept CanTransport construction to capture Config
    original_init = cli.CanTransport.__init__
    def mock_init(self, config):
        captured_configs.append(config)
        original_init(self, config)

    monkeypatch.setattr(cli.CanTransport, "__init__", mock_init)
    # Mock BootConsole.cmdloop to raise KeyboardInterrupt so we don't start the loop
    monkeypatch.setattr(cli.BootConsole, "cmdloop", MagicMock(side_effect=KeyboardInterrupt))

    # Test each interface's default channel
    interfaces_and_expected_defaults = {
        "gs_usb": 0,
        "pcan": "PCAN_USBBUS1",
        "vector": 0,
        "kvaser": 0,
        "canalystii": 0,
        "nican": "CAN0",
        "socketcan": "can0",
        "some_unknown_interface": "can0",
    }

    for interface, expected_channel in interfaces_and_expected_defaults.items():
        main(["--interface", interface])
        config = captured_configs[-1]
        assert config.interface == interface
        assert config.channel == expected_channel


def test_main_uses_target_bootstrap_init_id_by_default(monkeypatch) -> None:
    from bsl_console.cli import main
    import bsl_console.cli as cli

    captured_boot_console_kwargs = {}

    def mock_boot_console_init(self, transport, **kwargs):
        captured_boot_console_kwargs.update(kwargs)

    monkeypatch.setattr(cli.BootConsole, "__init__", mock_boot_console_init)
    monkeypatch.setattr(cli.BootConsole, "cmdloop", MagicMock(side_effect=KeyboardInterrupt))

    ret = main(["--target", "tc1796", "--interface", "socketcan", "--channel", "can0"])
    assert ret == 130
    assert captured_boot_console_kwargs["bootstrap_init_arbitration_id"] == normalize_boot_identifier(get_target("tc1796").bootstrap_init_id)


def test_main_respects_bootstrap_init_id_override(monkeypatch) -> None:
    from bsl_console.cli import main
    import bsl_console.cli as cli

    captured_boot_console_kwargs = {}

    def mock_boot_console_init(self, transport, **kwargs):
        captured_boot_console_kwargs.update(kwargs)

    monkeypatch.setattr(cli.BootConsole, "__init__", mock_boot_console_init)
    monkeypatch.setattr(cli.BootConsole, "cmdloop", MagicMock(side_effect=KeyboardInterrupt))

    ret = main([
        "--target",
        "tc1796",
        "--interface",
        "socketcan",
        "--channel",
        "can0",
        "--bootstrap-init-id",
        "0x321",
    ])
    assert ret == 130
    assert captured_boot_console_kwargs["bootstrap_init_arbitration_id"] == 0x321







