from __future__ import annotations

import argparse
import cmd
import shlex
import sys
from pathlib import Path
from typing import Callable

from .bootstrap import BootstrapConfig, BootstrapTransferClient
from .bootloader import BootloaderClient
from .protocol import (
    BootloaderResponse,
    normalize_boot_identifier,
)
from .targets import Target, get_target, TARGETS
from .transport import CanTransport, CanTransportConfig


BOOTLOADER_STATUS_CODES: dict[int, str] = {
    0x55: "BSL_SUCCESS",
    0xFF: "BSL_BLOCK_TYPE_ERROR",
    0xFE: "BSL_MODE_ERROR",
    0xFD: "BSL_CHKSUM_ERROR",
    0xFC: "BSL_ADDRESS_ERROR",
    0xFB: "BSL_ERASE_ERROR",
    0xFA: "BSL_PROGRAM_ERROR",
    0xF9: "BSL_VERIFICATION_ERROR",
    0xF8: "BSL_PROTECTION_ERROR",
    0xF7: "BSL_TIMEOUT_ERROR",
}


def _parse_int_arg(argv: list[str], index: int, default: int, name: str) -> int:
    if len(argv) <= index:
        return default
    try:
        return int(argv[index], 0)
    except ValueError as exc:
        raise ValueError(f"invalid integer value for {name}: '{argv[index]}'") from exc


def parse_unlock_arguments(argv: list[str]) -> tuple[int, int, int, int, int]:
    """Parse unlock subcommand arguments.

    Returns:
        tuple: (password0, password1, flash_bank, protection, ucb)

    Raises:
        ValueError: with a clear user-facing error message.
    """
    if not argv:
        raise ValueError("USAGE_ERROR")

    first_arg = argv[0]

    # Try reading as a file if it exists
    is_file = False
    try:
        if Path(first_arg).is_file():
            is_file = True
    except Exception:
        pass

    if is_file:
        try:
            data = Path(first_arg).read_bytes()
        except Exception as exc:
            raise ValueError(f"cannot read password file: {exc}") from exc

        if len(data) != 8:
            raise ValueError(
                f"password file must be exactly 8 bytes (got {len(data)} bytes)"
            )

        p0 = int.from_bytes(data[0:4], "big")
        p1 = int.from_bytes(data[4:8], "big")

        flash_bank = _parse_int_arg(argv, 1, 0, "flash_bank")
        protection = _parse_int_arg(argv, 2, 0, "protection")
        ucb = _parse_int_arg(argv, 3, 0, "ucb")
        return p0, p1, flash_bank, protection, ucb

    # If not a file, it must be raw integers.
    if len(argv) == 1:
        try:
            int(first_arg, 0)
            raise ValueError("USAGE_ERROR")
        except ValueError as exc:
            if str(exc) == "USAGE_ERROR":
                raise
            raise ValueError(f"password file '{first_arg}' not found") from exc

    if len(argv) < 2:
        raise ValueError("USAGE_ERROR")

    try:
        p0 = int(first_arg, 0)
        p1 = int(argv[1], 0)
    except ValueError as exc:
        if not first_arg.startswith(("0x", "0b")) and not first_arg.isdigit():
            raise ValueError(
                f"password file '{first_arg}' not found or invalid integer format"
            ) from exc
        raise ValueError(
            "passwords must be valid integers or a valid path to an 8-byte file"
        ) from exc

    flash_bank = _parse_int_arg(argv, 2, 0, "flash_bank")
    protection = _parse_int_arg(argv, 3, 0, "protection")
    ucb = _parse_int_arg(argv, 4, 0, "ucb")

    return p0, p1, flash_bank, protection, ucb


