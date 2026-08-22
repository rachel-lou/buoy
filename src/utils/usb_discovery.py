"""Find a USB-serial device by vendor/product ID.

Shared by anything connected over USB rather than fixed GPIO UART pins --
``/dev/ttyUSBn`` paths shift across reboots and replugs depending on
enumeration order (and depending on what else is plugged in at the time),
so nothing that stays working long-term can hardcode one.
"""
from __future__ import annotations

import logging
from typing import Optional


def discover_usb_serial_device(
    serial_module,
    vendor_id: int,
    product_id: int,
    logger: Optional[logging.Logger] = None,
) -> Optional[str]:
    """Return the device path of the first attached port matching vendor_id/product_id.

    Returns None if nothing matches, or if enumeration itself fails.
    """
    try:
        ports = serial_module.tools.list_ports.comports()
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.error("usb_discovery_failed", extra={"component": "usb_discovery", "error": str(exc)})
        return None
    for port in ports:
        if getattr(port, "vid", None) == vendor_id and getattr(port, "pid", None) == product_id:
            return port.device
    return None
