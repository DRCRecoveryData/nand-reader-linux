#!/bin/bash
# Restart the pipe transport chain: nanddev proxy (if needed), bridge + pipesrv.
set -u
cd /home/drclab/nand/research
if ! ss -xl 2>/dev/null | grep -q nanddev.sock; then
    rm -f /tmp/nanddev.sock
    NAND_DEV_NOCYCLE=1 setsid nohup python3 -u nanddev_proxy.py > /tmp/proxy.out 2>&1 < /dev/null &
    sleep 2
fi
cd k32hook
export WINEPREFIX=/tmp/cap4 DISPLAY=:0.0 WINEDEBUG=-all NAND_DEV_NOCYCLE=1
unset WINELOADERNOEXEC
setsid nohup python3 -u pipe_bridge.py > /tmp/bridge.out 2>&1 < /dev/null &
sleep 3
cat /tmp/bridge.out