class BootConsole(cmd.Cmd):
    intro = "TC1796 BSL boot console. Type help or ? to list commands."
    prompt = "bsl> "

    def __init__(
        self,
        transport: CanTransport,
        *,
        target: Target,
        command_arbitration_id: int,
        response_arbitration_id: int | None,
        bootstrap_init_arbitration_id: int,
        bootstrap_ack_id: int,
        bootstrap_data_id: int,
        bootstrap_init_interval_s: float,
        bootstrap_post_ack_delay_s: float,
        bootstrap_data_interval_s: float,
        bootstrap_data_send_retry_count: int,
        bootstrap_data_send_retry_delay_s: float,
        bootloader_inter_block_delay_s: float,
        bootloader_inter_frame_delay_s: float = 0.0,
        erase_timeout_s: float,
        program_verify: bool,
    ) -> None:
        super().__init__()
        self.transport = transport
        self.target = target
        self.bootstrap_client = BootstrapTransferClient(
            transport,
            BootstrapConfig(
                ack_id=bootstrap_ack_id,
                data_id=bootstrap_data_id,
                message_count=0,
                init_arbitration_id=bootstrap_init_arbitration_id,
                init_interval_s=bootstrap_init_interval_s,
                post_ack_delay_s=bootstrap_post_ack_delay_s,
                data_interval_s=bootstrap_data_interval_s,
                data_send_retry_count=bootstrap_data_send_retry_count,
                data_send_retry_delay_s=bootstrap_data_send_retry_delay_s,
            ),
        )
        self.bootloader = BootloaderClient(
            transport,
            command_arbitration_id=command_arbitration_id,
            response_arbitration_id=response_arbitration_id,
            inter_block_delay_s=bootloader_inter_block_delay_s,
            inter_frame_delay_s=bootloader_inter_frame_delay_s,
        )
        self.erase_timeout_s = max(0.0, float(erase_timeout_s))
        self.program_verify = bool(program_verify)
        self._last_progress_line_len = 0

    def _erase_with_timeout(self, address: int, size: int) -> BootloaderResponse:
        prev_timeout = self.bootloader.response_timeout_s
        self.bootloader.response_timeout_s = max(prev_timeout, self.erase_timeout_s)
        try:
            return self.bootloader.erase_flash(address, size)
        finally:
            self.bootloader.response_timeout_s = prev_timeout

    def _progress(self, current: int, total: int, label: str) -> None:
        if total <= 0:
            return
        ratio = max(0.0, min(1.0, float(current) / float(total)))
        width = 24
        filled = int(ratio * width)
        bar = "#" * filled + "-" * (width - filled)
        percent = int(ratio * 100)
        line = f"{label}: [{bar}] {percent:3d}% ({current}/{total})"
        pad = " " * max(0, self._last_progress_line_len - len(line))
        sys.stdout.write("\r" + line + pad)
        sys.stdout.flush()
        self._last_progress_line_len = len(line)

    def _progress_done(self) -> None:
        if self._last_progress_line_len > 0:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._last_progress_line_len = 0

    def _print_bootloader_response(
        self, command_name: str, response: BootloaderResponse, *, expect_status: bool
    ) -> None:
        code = int(response.code) & 0xFF
        status_name = BOOTLOADER_STATUS_CODES.get(code)
        code_text = f"0x{code:02X}"
        if status_name is not None:
            code_text = f"{status_name} ({code_text})"
        if expect_status:
            print(f"{command_name}: {code_text}")
        else:
            payload = response.data.hex(" ")
            print(
                f"{command_name}: data={payload} len={len(response.data)} code={code_text}"
            )

    def _print_bootloader_responses(
        self, command_name: str, responses: list[BootloaderResponse]
    ) -> None:
        success = 0
        error = 0
        unknown = 0
        status_counts: dict[str, int] = {}
        for response in responses:
            code = int(response.code) & 0xFF
            name = BOOTLOADER_STATUS_CODES.get(code)
            if name == "BSL_SUCCESS":
                success += 1
            elif name is None:
                unknown += 1
            else:
                error += 1
            status_label = name if name is not None else f"UNKNOWN_0x{code:02X}"
            status_counts[status_label] = status_counts.get(status_label, 0) + 1

        breakdown = " ".join(
            f"{name}={count}" for name, count in sorted(status_counts.items())
        )
        if breakdown:
            print(
                f"{command_name}: success={success} error={error} unknown={unknown} {breakdown}"
            )
        else:
            print(f"{command_name}: success={success} error={error} unknown={unknown}")

    def _load_hexrec(self, command_name: str):
        try:
            from hexrec import SrecFile
        except ImportError as exc:
            print(f"{command_name} failed: hexrec package not found. {exc}")
            return None
        return SrecFile

    def _write_read_payload(
        self,
        command_name: str,
        address: int,
        payload: bytes,
        outfile: Path | None,
        fmt: str,
    ) -> None:
        if outfile is None:
            print(f"{command_name}: {len(payload)} bytes")
            return

        if fmt == "srec":
            SrecFile = self._load_hexrec(command_name)
            if SrecFile is None:
                return
            srec = SrecFile.from_blocks([(address, payload)])
            srec.save(str(outfile))
        else:
            outfile.write_bytes(payload)
        print(f"{command_name}: {len(payload)} bytes -> {outfile}")

    def _execute_read_command(
        self,
        command_name: str,
        arg: str,
        usage: str,
        reader: Callable[..., bytes],
    ) -> None:
        argv = shlex.split(arg)
        if len(argv) < 2:
            print(usage)
            return

        address = int(argv[0], 0)
        size = int(argv[1], 0)
        outfile = Path(argv[2]) if len(argv) >= 3 else None
        fmt = argv[3].lower() if len(argv) >= 4 else "bin"

        try:
            payload = reader(address, size, progress_cb=self._progress)
            self._progress_done()
            self._write_read_payload(command_name, address, payload, outfile, fmt)
        except Exception as exc:
            self._progress_done()
            print(f"{command_name} failed: {exc}")

    def _load_program_blocks(
        self,
        command_name: str,
        address: int,
        file_path: Path,
        fmt: str,
        size: int,
        origin_address: int | None,
    ) -> list[tuple[int, bytes]] | None:
        if fmt != "srec":
            return [(address, file_path.read_bytes())]

        if not file_path.is_file():
            print(f"{command_name} failed: file '{file_path}' not found")
            return None

        SrecFile = self._load_hexrec(command_name)
        if SrecFile is None:
            return None

        srec = SrecFile.load(str(file_path))
        crop_size = max(0, int(size))
        if address != 0:
            if origin_address is not None:
                if crop_size > 0:
                    srec.memory.crop(origin_address, origin_address + crop_size)
            else:
                if crop_size > 0:
                    srec.memory.crop(address, address + crop_size)
                else:
                    crop_size = srec.memory.content_endex - address
                    srec.memory.crop(address, address + crop_size)

        blocks = list(srec.memory.to_blocks())
        if not blocks:
            print(f"{command_name}: no SREC data to program after cropping/filtering")
            return None

        if address != 0 and origin_address is not None:
            offset = address - origin_address
            blocks = [(addr + offset, data) for addr, data in blocks]

        return [(int(addr), bytes(data)) for addr, data in blocks]

    def _execute_program_command(
        self,
        command_name: str,
        destination_label: str,
        arg: str,
        programmer: Callable[..., list[BootloaderResponse]],
    ) -> None:
        argv = shlex.split(arg)
        if len(argv) < 2:
            print(
                f"usage: {command_name} <address> <file> [format] [size] [origin_address]"
            )
            return

        address = int(argv[0], 0)
        file_path = Path(argv[1])
        fmt = argv[2].lower() if len(argv) >= 3 else "bin"
        size = int(argv[3], 0) if len(argv) >= 4 else 0
        origin_address = int(argv[4], 0) if len(argv) >= 5 else None

        try:
            blocks = self._load_program_blocks(
                command_name, address, file_path, fmt, size, origin_address
            )
            if not blocks:
                return

            show_address = fmt == "srec"
            for block_address, data in blocks:
                if show_address:
                    print(
                        f"Programming {len(data)} bytes to 0x{block_address:08X} ({destination_label})..."
                    )
                responses = programmer(
                    block_address,
                    data,
                    verify=self.program_verify,
                    progress_cb=self._progress,
                )
                self._progress_done()
                response_name = (
                    f"{command_name} (0x{block_address:08X})"
                    if show_address
                    else command_name
                )
                self._print_bootloader_responses(response_name, responses)
        except Exception as exc:
            self._progress_done()
            print(f"{command_name} failed: {exc}")

    def do_bootstrap(self, arg: str) -> None:
        """bootstrap <bootloader.bin>: transfer a ROM-BSL stage-2 bootloader image."""
        argv = shlex.split(arg)
        if not argv:
            print("usage: bootstrap <bootloader.bin>")
            return
        image = Path(argv[0]).read_bytes()
        try:
            result = self.bootstrap_client.transfer(image, progress_cb=self._progress)
            self._progress_done()
        except Exception as exc:
            self._progress_done()
            print(f"bootstrap failed: {exc}")
            return
        print(
            f"bootstrap: ok frames={result.sent_frames} chunks={result.message_count} ack=0x{(result.ack_frame_id or 0):03X}"
        )
        print(
            "bootstrap:",
            f"bit_timing={result.bit_timing}",
            f"ack_data={result.ack_data.hex(' ')}",
        )
        print(
            "bootstrap:",
            f"init={result.init_frame.hex(' ')}",
            f"first_data={result.first_data_frame.hex(' ')}",
        )

    def do_ping(self, arg: str) -> None:
        """ping: send a bootloader keep-alive command and print raw response."""
        try:
            response = self.bootloader.keep_alive()
            if response.data == b"\xde\xad\xbe\xef\xd0\x0d\xba\xad":
                print(f"ping: success data={response.data.hex(' ')}")
            else:
                self._print_bootloader_response("ping", response, expect_status=False)
        except Exception as exc:
            print(f"ping failed: {exc}")

    def do_read32(self, arg: str) -> None:
        """read32 <address>: read one 32-bit value from target memory."""
        argv = shlex.split(arg)
        if len(argv) != 1:
            print("usage: read32 <address>")
            return
        try:
            response = self.bootloader.read_mem32(int(argv[0], 0))
            if len(response.data) >= 4:
                value = int.from_bytes(response.data[0:4], "little", signed=False)
                print(f"read32: 0x{value:08X}")
            else:
                print("read32: no data")
        except Exception as exc:
            print(f"read32 failed: {exc}")

    def do_read_uncompressed(self, arg: str) -> None:
        """read_uncompressed <address> <size> [outfile] [format]: dump bytes without compression."""
        self._execute_read_command(
            "read_uncompressed",
            arg,
            "usage: read_uncompressed <address> <size> [outfile] [format]",
            self.bootloader.read_uncompressed,
        )

    def do_read_compressed(self, arg: str) -> None:
        """read_compressed <address> <size> [outfile] [format]: dump bytes using LZ4 transfer."""
        self._execute_read_command(
            "read_compressed",
            arg,
            "usage: read_compressed <address> <size> [outfile] [format]",
            self.bootloader.read_compressed,
        )

    def do_program_spram(self, arg: str) -> None:
        """program_spram <address> <file> [format] [size] [origin_address]: program a binary or SREC image into SPRAM."""
        self._execute_program_command(
            "program_spram", "spram", arg, self.bootloader.program_spram
        )

    def do_program_flash(self, arg: str) -> None:
        """program_flash <address> <file> [format] [size] [origin_address]: program a binary or SREC image into flash."""
        self._execute_program_command(
            "program_flash", "flash", arg, self.bootloader.program_flash
        )

    def do_erase_sector(self, arg: str) -> None:
        """erase_sector <address>: erase exactly one sector containing address."""
        argv = shlex.split(arg)
        if len(argv) != 1:
            print("usage: erase_sector <address>")
            return

        try:
            address = int(argv[0], 0)
            sector_start, sector_size = self.target.resolve_flash_sector(address)
            response = self._erase_with_timeout(sector_start, sector_size)
            print(f"erase_sector: start=0x{sector_start:08X} size=0x{sector_size:X}")
            self._print_bootloader_response(
                "erase_sector", response, expect_status=True
            )
        except Exception as exc:
            print(f"erase_sector failed: {exc}")

    def do_erase_range(self, arg: str) -> None:
        """erase_range <address> <size>: erase all sectors intersecting the range."""
        argv = shlex.split(arg)
        if len(argv) != 2:
            print("usage: erase_range <address> <size>")
            return

        try:
            start = int(argv[0], 0)
            size = int(argv[1], 0)
            if size <= 0:
                raise ValueError("size must be positive")

            end = start + size
            seen: set[int] = set()
            cursor = start
            erased = 0

            while cursor < end:
                sector_start, sector_size = self.target.resolve_flash_sector(cursor)
                if sector_start in seen:
                    cursor = sector_start + sector_size
                    continue

                seen.add(sector_start)
                response = self._erase_with_timeout(sector_start, sector_size)
                print(
                    f"erase_range: sector=0x{sector_start:08X} size=0x{sector_size:X}"
                )
                self._print_bootloader_response(
                    "erase_range", response, expect_status=True
                )
                erased += 1
                cursor = sector_start + sector_size

            print(f"erase_range: done sectors={erased}")
        except Exception as exc:
            print(f"erase_range failed: {exc}")

    def do_run_flash(self, arg: str) -> None:
        """run_flash: jump to flash entry at 0xA0000000 via bootloader command."""
        try:
            response = self.bootloader.run_from_flash()
            self._print_bootloader_response("run_flash", response, expect_status=True)
        except Exception as exc:
            print(f"run_flash failed: {exc}")

    def do_run_spram(self, arg: str) -> None:
        """run_spram: jump to SPRAM entry at 0xD4001400 via bootloader command."""
        try:
            response = self.bootloader.run_from_spram()
            self._print_bootloader_response("run_spram", response, expect_status=True)
        except Exception as exc:
            print(f"run_spram failed: {exc}")

    def do_unlock(self, arg: str) -> None:
        """unlock <password0> <password1> [flash_bank] [protection] [ucb]: send flash passwords.
        Alternatively: unlock <password_file> [flash_bank] [protection] [ucb] (interprets 8-byte binary file as big endian)
        """
        argv = shlex.split(arg)
        try:
            password0, password1, flash_bank, protection, ucb = parse_unlock_arguments(
                argv
            )
        except ValueError as exc:
            if str(exc) == "USAGE_ERROR":
                self._print_unlock_usage()
            else:
                print(f"unlock failed: {exc}")
            return

        try:
            response = self.bootloader.send_passwords(
                password0,
                password1,
                flash_bank=flash_bank,
                protection=protection,
                ucb=ucb,
            )
            self._print_bootloader_response("unlock", response, expect_status=True)
        except Exception as exc:
            print(f"unlock failed: {exc}")

    def _print_unlock_usage(self) -> None:
        print("usage: unlock <password0> <password1> [flash_bank] [protection] [ucb]")
        print("   or: unlock <password_file> [flash_bank] [protection] [ucb]")
        print("  flash_bank: 0=PFlash0 (default), 1=PFlash1")
        print("  protection: 0=read (default), 1=write")
        print("  ucb: 0..255 (default 0)")

    def do_quit(self, arg: str) -> bool:
        """quit: exit the interactive console."""
        return True

    def do_exit(self, arg: str) -> bool:
        """exit: alias for quit."""
        return True

    def emptyline(self) -> None:
        """Ignore empty input lines instead of repeating the previous command."""
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TC1796 BSL boot console")
    parser.add_argument("--interface", default="gs_usb")
    parser.add_argument("--channel", default=None)
    parser.add_argument("--bitrate", type=int, default=500000)
    parser.add_argument(
        "--target",
        default="tc1796",
        choices=list(TARGETS.keys()),
        help="Target microcontroller configuration",
    )
    parser.add_argument("--command-id", type=lambda value: int(value, 0), default=None)
    parser.add_argument("--response-id", type=lambda value: int(value, 0), default=None)
    parser.add_argument(
        "--bootstrap-init-id",
        type=lambda value: int(value, 0),
        default=None,
    )
    parser.add_argument(
        "--bootstrap-ack-id",
        type=lambda value: int(value, 0),
        default=None,
    )
    parser.add_argument(
        "--bootstrap-data-id",
        type=lambda value: int(value, 0),
        default=None,
    )
    parser.add_argument("--bootstrap-init-interval-s", type=float, default=1.0)
    parser.add_argument("--bootstrap-post-ack-delay-s", type=float, default=0.01)
    parser.add_argument("--bootstrap-data-interval-s", type=float, default=0.007)
    parser.add_argument("--bootstrap-data-send-retry-count", type=int, default=5)
    parser.add_argument(
        "--bootstrap-data-send-retry-delay-s", type=float, default=0.002
    )
    parser.add_argument("--bootloader-inter-block-delay-s", type=float, default=0.007)
    parser.add_argument("--bootloader-inter-frame-delay-s", type=float, default=0.001)
    parser.add_argument("--erase-timeout-s", type=float, default=30.0)
    parser.add_argument("--program-verify", type=int, choices=[0, 1], default=1)
    parser.add_argument("--gs-usb-one-shot", type=int, choices=[0, 1], default=1)
    parser.add_argument(
        "--gs-usb-disable-hw-timestamps", type=int, choices=[0, 1], default=1
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    target = get_target(args.target)

    channel = args.channel
    if channel is None:
        # Default channels for common python-can interfaces
        default_channels = {
            "gs_usb": 0,
            "pcan": "PCAN_USBBUS1",
            "vector": 0,
            "kvaser": 0,
            "canalystii": 0,
            "nican": "CAN0",
            "socketcan": "can0",
        }
        channel = default_channels.get(args.interface, "can0")


    extra: dict[str, object] = {}
    if args.interface == "gs_usb":
        extra["one_shot"] = bool(args.gs_usb_one_shot)
        extra["disable_hw_timestamps"] = bool(args.gs_usb_disable_hw_timestamps)

    transport = CanTransport(
        CanTransportConfig(
            interface=args.interface,
            channel=channel,
            bitrate=args.bitrate,
            extra=extra,
        )
    )

    bootstrap_init_id_val = (
        args.bootstrap_init_id
        if args.bootstrap_init_id is not None
        else target.bootstrap_init_id
    )
    bootstrap_ack_id_val = (
        args.bootstrap_ack_id
        if args.bootstrap_ack_id is not None
        else target.bootstrap_ack_id
    )
    bootstrap_data_id_val = (
        args.bootstrap_data_id
        if args.bootstrap_data_id is not None
        else target.bootstrap_data_id
    )

    command_id = args.command_id
    if command_id is None:
        command_id = normalize_boot_identifier(bootstrap_data_id_val)

    bootstrap_init_id_arb = args.bootstrap_init_id
    if bootstrap_init_id_arb is None:
        bootstrap_init_id_arb = normalize_boot_identifier(bootstrap_init_id_val)

    response_id = args.response_id
    if response_id is None:
        response_id = normalize_boot_identifier(bootstrap_ack_id_val)

    console = BootConsole(
        transport,
        target=target,
        command_arbitration_id=command_id,
        response_arbitration_id=response_id,
        bootstrap_init_arbitration_id=bootstrap_init_id_arb,
        bootstrap_ack_id=bootstrap_ack_id_val,
        bootstrap_data_id=bootstrap_data_id_val,
        bootstrap_init_interval_s=args.bootstrap_init_interval_s,
        bootstrap_post_ack_delay_s=args.bootstrap_post_ack_delay_s,
        bootstrap_data_interval_s=args.bootstrap_data_interval_s,
        bootstrap_data_send_retry_count=args.bootstrap_data_send_retry_count,
        bootstrap_data_send_retry_delay_s=args.bootstrap_data_send_retry_delay_s,
        bootloader_inter_block_delay_s=args.bootloader_inter_block_delay_s,
        bootloader_inter_frame_delay_s=args.bootloader_inter_frame_delay_s,
        erase_timeout_s=args.erase_timeout_s,
        program_verify=bool(args.program_verify),
    )
    try:
        console.cmdloop()
    except KeyboardInterrupt:
        print("\nApplication terminated.")
        return 130
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
