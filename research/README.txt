nand_linux.py and nand_gui.py drive the reader directly with libusb; the vendor
Windows tool, Wine and the kernel32 interpose experiments are not used.

shim-interpose/  kernel32-stand-in DLL that rewired NAND_Reader.exe to nanddrv.dll
                 (USB 03eb:5903 was never visible to Wine, so the vendor tool
                 could not reach the reader; kept for reference only)
