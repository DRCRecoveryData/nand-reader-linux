/* erun.exe VAR=value... prog.exe [args] -- set the environment, then run. */
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    int i;
    char cmd[4096] = {0};
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    for (i = 1; i < argc && strchr(argv[i], '='); i++)
        SetEnvironmentVariableA(argv[i], strchr(argv[i], '=') + 1);
    for (; i < argc; i++) {
        if (cmd[0]) strcat(cmd, " ");
        strcat(cmd, strchr(argv[i], ' ') ? "\"" : "");
        strcat(cmd, argv[i]);
        if (strchr(argv[i], ' ')) strcat(cmd, "\"");
    }
    if (!cmd[0]) return 2;
    memset(&si, 0, sizeof si);
    si.cb = sizeof si;
    if (!CreateProcessA(NULL, cmd, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        fprintf(stderr, "create %lu\n", GetLastError());
        return 1;
    }
    WaitForSingleObject(pi.hProcess, 30000);
    return 0;
}
