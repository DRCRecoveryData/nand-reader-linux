"""YKUSH XS switched hub over libusb: the only way to drop board power from the host.

Command set from Yepkit's ykush control app (src/ykushxs/ykushxs.cpp), Linux
path: raw interrupt transfers on EP 0x01/0x81, no HID report-ID prefix.

  0x11  port up      -> reply[0] == 0x01
  0x01  port down    -> reply[0] == 0x01
  0x21  port status  -> reply[0] == 0x01, reply[1] == 0x11 while the port is ON

Board power comes only from the downstream port: the ATtiny holds PB2 high and
0xB0 is not reachable from the host, so this is what asserts nCONFIG.
"""

import time

import usb.core
import usb.util

VID = 0x04D8
PID = 0xF0CD
EP_OUT = 0x01
EP_IN = 0x81
REPORT = 64

CMD_UP = 0x11
CMD_DOWN = 0x01
CMD_STATUS = 0x21
STATUS_ON = 0x11
READER_IDS = ((0x03EB, 0x5903),)


class YkushError(RuntimeError):
    pass


def _reports():
    return list(usb.core.find(find_all=True, idVendor=VID, idProduct=PID))


def present():
    """True if a YKUSH XS is attached (its own USB link, not the reader's)."""
    return bool(_reports())


def _send(dev, cmd):
    dev.write(EP_OUT, bytes([cmd]) + bytes(REPORT - 1), 1000)
    reply = bytes(dev.read(EP_IN, REPORT, 2000))
    if not reply or reply[0] != 0x01:
        raise YkushError(f"command 0x{cmd:02x} rejected: reply {reply[:4].hex(' ')}")
    return reply


def status():
    """True if the reader port is powered, False if off, None if no board."""
    devs = _reports()
    if not devs:
        return None
    try:
        return _send(devs[0], CMD_STATUS)[1] == STATUS_ON
    finally:
        usb.util.dispose_resources(devs[0])


def set_power(on):
    devs = _reports()
    if not devs:
        raise YkushError("no YKUSH XS attached")
    try:
        _send(devs[0], CMD_UP if on else CMD_DOWN)
    finally:
        usb.util.dispose_resources(devs[0])


def reader_attached():
    for vid, pid in READER_IDS:
        if usb.core.find(idVendor=vid, idProduct=pid) is not None:
            return True
    return False


def power_cycle(off_s=1.5, deadline_s=20.0, settle_s=0.5, log=None):
    """Drop the reader's power, wait, restore it, wait for re-enumeration.

    Returns seconds the reader took to come back after power was restored.
    """
    say = log or (lambda msg: None)
    if not present():
        raise YkushError("no YKUSH XS attached")
    say("ykush: port OFF")
    set_power(False)
    time.sleep(off_s)
    if reader_attached():
        raise YkushError(f"reader still enumerated {off_s:.1f}s after power off "
                         "- wrong hub output, or the reader is powered elsewhere")
    say("ykush: port ON")
    set_power(True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < deadline_s:
        if reader_attached():
            time.sleep(settle_s)
            took = time.monotonic() - t0
            say(f"ykush: reader back in {took:.1f}s")
            return took
        time.sleep(0.1)
    raise YkushError(f"reader did not re-enumerate within {deadline_s:.0f}s")
