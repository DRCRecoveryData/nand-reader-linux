# NAND_Reader on Linux

Linux driver for the **Soft-Center NAND Flash Reader 2.0** (`03eb:5903`),
built to the wire protocol documented in `DRCRecoveryData/nand-recovery-reference`.
No vendor Windows software is used: `./nand` drives the reader with libusb.

## Status

| Part | State |
|---|---|
| USB enumerate + claim (EP2 OUT / EP6 IN) | working, verified against the live device |
| Drop-behaviour probe (bare DAC / `FFFF`) | verified — both dropped, read times out (110) |
| DAC word + echo decode | working — 327 cV at a 330 cV target, repeatable |
| Payloads | present, hash-matched, pack verified byte-for-byte |
| ID table | generated in code, sha256 `2b1bc203` matches the doc |
| ID leg | **working** — `89 C4 08 32`, no YKUSH needed |
| Page read (`readp`) | **working** — real per-page NAND data via the vendor READ program |
| Spare area (`readp --unit 2176`) | working — 2048 B data + 128 B spare |
| Banks on read | banks 1 and 2 read real data; 3 and 4 unselected by the FPGA (`30` fill) |
| Range dump (`dump`) | range-control path still returns one fixed unit; read page-by-page instead |
| Power leg (rail + DAC echo) | working, no YKUSH needed |
| `30 XX` status frame | answers on the power design, `[30, op, v_hi, v_lo]` |

### Reading real pages (`readp`)

`./nand readp <page> [...] [--out DIR] [--votes N] [--unit N]` reads individual
2 KB pages with the vendor's own READ program (`NAND_Reader.exe`
`FUN_0045e3d0`: reset, three zero row-address cycles, two column cycles,
`11 30` = READ / `0x30`, then the DMA trailer). The page number is carried in
the 32-byte range/control block (bytes 0..3, `_range_ctl`), **not** in the
row-address cycles and **not** in the program trailer — both were tested and
neither changes the output. The `0x13` column cycles are also ignored: the FPGA
always streams from the start of the page, so reading the spare area is done by
lengthening the unit, not by moving the column. One control block fits per boot,
so every page needs its own YKUSH cold cycle (~35 s/page).

What the chip actually contains (verified 2026-11-11):

- 2 KB pages, organised in **8-page blocks**: one erased page (`ff ff …`), then
  seven data-or-blank pages. Erased pages land on multiples of 8 (992, 1008,
  1024, 1040, …); within a block, pages read `PROG` (high-entropy content,
  ~7.4 bits/byte, no file signatures) or all-`00` (blank). This cadence is
  genuine flash structure, not a pattern generator.
- The low region is erased; programmed data clusters around pages ~1000–2050.
- **Banks 1 and 2 both read real page data** (same page returns different,
  valid content on each — a second die/section). Banks 3 and 4 return
  `30 30 30 …` (the DMA trailer echo) because the FPGA leaves those sets
  unselected; that is a hardware limit, not a driver gap. Use
  `readp --bank 2` for the second bank.
- `--unit 2176` returns 2048 B data **plus 128 B spare** (an x16 2176-byte page
  layout); the spare is also high-entropy on every page — there is no plain
  ECC block sitting there. The stored content, main and spare, is encrypted or
  controller-scrambled, so this is a raw-cell read, not a decoded image.

### Cleaning the read (`--votes`)

A small fraction of cells are marginal: each read resolves them to single bits
`0x01..0x80` that flip between reads, on a fixed set of ~20–90 byte positions
per page. `--votes N` reads the page N times and takes the per-bit majority
(the best estimate of the stored bit) and reports how many bits were marginal.

- At 15 votes the head of a page is stable and reproducible; ~70 bits/page are
  marginal but strongly biased (e.g. 1–2 or 13–14 of 15) and vote correctly.
- About **~12 bits per 2 KB page stay undecided even at 31 votes** (persistent
  ~50/50 cells). That is the raw-read floor for a worn page without its ECC
  layer; correcting past it needs the chip's ECC, which is not recoverable from
  the 2176-byte read here. Use `--votes` 15 for a clean best-effort copy and
  accept a handful of bit errors per page, or 31+ where the content matters.

The earlier `dump` range path still returns only one fixed unit because the FPGA
emits one unit per control block regardless of the range fields; read pages one
at a time with `readp` instead.

A full single-read image of the data region already sits in
`out/region_984_1064/` (43 programmed pages from pages 984–1064, one
`page_<n>.bin` each, all distinct). Re-read any of them with `--votes 15` for a
cleaner copy of a marginal page.

