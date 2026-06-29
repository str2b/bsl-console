from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import can
except ImportError:  # pragma: no cover - handled at runtime
    can = None

try:
    import libusb_package
except Exception:  # pragma: no cover
    libusb_package = None


class CanTransportError(RuntimeError):
    pass


def _patch_gs_usb_read_compat() -> None:
    """Patch gs_usb read() to tolerate 20/24-byte frame variants."""

    try:
        import usb.core  # type: ignore
        import gs_usb.gs_usb as gs_usb_mod  # type: ignore
        from gs_usb.gs_usb_frame import GsUsbFrame  # type: ignore
    except Exception:
        return

    if getattr(gs_usb_mod.GsUsb.read, "_bsl_console_patched", False):
        return

    def _compat_read(self, frame, timeout_ms):
        hw_timestamps = (
            (self.device_flags & gs_usb_mod.GS_CAN_MODE_HW_TIMESTAMP)
            == gs_usb_mod.GS_CAN_MODE_HW_TIMESTAMP
        )
        try:
            data = self.gs_usb.read(0x81, frame.__sizeof__(hw_timestamps), timeout_ms)
        except usb.core.USBError:
            return False

        if not data:
            return False

        data_len = len(data)
        if data_len == 24:
            GsUsbFrame.unpack_into(frame, data, True)
            return True
        if data_len == 20:
            GsUsbFrame.unpack_into(frame, data, False)
            return True
        return False

    _compat_read._bsl_console_patched = True  # type: ignore[attr-defined]
    gs_usb_mod.GsUsb.read = _compat_read


def _enable_pyusb_libusb_backend() -> None:
    """Ensure PyUSB can discover a libusb DLL on Windows."""

    if libusb_package is None:
        return

    try:
        dll_path = Path(libusb_package.get_library_path())
    except Exception:
        return

    if not dll_path.exists():
        return

    dll_dir = str(dll_path.parent)
    path = os.environ.get("PATH", "")
    parts = path.split(";") if path else []
    if dll_dir not in parts:
        os.environ["PATH"] = f"{dll_dir};{path}" if path else dll_dir

    _patch_gs_usb_read_compat()


def discover_gs_usb_devices() -> list[str]:
    """Return a lightweight description of available gs_usb devices."""

    _enable_pyusb_libusb_backend()

    try:
        from gs_usb.gs_usb import GsUsb
    except Exception as exc:
        raise CanTransportError(
            "gs_usb discovery is unavailable because the gs_usb package is missing or broken"
        ) from exc

    try:
        devices = GsUsb.scan()
    except Exception as exc:
        raise CanTransportError("failed to scan gs_usb devices") from exc

    descriptions: list[str] = []

    def _fmt_hex(value: object) -> str:
        if value is None:
            return "n/a"
        try:
            return f"0x{int(value):04x}"
        except Exception:
            return str(value)

    for index, device in enumerate(devices):
        serial = getattr(device, "serial", None)
        if callable(serial):
            try:
                serial = serial()
            except Exception:
                serial = None
        vendor = getattr(device, "idVendor", None)
        product = getattr(device, "idProduct", None)
        descriptions.append(
            f"index={index} vid={_fmt_hex(vendor)} pid={_fmt_hex(product)} serial={serial or 'n/a'}"
        )
    return descriptions


