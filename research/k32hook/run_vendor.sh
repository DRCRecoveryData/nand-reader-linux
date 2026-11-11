#!/bin/bash
# Launch the vendor GUI under Wine, inject k32hook.dll, patch its IAT.
# Wine PIDs differ from host PIDs; NAND_Reader's Wine pid is read from tasklist.
set -u
PREFIX=${PREFIX:-/tmp/cap4}
NR=$PREFIX/drive_c/nr
HERE=$(dirname "$(readlink -f "$0")")
export WINEPREFIX=$PREFIX DISPLAY=${DISPLAY:-:0.0} WINEDEBUG=-all
LOG=$NR/nanddev_client.log

pkill -x NAND_Reader.exe 2>/dev/null && sleep 1
wineserver -k 2>/dev/null; sleep 1
cp "$HERE/k32hook.dll" "$NR/k32hook.dll"
rm -f "$LOG"
: > "$LOG"

( cd "$NR" && setsid nohup wine NAND_Reader.exe >/tmp/vendor.out 2>&1 & )
for _ in $(seq 1 60); do
    WPID=$(wine tasklist.exe 2>/dev/null | awk '/NAND_Reader/ {print $2}')
    [ -n "${WPID:-}" ] && break
    sleep 1
done
[ -z "${WPID:-}" ] && { echo "vendor did not start"; exit 1; }
HPID=$(pgrep -x NAND_Reader.exe | head -1)
echo "wine pid=$WPID host pid=$HPID"

( cd "$NR" && wine ./inject.exe "$WPID" 'Z:\tmp\cap4\drive_c\nr\k32hook.dll' )
sleep 1
sudo -n python3 "$HERE/rootpatch.py" "$HPID"
echo "--- shim log ---"
cat "$LOG"; echo