`./nand id --cycle` power-cycles the reader through the YKUSH XS, opens the
device and streams: chip image, then the 2,048 B pair-swapped table, then the
32-byte descriptor, then a read. It has to be a fresh handle opened right after
the reader re-enumerates — no settle before the first write.

The dump leg streams the chip image plus the table and the 32-byte range
control, then reads `(end − start) × unit` bytes in <=1 MiB chunks. On this
bench the map delivers exactly `unit` bytes and then stops, for every
start/end/unit tested:

| start, end, unit | bytes delivered |
|---|---|
| 0, 1, 2048 | 2048 |
| 0, 64, 2048 | 2048 |
| 5, 9, 2048 | 2048 |
| 0, 1, 4096 | 4096 |
| 0, 1, 256 | 256 |

Length scales only with `unit`, never with the page count or the start page,
so the range fields are not producing a range read. The content is
`89 c4 08 32 a6 00 00 00` repeated, i.e. a simulated ID loop, not NAND data.
The only things that change it are flags bits 1 (bus16 → 2× unit, zeros), 3
(ignore-busy → zeros), 6 (DDR → unit byte-pairs interleaved) and 7 (→ zeros) —
all FPGA-side framing, none of them a page read. Ctl+desc in one leg returns
the same one unit; desc alone returns `00 00 00 00`. A further request times
out and wedges EP2 until the next power cycle. The stream loop is in
`Reader.dump()`.

Separately, the ID path only works because it is a 4-byte read; the same map
cannot be made to emit a second unit.

Because the same bytes come back for every page, `dump` can tell a real reply
from an FPGA pattern:

- `--verify` stops before writing anything if the first unit is a known
  synthetic pattern or is one exact period repeated for the whole unit, and
  removes the partial file.
- `--report` prints how many boots ran, how many stopped after a partial read
  (the map never streams a range), and a tally of the first-unit signatures.

A working run shows units that differ page to page and no stall between them.
A run where every boot stalls and every unit shares one signature is the map
answering from its own pattern generator, and no host-side change fixes that.
Getting real payload needs the vendor read path
(`NAND_Reader.exe` → `FlashReader2.sys`), which does not enumerate under Wine
on this host. The vendor binary was nevertheless run under Wine with its device
I/O relayed to `nanddev_proxy.py` (`research/k32hook/`), and it exhibits the
same behaviour: it streams the identical 1,254,752-byte bitstream and 2,048-byte
program block, sends the ID descriptor `… 04 00 04 <bank> 65 00 35 77`, and
reads a fixed `89 c4 08 32` on bank 1 (`00 00 00 00` elsewhere). It does not
recognise that ID, so it never issues a dump. The vendor software hits the same
fixed-pattern wall as the native driver, so the limitation is the FPGA/CPLD
data path (or an absent NAND), not the host driver.

### What was ruled out for the dump (2026-11-11)

Each entry is a real measurement on a YKUSH cold cycle, not a guess:

- page address: start/end set to 0…4, 1,000,000 and 4,194,300 give identical
  bytes, and so does writing the page number into every 4-byte slot of the
  32-byte control block
- rail voltage 0.90–3.30 V in 10 cV steps: the reply picks one of two fixed
  patterns (`89 c4 08 32 a6…` or `2c 01 02 03 04…`), switching with no regard
  to the address; below 0.90 V it is `00` fill, 1.00–1.60 V `27` fill, i.e. a
  bus that is not being driven
- a write after every unit to ask for the next page (nine different 2–32 byte
  triggers): still exactly one unit, then silence
- a second control block on the same boot: always times out, so one control
  per boot is a hard limit
- gaps of 0, 50 ms, 0.5 s and 2 s between table and control, and between
  image and table: no change
- op tables with `0x00 0x30`, `0x11 0x80` or any single opcode substituted
  into the shipped 16-op sequence: every variant times out, so the map
  enforces the shipped table byte for byte
- odd and non-power-of-two `unit` values: length becomes
  `round_up(unit+1, 256)` and the content is always `00` fill

The chip image is byte-identical to the vendor map design (it unpacks to the
same 78,422-byte RBF, sha256 `0a5b07c9…`), so this is the vendor bitstream
behaving as shipped, not a mis-encoded configuration.

### Automatic recovery

A leg that times out leaves the FPGA holding a pending URB, and neither a bus
reset nor a fresh handle clears it. `ensure_awake()` (used by `power` and
`dump`) and `id` therefore fall back to a YKUSH power cycle and retry, so the
usual case is a plain `./nand id` / `./nand dump` with no manual replug. The
YKUSH XS is only needed when the reader is stuck.

