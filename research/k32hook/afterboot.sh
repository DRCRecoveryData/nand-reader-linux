#!/bin/bash
# Run once after the reboot that clears the stalled Wine state: start the
# device proxy, launch the vendor tool through the import redirect and leave
# the session ready for Read ID / Dump in the GUI.
LOG=/home/drclab/nand/research/k32hook/afterboot.log
exec >>"$LOG" 2>&1
echo "=== $(date) afterboot"
rm -f ~/.config/autostart/nand-capture.desktop
export DISPLAY=${DISPLAY:-:0.0}
cd /home/drclab/nand/research
if ! pgrep -f nanddev_proxy.py >/dev/null; then
    NAND_DEV_NOCYCLE=1 NAND_DEV_LOG=/tmp/nanddev.log setsid nohup python3 -u nanddev_proxy.py < /dev/null &
    sleep 1
fi
cd /home/drclab/nand/research/k32hook
timeout 120 python3 -u licr.py --detach > licr.out
echo "licr rc=$?"
