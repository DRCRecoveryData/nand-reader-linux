/* Capture shim: proxy CreateFileA/WriteFile/ReadFile for \\.\NAND_Flash_Reader2.
 *
 * The vendor tool opens the device, streams a 1,254,752-byte FPGA image, then a
 * 2-byte DAC word, then reads 2/4 bytes back.  We record every write into a
 * transcript file and answer reads with a plausible echo so the tool progresses.
 *
 * Written as a DLL that replaces the real one via WINEDLLOVERRIDES, loaded by the
 * process before kernel32 resolves its imports.
 */
#include <windows.h>
#include <stdio.h>
#include <string.h>

static HANDLE g_dev = (HANDLE)(ULONG_PTR)0x4E414E44; /* fake "NAND" handle */
static FILE *g_out;
static int g_writes;

static void open_log(void)
{
    if (!g_out) {
        const char *p = getenv("NAND_CAP");
        g_out = fopen(p ? p : "C:\\nandcap.bin", "wb");
    }
}

static int is_dev(LPCSTR n)
{
    return n && strstr(n, "NAND_Flash_Reader2");
}

HANDLE WINAPI CreateFileA(LPCSTR name, DWORD access, DWORD share,
                          LPSECURITY_ATTRIBUTES sa, DWORD disp,
                          DWORD flags, HANDLE tmpl)
{
    if (is_dev(name)) {
        open_log();
        if (g_out) fprintf(g_out, "OPEN\n");
        return g_dev;
    }
}

BOOL WINAPI WriteFile(HANDLE h, LPCVOID buf, DWORD n, LPDWORD written,
                      LPOVERLAPPED ov)
{
    if (h == g_dev) {
        open_log();
        if (g_out) {
            fprintf(g_out, "W %lu ", (unsigned long)n);
            fwrite(buf, 1, n, g_out);
            fprintf(g_out, "\n");
            fflush(g_out);
        }
        g_writes++;
        if (written) *written = n;
        return TRUE;
    }
}

BOOL WINAPI ReadFile(HANDLE h, LPVOID buf, DWORD n, LPDWORD read,
                     LPOVERLAPPED ov)
{
    if (h == g_dev) {
        /* echo: reply with the last DAC-style value so the tool keeps going */
        memset(buf, 0, n);
        if (n >= 2) { ((unsigned char *)buf)[0] = 0xDB; ((unsigned char *)buf)[1] = 0x0F; }
        if (g_out) { fprintf(g_out, "R %lu\n", (unsigned long)n); fflush(g_out); }
        if (read) *read = n;
        return TRUE;
    }
}

BOOL WINAPI CloseHandle(HANDLE h)
{
    if (h == g_dev) {
        if (g_out) { fprintf(g_out, "CLOSE\n"); fclose(g_out); g_out = NULL; }
        return TRUE;
    }
}
