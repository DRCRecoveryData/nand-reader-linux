/* capture_pass.dll -- transparent logger for the vendor NAND_Reader.
 *
 * Every device call is forwarded to the real driver, so the tool behaves
 * exactly as it does on a working machine. Each device write is appended to
 * the log named by NAND_CAP as:
 *
 *   W <len> <hex bytes, first 256><\n>
 *   R <len> -> <hex of bytes actually returned><\n>
 *
 * The log is also pushed to stderr so it survives a crash.
 */
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static FILE *g_out;
static HANDLE g_dev;
static int g_depth;

static void log_open(void)
{
    if (g_out) return;
    const char *p = getenv("NAND_CAP");
    g_out = fopen(p ? p : "Z:\\tmp\\nandcap.txt", "w");
}

static void hexdump(const char *tag, const unsigned char *b, DWORD n, DWORD cap)
{
    char line[600];
    int k = 0;
    DWORD i;
    k += snprintf(line + k, sizeof line - k, "%s %lu ", tag, (unsigned long)n);
    for (i = 0; i < n && i < cap && k < (int)sizeof line - 4; i++)
        k += snprintf(line + k, sizeof line - k, "%02x", b[i]);
    if (n > cap) k += snprintf(line + k, sizeof line - k, "...");
    snprintf(line + k, sizeof line - k, "\n");
    if (g_out) { fputs(line, g_out); fflush(g_out); }
    fputs(line, stderr); fflush(stderr);
}

static int is_dev(LPCSTR n) { return n && strstr(n, "NAND_Flash_Reader2"); }

/* Forward through kernel32_real so the wrappers below never recurse. */
typedef HANDLE (WINAPI *p_CFA)(LPCSTR,DWORD,DWORD,LPSECURITY_ATTRIBUTES,DWORD,DWORD,HANDLE);
typedef BOOL   (WINAPI *p_WF)(HANDLE,LPCVOID,DWORD,LPDWORD,LPOVERLAPPED);
typedef BOOL   (WINAPI *p_RF)(HANDLE,LPVOID,DWORD,LPDWORD,LPOVERLAPPED);
typedef BOOL   (WINAPI *p_CH)(HANDLE);

static p_CFA r_cfa; static p_WF r_wf; static p_RF r_rf; static p_CH r_ch;

static void resolve(void)
{
    HMODULE m;
    if (r_cfa) return;
    m = LoadLibraryA("kernel32_real.dll");
    if (!m) m = LoadLibraryA("kernel32.dll");
    r_cfa = (p_CFA)GetProcAddress(m, "CreateFileA");
    r_wf  = (p_WF) GetProcAddress(m, "WriteFile");
    r_rf  = (p_RF) GetProcAddress(m, "ReadFile");
    r_ch  = (p_CH) GetProcAddress(m, "CloseHandle");
}

__declspec(dllexport) HANDLE WINAPI CreateFileA(LPCSTR name, DWORD acc,
        DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disp, DWORD fl, HANDLE t)
{
    HANDLE h; resolve(); h = r_cfa(name, acc, share, sa, disp, fl, t);
    if (is_dev(name)) {
        log_open();
        g_dev = h;
        if (g_out) { fprintf(g_out, "OPEN\n"); fflush(g_out); }
        fprintf(stderr, "OPEN handle=%p\n", (void *)h);
    }
    return h;
}

__declspec(dllexport) BOOL WINAPI WriteFile(HANDLE h, LPCVOID b, DWORD n,
                                            LPDWORD w, LPOVERLAPPED o)
{
    BOOL r; resolve(); r = r_wf(h, b, n, w, o);
    if (h == g_dev && !g_depth++) {
        log_open();
        hexdump("W", (const unsigned char *)b, n, 256);
        g_depth--;
    } else if (h == g_dev) g_depth--;
    return r;
}

__declspec(dllexport) BOOL WINAPI ReadFile(HANDLE h, LPVOID b, DWORD n,
                                           LPDWORD rn, LPOVERLAPPED o)
{
    BOOL r; resolve(); r = r_rf(h, b, n, rn, o);
    if (h == g_dev) {
        log_open();
        char tag[32];
        snprintf(tag, sizeof tag, "R(%lu)->", (unsigned long)(rn ? *rn : 0));
        hexdump(tag, (const unsigned char *)b, rn ? *rn : 0, 64);
    }
    return r;
}

__declspec(dllexport) BOOL WINAPI CloseHandle(HANDLE h)
{
    if (h == g_dev && h != INVALID_HANDLE_VALUE) {
        if (g_out) { fputs("CLOSE\n", g_out); fclose(g_out); g_out = NULL; }
        g_dev = NULL;
    }
    resolve(); return r_ch(h);
}

BOOL WINAPI DllMain(HINSTANCE i, DWORD r, LPVOID p)
{
    (void)i; (void)r; (void)p;
    return TRUE;
}
