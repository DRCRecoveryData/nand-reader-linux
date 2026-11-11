/* rundll.exe <dll> <export> -- LoadLibrary + call the export in-process. */
#include <windows.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    HMODULE h;
    FARPROC p;
    if (argc < 3) return 2;
    h = LoadLibraryA(argv[1]);
    if (!h) { fprintf(stderr, "load %s failed %lu\n", argv[1], GetLastError()); return 1; }
    p = GetProcAddress(h, argv[2]);
    if (!p) { fprintf(stderr, "export %s missing\n", argv[2]); return 1; }
    ((void (WINAPI *)(void))p)();
    return 0;
}