### The power design's status frame

The power design answers a 48-byte `30 XX 00 ...` frame with 4 bytes
`30 XX v_hi v_lo` (e.g. `30 09 00 10` for `30 09`, `30 07 00 00` for `30 07`).
It answers once per boot, only on EP6, and ignores the frame body: a sweep of
all 48 body bytes, the vendor's `0x1ec0/0x240/0x3ff` and `0x4930/0x1000/0x7ff`
structures, and bank-select bytes 1-7 all give the same reply for a given
opcode. The chip design never answers this frame.

### Tried and ruled out (each on a YKUSH cold cycle)

- read length: 4 B, 64 B, and a raw `dev.read(..., 64, 30000)` all give the same answer
- read timeout: the ID arrives in ~40 ms when it arrives at all; 2 s vs 20 s vs 30 s makes no difference
- extra open/claim/close before the real one; reopening the handle
- powering the port off for 1, 3, 6, 10 s before on
- letting the port sit 1.2–3 s after power-up before streaming
- power image before the chip image (always `00 00 00 00` or wedges EP2)
- descriptor bytes 4–7 as `01 00 00 00`, `00…`, `00 00 01 00`, `FF…`
- separate table write then descriptor write, with 5 ms pauses
- single 1.25 MB write, 256 KB chunks, 1 MB chunks (identical)

### What the hardware actually does (2026-11-10, every run on a YKUSH cold cycle)

| Sequence | Answer |
|---|---|
| chip image + table + descriptor, then read 64 B | `89 c4 08 32` |
| same, warmed up first (no immediate stream) | `00 00 00 00` — the config window is missed |
| chip image, then a DAC word, then read | timeout (110) |
| chip image, then a bare `FF FF`, then read | timeout (110) |
| chip image alone, then listen 60 s | nothing |
| power image + DAC word | `4d 80 0f e0` = echo + sample |
| power image + `12 34` | `12 34 02 20` — bytes 0..1 echo the word, 2..3 the sample |
| power image + corrupted (bit-flipped) | `4d 80 0f e0` — **same**, so this proves nothing about configuration |
| power image + DAC word + second word | only the first answers; the second is discarded |
| table + descriptor, no preceding image | times out and wedges EP2 until a power cycle |
| cold stream of the chip image | EP2 stops accepting writes; the reader stays stuck |

The corrupted-image rows are the important ones: a bit-flipped design returns
the same bytes as an intact one, so neither reply is evidence that anything
configured. `89 C4 08 32` cannot come from the SX2 (it is 4 bytes of NAND
vendor/device data), so when it appears the chip design did run. The open
question is what state makes it run, and nothing measured so far predicts it.

The one positive fact: EP2 takes unlimited writes on a cold reader until a
1.25 MB stream lands, and stops immediately after. The endpoint is watching that
transfer.

### One image stream per power-up

The FPGA configures once per boot. After a configuration image has been
streamed, a second stream has nothing to configure into and its trailing read
times out. A timed-out read wedges both endpoints until a physical replug;
neither a USB bus reset nor `clear_halt` clears it. So: one operation per
replug — `id` and `power` cannot share a session.

### Compared with NAND_Reader.exe (static only)

Read out of the vendor binary by disassembly; **not** confirmed against a
working Windows session — the vendor's x64 driver does not run under Wine here.

| What | Vendor | Here |
|---|---|---|
| power design source | embedded at RVA `0x50a8dc`, len `0x13258` | expands to `6c614063` |
| chip design source | embedded at RVA `0x530dcc`, len `0x13258` | expands to `d2c3da70` |
| expansion (`0x424440`) | per byte, 8 words, LSB first, `1→0xFFFF`, drop last 32 B | identical |
| ID table (`0x41edc0`) | 16 `(op,arg)` pairs via `0x418de0` | `ID_TABLE_OPS` |
| pair swap (`0x424fc4`) | even/odd byte swap over 2,048 B | `id_table()` |
| write order | table `0x800`, then `0x20` descriptor | `id_tail()` |
| transport | `WriteFile` vtable+4, `ReadFile` vtable+8 | `_out` / `_in` |
| power down (`0x424ca0`) | write inverted word, read **2** bytes | `power_down()` |

What this does **not** establish: that the descriptor bytes, the table variant
(simplified `0x41edc0` vs full `0x45ed10`), or the stream timing are the ones
the vendor actually uses on the wire. Those are the leading suspects for the
unreproduced ID read.