@dataclass(slots=True)
class CanTransportConfig:
    interface: str
    channel: str | int | None = None
    bitrate: int | None = None
    fd: bool = False
    data_bitrate: int | None = None
    receive_own_messages: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class CanTransport:
    def __init__(self, config: CanTransportConfig) -> None:
        self.config = config
        self._bus = None
        self._rx_buffer: deque[Any] = deque()

    @property
    def bus(self):
        return self._bus

    def open(self) -> None:
        if can is None:
            raise CanTransportError("python-can is not installed")
        if self._bus is not None:
            return

        channel = self.config.channel
        if self.config.interface == "gs_usb":
            _enable_pyusb_libusb_backend()
            if channel is None:
                details = discover_gs_usb_devices()
                if not details:
                    raise CanTransportError(
                        "No gs_usb devices detected. Check USB connection and WinUSB/Zadig driver binding."
                    )
                channel = 0
            elif isinstance(channel, str):
                try:
                    channel = int(channel, 0)
                except ValueError as exc:
                    raise CanTransportError(
                        "gs_usb channel must be a numeric device index (for example 0, 1, 2)"
                    ) from exc

        kwargs: dict[str, Any] = {
            "interface": self.config.interface,
            "channel": channel,
            "bitrate": self.config.bitrate,
            "fd": self.config.fd,
            "receive_own_messages": self.config.receive_own_messages,
        }
        if self.config.data_bitrate is not None:
            kwargs["data_bitrate"] = self.config.data_bitrate
        kwargs.update(self.config.extra)

        attempts = 3 if self.config.interface == "gs_usb" else 1
        for attempt in range(1, attempts + 1):
            try:
                self._bus = can.Bus(**kwargs)
                self._configure_gs_usb_runtime()
                break
            except OSError as exc:
                if self.config.interface != "gs_usb" or getattr(exc, "errno", None) != 13 or attempt == attempts:
                    raise
                time.sleep(0.1 * attempt)
            except Exception as exc:
                if self.config.interface == "gs_usb" and "Cannot find device" in str(exc):
                    details = []
                    try:
                        details = discover_gs_usb_devices()
                    except Exception:
                        details = []
                    discovered = ", ".join(details) if details else "none"
                    raise CanTransportError(
                        "gs_usb adapter not found for the selected index. "
                        f"Requested channel={channel}. Discovered devices: {discovered}. "
                        "Check USB connection and WinUSB/Zadig driver binding."
                    ) from exc
                raise

    def _configure_gs_usb_runtime(self) -> None:
        if self._bus is None or self.config.interface != "gs_usb":
            return

        gs_dev = getattr(self._bus, "gs_usb", None)
        if gs_dev is None:
            return

        disable_hw_timestamps = bool(self.config.extra.get("disable_hw_timestamps", True))
        one_shot = bool(self.config.extra.get("one_shot", True))

        try:
            from gs_usb.constants import GS_CAN_MODE_NORMAL, GS_CAN_MODE_ONE_SHOT  # type: ignore

            flags = GS_CAN_MODE_NORMAL
            if one_shot:
                flags |= GS_CAN_MODE_ONE_SHOT

            # Re-start with explicit flags to avoid timestamp/frame-size mismatch and
            # to prevent automatic retransmit bursts when no ACK is present yet.
            if disable_hw_timestamps or one_shot:
                gs_dev.stop()
                gs_dev.start(flags=flags)
        except Exception:
            pass

    def close(self) -> None:
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None
        self._rx_buffer.clear()

    def send(self, arbitration_id: int, payload: bytes, *, extended_id: bool = False) -> None:
        if self._bus is None:
            self.open()
        if self._bus is None:
            raise CanTransportError("transport is not open")
        message = can.Message(arbitration_id=arbitration_id, data=payload, is_extended_id=extended_id)
        # Retry loop for transmit queue full errors
        max_retries = 1000
        for attempt in range(max_retries):
            try:
                self._bus.send(message)
                break
            except Exception as exc:
                exc_str = str(exc).lower()
                if "queue is full" in exc_str or "xmtfull" in exc_str or "full" in exc_str:
                    if attempt < max_retries - 1:
                        time.sleep(0.001)
                        continue
                raise

        # gs_usb keeps TX contexts that are released while processing RX events.
        # During long write bursts, pump non-blocking RX to avoid context exhaustion.
        if self.config.interface == "gs_usb":
            self._pump_gs_usb_rx(max_messages=4)

    def recv(self, timeout: float | None = None):
        if self._bus is None:
            self.open()
        if self._bus is None:
            raise CanTransportError("transport is not open")
        if self._rx_buffer:
            return self._rx_buffer.popleft()
        return self._bus.recv(timeout)

    def drain_rx(self, *, max_messages: int = 256) -> int:
        """Discard pending RX frames from internal and bus buffers."""

        if self._bus is None:
            self.open()
        if self._bus is None:
            raise CanTransportError("transport is not open")

        discarded = 0
        while self._rx_buffer and discarded < max_messages:
            self._rx_buffer.popleft()
            discarded += 1

        while discarded < max_messages:
            try:
                msg = self._bus.recv(0.0)
            except Exception:
                break
            if msg is None:
                break
            discarded += 1

        return discarded

    def iter_messages(self) -> Iterable[Any]:
        if self._bus is None:
            raise CanTransportError("transport is not open")
        yield from self._bus

    def _pump_gs_usb_rx(self, *, max_messages: int = 4) -> None:
        if self._bus is None:
            return
        for _ in range(max_messages):
            try:
                msg = self._bus.recv(0.0)
            except Exception:
                return
            if msg is None:
                return
            self._rx_buffer.append(msg)
