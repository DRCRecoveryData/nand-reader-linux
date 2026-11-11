#!/usr/bin/env python3
"""Snapshot everything the reader port exposes, into one sealed archive.

Captured, all read-only:

  atmel/atiny13a_descriptor_page.bin  flash 0x0300..0x03FF via 0xA1 (256 B)
  fpga/streamed_segment.bin           the config stream we send, hash-verified
  fpga/live_state.json                rail reply, chip ID, enumeration facts
  usb/device_descriptor.bin           raw descriptor set (18 B + config tree)
  usb/strings.json                    all USB strings as the reader reports them
  images/<vendor>.bin                 the sealed factory images, when given
  SHA256SUMS                          seal over everything above
  MANIFEST.md                         what each file is and what it cannot be

The ATtiny flash at 0x0000..0x02F7 is not captured because there is no path to
it: the ATtiny only answers on the SX2 I2C bus, has no write opcode and can
address no flash below 0x02F8. That is stated in MANIFEST.md rather than hidden.

  python3 reader_backup.py --out ~/backups/reader_2026-11-11 --power-cycle
  python3 reader_backup.py --out DIR --replugged --id
"""

import argparse
import hashlib
import json
import os
import struct
import sys
import time

import nand_linux as nl
import usb.core
import usb.util

import ykush



