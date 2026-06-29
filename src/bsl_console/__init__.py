"""TC1796 CAN boot console package."""

from .bootstrap import BootstrapConfig, BootstrapTransferClient, BootstrapTransferResult
from .bootloader import BootloaderClient, BootloaderCommand
from .protocol import (
    BLOCK_TYPE_DATA,
    BLOCK_TYPE_EOT,
    BLOCK_TYPE_HEADER,
    BootloaderBlock,
    DataBlock,
    EotBlock,
    HeaderBlock,
    checksum_xor,
)
from .targets import Target, get_target, TARGETS
from .transport import CanTransport, CanTransportConfig, CanTransportError

__all__ = [
    "BLOCK_TYPE_DATA",
    "BLOCK_TYPE_EOT",
    "BLOCK_TYPE_HEADER",
    "BootstrapConfig",
    "BootstrapTransferClient",
    "BootstrapTransferResult",
    "BootloaderBlock",
    "BootloaderClient",
    "BootloaderCommand",
    "CanTransport",
    "CanTransportConfig",
    "CanTransportError",
    "DataBlock",
    "EotBlock",
    "HeaderBlock",
    "checksum_xor",
    "Target",
    "get_target",
    "TARGETS",
]
