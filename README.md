# BSL Boot Console

Python boot console for the TC1796 CAN bootstrap loader and the follow-up bootloader protocol.

This project is a generic, vendor-independent alternative bootloader/BSL interface to the [fastboatster/TC1796_CAN_BSL](https://github.com/fastboatster/TC1796_CAN_BSL/) tool.

**Extensible Target Support**: The tool supports multiple Infineon TriCore microcontrollers. You can select the target using the `--target` CLI parameter. Supported target values are `tc1796` (default), `tc1792`, and `tc1766`.
**Multi-Platform Portability**: Unlike the original Raspberry Pi specific implementation (which relies on `pigpio` and SocketCAN), this console runs on **Windows, macOS, and Linux**. It works with any USB-to-CAN adapter supported by `python-can` (such as CANable/Candlelight using the `gs_usb` backend).

---

This implementation leverages the custom stage-2 bootloader design and binary:
- **Bootloader Binary**: The stage-2 bootloader binary can be downloaded directly from [fastboatster/TC1796_CAN_BSL](https://github.com/fastboatster/TC1796_CAN_BSL/blob/main/bootloader.bin).
- **Bootloader Source**: The custom bootloader firmware source code is available at [fastboatster/TC1796_CAN_bootloader](https://github.com/fastboatster/TC1796_CAN_bootloader).

The `gs_usb` backend used with candelight_fw requires `pyusb` to be installed.

---

## Preparation

Before using the boot console, you must configure the target microcontroller to boot into bootstrap loader (BSL) mode:
1. **Boot Mode Configuration**: Configure the hardware configuration pins `HWCFG[3:0] = 0001` (refer to the *TC1796 User's Manual* for details).
2. **CAN Connection**: Connect the CAN bus physical lines (`CAN_H` and `CAN_L`) to the microcontroller's CAN transceiver, which connects to the target's `RXDCAN0` and `TXDCAN0` pins.

---

## Command-Line Arguments

The console tool accepts the following arguments to customize the connection, target, and timing settings:

### General & Target Selection
* `--target`: Target microcontroller profile. Choices: `tc1796` (default), `tc1792`, `tc1766`.
* `--interface`: The `python-can` interface backend (default: `gs_usb`). Examples: `pcan`, `socketcan`, `vector`, `kvaser`, etc.
* `--channel`: The CAN channel identifier (default: `0` for `gs_usb`, `can0` for `vector` or `PCAN_USBBUS1` for `pcan`).
* `--bitrate`: The CAN bus bitrate in bps (default: `500000`).

### CAN ID Overrides
By default, CAN IDs are automatically populated using the selected `--target` profile configuration. You can override them using:
* `--command-id`: The CAN arbitration ID to send bootloader commands to.
* `--response-id`: The expected CAN arbitration ID for bootloader responses. If omitted, this defaults to the target's expected ID (e.g., `0x321` for `tc1796`), which automatically filters out echoed frames and other bus traffic.
* `--bootstrap-init-id`: The CAN ID used to send the BSL initialization packet.
* `--bootstrap-ack-id`: The CAN ID used to wait/listen for the BSL ROM loader ACK.
* `--bootstrap-data-id`: The CAN ID used to transmit BSL binary chunks.

### Bootstrap Timing & Tuning
* `--bootstrap-init-interval-s`: Time in seconds between BSL initialization packets (default: `1.0`).
* `--bootstrap-post-ack-delay-s`: Delay in seconds after receiving the bootstrap ACK before starting the binary data transfer (default: `0.01`).
* `--bootstrap-data-interval-s`: Delay in seconds between binary data frames during BSL upload (default: `0.007`).
* `--bootstrap-data-send-retry-count`: Maximum transmit retry attempts for a BSL chunk if sending fails (default: `5`).
* `--bootstrap-data-send-retry-delay-s`: Delay in seconds between BSL chunk retry attempts (default: `0.002`).

### Bootloader Timing & Tuning
* `--bootloader-inter-block-delay-s`: Idle delay in seconds inserted between entire blocks during read/write commands (default: `0.007`).
* `--bootloader-inter-frame-delay-s`: Idle delay in seconds inserted between individual 8-byte CAN frames within a data block (default: `0.001`). Increase this if your ECU or transceiver suffers from receive overrun/dropped frame errors.
* `--erase-timeout-s`: Maximum timeout in seconds allowed for flash sector erase operations (default: `30.0`).
* `--program-verify`: Verify write integrity block-by-block. Choices: `0` (disable) or `1` (enable, default).

### Interface-Specific Settings
* `--gs-usb-one-shot`: Enable/disable CAN one-shot mode for `gs_usb` interface. Choices: `0` or `1` (default).
* `--gs-usb-disable-hw-timestamps`: Disable hardware timestamps for `gs_usb` interface. Choices: `0` or `1` (default).

---

## Usage Workflow

To interact with the target, you must follow the typical bootloader initialization sequence:

### 1. Bootstrap
Before any protocol commands can be processed, you must upload the stage-2 bootloader image to the target SPRAM (you can download it from [fastboatster/TC1796_CAN_BSL](https://github.com/fastboatster/TC1796_CAN_BSL/blob/main/bootloader.bin)):
```text
bsl> bootstrap /path/to/bootloader.bin
```

### 2. Unlock (if locked)
If the target microcontroller has flash protection enabled (which is typical), flash commands (like reading, programming or erasing) will fail. You must unlock the target:
- **Unlock via raw passwords (integers):**
  ```text
  bsl> unlock <pass1> <pass2> [flash_bank] [protection] [ucb]
  ```
- **Unlock via an 8-byte binary password file:**
  ```text
  bsl> unlock /path/to/password.bin [flash_bank] [protection] [ucb]
  ```
  *(The 8-byte file is parsed as two 32-bit big-endian integers)*

> **Password Acquisition**: The passwords required to unlock the device are target-specific and must be obtained by other means (which is outside the scope of this project).

### 3. Execute Commands
Once bootstrapped and unlocked, you are ready to issue commands:
- **Verify communication:** Use the `ping` command.
- **Read memory**: Use `read_compressed` or `read_uncompressed`.
  - Usage: `read_compressed/read_uncompressed <address> <size> [outfile] [format]`
  - Format defaults to `bin`. Pass `srec` to export to Motorola S-record format.
- **Modify memory**: Use `program_flash` or `program_spram` to program data, or `erase_range`/`erase_sector`.
  - Usage (Binary): `program_flash/program_spram <address> <file>` (default `bin` format)
  - Usage (SREC): `program_flash/program_spram <address> <file> srec [size] [origin_address]` (programs data blocks from the SREC file. If `<address>` is non-zero and `[origin_address]` is provided, it crops the SREC data starting at `origin_address` for `size` bytes and shifts/maps it to write to `address` on the ECU. If `<address>` is non-zero and `[origin_address]` is omitted, it crops starting at `address` and programs it directly without shifting. Pass `0` for `<address>` to program all blocks in the SREC file to their original addresses).
- **Exit/Jump:** Use `run_flash` or `run_spram`.

---

## Credits

Credits and special thanks to the original authors of the TC179x BSL tools and research:
- [fastboatster](https://github.com/fastboatster) for the TC1796 BSL implementation.
- [bri3d](https://github.com/bri3d) for reverse engineering contributions and TC1791 BSL research.