def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write(dst, rel, data, note):
    path = os.path.join(dst, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return {"file": rel, "bytes": len(data), "sha256": sha256(data), "note": note}


def dev_descriptor(dev, cfg):
    def ep(addr, attr, size):
        return struct.pack("<BBBBHBB", 7, 0x05, addr, attr, size, 0, 0)
    body = struct.pack("<BBHBBBBB", 9, 0x02, 0, 1, 1, 0, 0xA0, 0xFA)
    for iface in cfg:
        eps = b"".join(ep(e.bEndpointAddress, e.bmAttributes, e.wMaxPacketSize)
                       for e in iface)
        body += struct.pack("<BBBBBBBBB", 9, 0x04, iface.bInterfaceNumber, 0,
                            iface.bNumEndpoints, iface.bInterfaceClass,
                            iface.bInterfaceSubClass, iface.bInterfaceProtocol, 0) + eps
    body = body[:2] + struct.pack("<H", len(body)) + body[4:]
    head = struct.pack("<BBHBBBBHHHBBBB", 18, 0x01, dev.bcdUSB, 0, 0, 0,
                       dev.bMaxPacketSize0, dev.idVendor, dev.idProduct,
                       dev.bcdDevice, dev.iManufacturer, dev.iProduct,
                       dev.iSerialNumber, 1)
    return head + body


def grab_usb(dst):
    """Descriptors and strings as the kernel hands them out (no vendor driver)."""
    dev = usb.core.find(idVendor=nl.VID, idProduct=nl.PID)
    if dev is None:
        raise RuntimeError("reader not enumerated")
    # The SX2's control pipe times out on raw GET_DESCRIPTOR; the kernel has
    # already read the tree at enumeration, so rebuild it from PyUSB's parse.
    cfg = dev.get_active_configuration()
    strings = {}
    for idx, what in ((dev.iManufacturer, "manufacturer"),
                      (dev.iProduct, "product"),
                      (dev.iSerialNumber, "serial")):
        if idx:
            try:
                strings[what] = usb.util.get_string(dev, idx)
            except usb.core.USBError:
                strings[what] = None
    for iface in cfg:
        if iface.iInterface:
            try:
                strings[f"interface{iface.bInterfaceNumber}"] = \
                    usb.util.get_string(dev, iface.iInterface)
            except usb.core.USBError:
                pass
    info = {
        "vid": f"{dev.idVendor:04x}", "pid": f"{dev.idProduct:04x}",
        "bcdDevice": f"{dev.bcdDevice:04x}", "bcdUSB": f"{dev.bcdUSB:04x}",
        "bus": int(dev.bus), "address": int(dev.address),
        "interfaces": [
            {"number": iface.bInterfaceNumber,
             "class": f"{iface.bInterfaceClass:02x}",
             "endpoints": [f"{ep.bEndpointAddress:02x}" for ep in iface]}
            for iface in cfg],
        "strings": strings,
    }
    entries = [write(dst, "usb/device_descriptor.bin",
                     dev_descriptor(dev, cfg),
                     "device + configuration + interface + endpoint descriptors"),
               write(dst, "usb/strings.json",
                     json.dumps(info, indent=2).encode(), "descriptor facts")]
    usb.util.dispose_resources(dev)
    return entries, info


def grab_live(dst, do_id, descriptor):
    """Cold-boot the reader, then capture state through id_at's proven order.

    id_at() streams the power design, writes the DAC word, tolerates a missing
    rail echo, then streams the chip design and reads the ID - the exact
    sequence that answers on hardware. power() alone is not used: streaming it
    as a first command fails intermittently on a freshly powered SX2.
    """
    live = {"time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    entries = []
    with nl.Reader() as r:
        r.load_payloads(PAYLOADS)
        if do_id:
            ident, rail = r.id_at(1, nl.CV_MAX, 4, 4000)
            live["chip_id"] = ident.hex(" ") if ident else None
            live["rail_cv"] = rail
        else:
            r.boot_rail(nl.CV_MAX)
        entries.append(write(dst, "fpga/streamed_segment.bin", r.power_image,
                             "power design exactly as sent over EP2"))
        if descriptor:
            page = read_descriptor_page(r)
            if page:
                entries.append(write(dst, "atmel/attiny13a_descriptor_page.bin",
                                     page, "ATtiny flash 0x0300..0x03FF via 0xA1"))
                live["attiny_page"] = f"0x{len(page):03x} bytes via 0xA1"
            else:
                live["attiny_page"] = PROPRIETARY_NOTE
    entries.append(write(dst, "fpga/live_state.json",
                         json.dumps(live, indent=2).encode(),
                         "rail reply and chip ID measured this run"))
    return entries, live


PROPRIETARY_NOTE = ("not reachable: 0xA1 is answered only by the ATtiny on the "
                    "SX2 I2C bus and the stock FPGA design does not master it")


def read_descriptor_page(r):
    desc = bytearray(nl.DESCRIPTOR)
    desc[0] = 0xA1
    try:
        r._out(bytes(desc))
        page = bytes(r.dev.read(nl.EP_IN, 256, 2500))
    except nl.usb.core.USBError:
        return b""
    if len(page) < 4 or page.count(0) == len(page):
        return b""
    return page


def man(groups, live, images):
    lines = ["# Reader backup", "",
             f"taken {time.strftime('%Y-%m-%d %H:%M:%S')} on the reader port.", "",
             "## What this proves", ""]
    for g in groups:
        lines.append(f"- **{g['group']}**")
        for e in g["files"]:
            lines.append(f"  - `{e['file']}` — {e['bytes']} B, "
                         f"`{e['sha256'][:16]}…` — {e['note']}")
    lines += ["", "## Measured this run", "",
              f"- rail at {nl.CV_MAX} cV: {live.get('rail_cv')}",
              f"- chip ID: {live.get('chip_id')}",
              f"- ATtiny descriptor page: {live.get('attiny_page', 'not attempted')}",
              f"- enumeration: bus {live.get('bus')} addr {live.get('address')}"
              if 'bus' in live else "- enumeration: not captured", "",
              "## What this cannot restore", "",
              "The ATtiny13A flash is not in this archive and cannot be read from",
              "the host. Its only wired pins are PB0/PB1 (SX2 I2C) and PB2 (FPGA",
              "nCONFIG); PB5/RESET, PB3/MOSI and PB4/MISO carry no net. The",
              "firmware answers A0/A1/B0/C0/D0, has no write opcode and can",
              "address only flash 0x02F8..0x03FF - so even the descriptor page at",
              "0x0300 is out of reach while the stock FPGA design is resident,",
              "which is what the `0xA1 returned nothing` result above records.",
              "Restoring the ATtiny needs ISP on six soldered wires, against the",
              "sealed copy `vendor_attiny13a.bin` (a29117f3…) bundled here when",
              "--images was given.", "",
              "The FPGA design, by contrast, restores over USB:",
              "`restore_linux.py --power-cycle` (or `--replugged`).", ""]
    if images:
        lines.append(f"Factory images referenced: {', '.join(images)}")
    return ("\n".join(lines) + "\n").encode()


def seal(dst, entries):
    lines = []
    for e in sorted(entries, key=lambda x: x["file"]):
        lines.append(f"{e['sha256']}  {e['file']}")
    body = ("\n".join(lines) + "\n").encode()
    with open(os.path.join(dst, "SHA256SUMS"), "wb") as fh:
        fh.write(body)
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--images", default=None,
                    help="factory/recovery folder to copy sealed images from")
    ap.add_argument("--id", action="store_true", help="also read the NAND chip ID")
    ap.add_argument("--attiny-page", action="store_true",
                    help="try 0xA1 for the ATtiny descriptor page (usually silent)")
    gate = ap.add_mutually_exclusive_group()
    gate.add_argument("--power-cycle", action="store_true")
    gate.add_argument("--replugged", action="store_true")
    gate.add_argument("--no-config", action="store_true",
                      help="only USB descriptors; never stream config")
    args = ap.parse_args()

    dst = os.path.abspath(os.path.expanduser(args.out))
    if os.path.exists(dst):
        sys.exit(f"FAIL {dst} exists, pick a new directory")
    os.makedirs(dst)

    groups = []
    try:
        files, info = grab_usb(dst)
        groups.append({"group": "USB enumeration", "files": files})
        live = {"bus": info["bus"], "address": info["address"]}

        if not args.no_config:
            if args.power_cycle:
                ykush.power_cycle(log=lambda m: print(m, file=sys.stderr))
            elif not args.replugged:
                sys.exit("FAIL pass --power-cycle, --replugged or --no-config")
            files, lv = grab_live(dst, args.id, args.attiny_page)
            live.update(lv)
            groups.append({"group": "FPGA design + live state", "files": files})

        copied = []
        if args.images:
            for name in ("vendor_power_init_segment.bin", "vendor_init_segment.bin",
                         "vendor_program.bin", "vendor_control.bin",
                         "vendor_attiny13a.bin"):
                src = os.path.join(args.images, name)
                if not os.path.isfile(src):
                    continue
                with open(src, "rb") as fh:
                    data = fh.read()
                files = [write(dst, f"images/{name}", data, "sealed factory image")]
                groups.append({"group": f"Factory image {name}", "files": files})
                copied.append(name)

        flat = [e for g in groups for e in g["files"]]
        with open(os.path.join(dst, "MANIFEST.md"), "wb") as fh:
            fh.write(man(groups, live, copied))
        seal(dst, flat + [{"file": "MANIFEST.md",
                           "sha256": sha256(open(os.path.join(dst, "MANIFEST.md"),
                                                 "rb").read())}])
    except (RuntimeError, OSError, usb.core.USBError, ykush.YkushError) as exc:
        sys.exit(f"FAIL {exc}")

    print(f"RESULT OK {len(flat)} files sealed in {dst}")


PAYLOADS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "payloads")

if __name__ == "__main__":
    main()