The DAC word is computed by the caller (`0x424b62`) and passed in — the
`0x4248f0` routine is *stream power design, write word, read 4*. The ADC scale
in `0x424b86` is `×0x14a >> 12`, i.e. `×330 cV`, matching `decode_cv`.

The one thing that held this up was a `SETTLE_S = 0.75` sleep between opening
the device and streaming the bitstream. The FPGA is configured over the FIFO at
power-up and only listens briefly; waiting first missed the window every time,
which looked exactly like a blank part. Stream immediately and it configures on
the first attempt. There is no multi-second settle.

Wire fidelity was checked byte for byte with usbmon: what the kernel submits is
exactly `nand_init.bin` (1,254,752 B in one URB). Text-mode usbmon prints only
a 32-byte preview per frame, so zeros seen after the first words in that view
are truncation, not corruption.

A separate wrong turn: `0xC0`/`0xD0` (the ATtiny13A nonce) and `0xB0` do time
out on this device, but they are the *vendor GUI's* gate, not the device's.
The FIFO path needs no handshake — EP6 answers 30 ms after the DAC word.

The DAC reply is `echo(2) | sample(2, big endian)`. The sample scales as
`sample × 330 cV >> 12`, the same `×0x14a >> 12` the vendor uses.

## Payloads

Both files are in `payloads/` and match the vendor hashes:

| file | bytes | sha256 prefix |
|---|---|---|
| `power_init.bin` | 1,254,752 | `6c614063` |
| `nand_init.bin` | 1,254,752 | `d2c3da70` |

Verified: each is the LSB-first pack of its RBF with a set bit as `0xFFFF`
and a clear bit as `0x0000`, byte for byte. `nand_program.bin` is not needed —
`id_table()` builds the 2,048-byte block from 16 `(op, arg)` pairs and
pair-swaps it, giving sha256 `2b1bc203`.

### Build them from an RBF instead

If you have `factory/fpga/init.lsb.rbf` (78,422 B) but not the packed segments:

```sh
python3 rbf2init.py pack factory/fpga/init.lsb.rbf -o payloads/power_init.bin
python3 rbf2init.py verify payloads/power_init.bin --rbf factory/fpga/init.lsb.rbf
python3 rbf2init.py unpack payloads/power_init.bin -o recovered.rbf   # inverse
```

Packing model (hardware.html section 4): one 16-bit word per config bit, LSB
first, 0xFFFF for a set bit — 78,422 × 16 = 1,254,752 exactly. `verify` checks
size, the 0x00/0xFF-only rule, ~7.9% one-bit density, and RBF round-trip.
`pack` also reports whether the result matches either vendor hash.


`nand_program.bin` is not shipped: the ID leg generates the 2,048-byte block
in `nand_linux.id_table()` (16 `(op, arg)` pairs, then a pair-swap of the
whole block), which reproduces the documented sha256 exactly.

## Use

```sh
./nand check                    # payload status, tells you what is missing
./nand probe                    # safe bring-up probe, no payloads, no rail
./nand info                     # describe the USB device
./nand gui                      # PyQt6 front end
./nand power --cycle --cv 330   # YKUSH cycle, raise rail, read echo, lower
./nand id --cycle               # YKUSH cycle, then read the ID
./nand readp 1000 1010 2040 --out out/   # read single pages (real data)
./nand readp 1013 --votes 15 --out out/  # majority-vote a marginal page clean
./nand dump --cycle --out out/b1.dump --end 4718592 --unit 18592 --bank 1
```

`--cv` is centivolts (330 = 3.30 V). `nand_gui.py` exposes the same operations
with a progress bar and a log pane.

`NAND_IN_TIMEOUT_MS` overrides the read timeout (default 20,000 ms). The vendor
driver submits a bare bulk URB with no timeout, so it waits as long as the FPGA
takes.

### Procedure

Each run needs a fresh power cycle, because only one image stream fits per boot:

```sh
./nand id --cycle                # expect 89 c4 08 32
./nand dump --cycle --out out/b1.dump --end 4718592 --unit 18592 --bank 1
```

`id` prints whatever the device answers. `89 c4 08 32` is the correct ID; a
`00 00 00 00` means the chip design did not configure, and `--cycle` retries
with a fresh power-up. `--retries N` sets the number of cold cycles (default 30
with `--cycle`, else 1).

## Udev

Installed at `/etc/udev/rules.d/99-nand-reader.rules` — device mode `0666`,
group `plugdev`, so no root is needed.

## References

- `index.html` appendix A — transaction maps, descriptor, range control
- `hardware.html` section 2 — EP2/EP6 mapping, ATtiny identity
