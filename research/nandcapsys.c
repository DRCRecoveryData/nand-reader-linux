/* nandcapsys.dll -- capture shim loaded in place of kernel32 for the vendor
 * NAND_Reader.exe.  Everything except the device handle forwards to the real
 * kernel32.dll (loaded from a renamed copy so we do not recurse).
 *
 * Device I/O is transcribed to NAND_CAP (default C:\nandcap.bin):
 *   OPEN
 *   W <len raw bytes>
 *   R <len>
 *   CLOSE
 */
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static HMODULE g_real;
static HANDLE  g_dev = (HANDLE)(ULONG_PTR)0x4E414E44;
static FILE   *g_out;

typedef HANDLE (WINAPI *pCreateFileA)(LPCSTR,DWORD,DWORD,LPSECURITY_ATTRIBUTES,DWORD,DWORD,HANDLE);
typedef BOOL   (WINAPI *pWriteFile)(HANDLE,LPCVOID,DWORD,LPDWORD,LPOVERLAPPED);
typedef BOOL   (WINAPI *pReadFile)(HANDLE,LPVOID,DWORD,LPDWORD,LPOVERLAPPED);
typedef BOOL   (WINAPI *pCloseHandle)(HANDLE);

static pCreateFileA fCreateFileA;
static pWriteFile   fWriteFile;
static pReadFile    fReadFile;
static pCloseHandle fCloseHandle;

static void init_real(void)
{
    if (g_real) return;
    g_real = LoadLibraryA("kernel32_real.dll");
    if (!g_real) g_real = LoadLibraryA("C:\\windows\\system32\\kernel32_real.dll");
    fCreateFileA = (pCreateFileA)GetProcAddress(g_real, "CreateFileA");
    fWriteFile   = (pWriteFile)  GetProcAddress(g_real, "WriteFile");
    fReadFile    = (pReadFile)   GetProcAddress(g_real, "ReadFile");
    fCloseHandle = (pCloseHandle)GetProcAddress(g_real, "CloseHandle");
}

static void open_log(void)
{
    if (!g_out) {
        const char *p = getenv("NAND_CAP");
        g_out = fopen(p ? p : "C:\\nandcap.bin", "wb");
    }
}

static int is_dev(LPCSTR n) { return n && strstr(n, "NAND_Flash_Reader2"); }

static unsigned char g_tail[2];
static unsigned g_last;

static void log_line(const char *fmt, ...)
{
    va_list ap;
    open_log();
    if (!g_out) return;
    va_start(ap, fmt);
    vfprintf(g_out, fmt, ap);
    va_end(ap);
    fflush(g_out);
}

__declspec(dllexport) HANDLE WINAPI CreateFileA(LPCSTR name, DWORD acc,
        DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disp, DWORD fl, HANDLE t)
{
    if (is_dev(name)) { log_line("OPEN\n"); return g_dev; }
    log_line("CFA %s\n", name ? name : "(null)");
    init_real(); return fCreateFileA(name, acc, share, sa, disp, fl, t);
}

__declspec(dllexport) BOOL WINAPI WriteFile(HANDLE h, LPCVOID b, DWORD n,
                                            LPDWORD w, LPOVERLAPPED o)
{
    if (h == g_dev) {
        open_log();
        if (g_out) { fprintf(g_out, "W %lu ", (unsigned long)n); fwrite(b,1,n,g_out); fputc('\n',g_out); fflush(g_out); }
        if (n >= 2) { g_tail[0] = ((const unsigned char*)b)[n-2]; g_tail[1] = ((const unsigned char*)b)[n-1]; }
        g_last = n;
        if (w) *w = n;
        return TRUE;
    }
    init_real(); return fWriteFile(h, b, n, w, o);
}

__declspec(dllexport) BOOL WINAPI ReadFile(HANDLE h, LPVOID b, DWORD n,
                                           LPDWORD r, LPOVERLAPPED o)
{
    if (h == g_dev) {
        /* the power design echoes the last two bytes written onto the bus */
        memset(b, 0, n);
        if (n >= 2 && g_last >= 2) { ((unsigned char*)b)[0]=g_tail[0]; ((unsigned char*)b)[1]=g_tail[1]; }
        if (g_out) { fprintf(g_out, "R %lu\n", (unsigned long)n); fflush(g_out); }
        if (r) *r = n;
        return TRUE;
    }
    init_real(); return fReadFile(h, b, n, r, o);
}

__declspec(dllexport) BOOL WINAPI CloseHandle(HANDLE h)
{
    if (h == g_dev) {
        if (g_out) { fputs("CLOSE\n", g_out); fclose(g_out); g_out = NULL; }
        return TRUE;
    }
    init_real(); return fCloseHandle(h);
}

/* WinUsb path: the tool issues CONTROL / BULK_IN / BULK_OUT through
 * DeviceIoControl on the reader handle. Record code, length and payload so a
 * Windows run yields the same transcript nand_linux.py should reproduce. */
__declspec(dllexport) BOOL WINAPI DeviceIoControl(HANDLE h, DWORD code,
        LPVOID in, DWORD inlen, LPVOID out, DWORD outlen, LPDWORD ret, LPOVERLAPPED o)
{
    if (h == g_dev) {
        open_log();
        if (g_out) {
            fprintf(g_out, "IOCTL %lx in=%lu outlen=%lu ", (unsigned long)code,
                    (unsigned long)inlen, (unsigned long)outlen);
            if (in && inlen && inlen <= 8192) fwrite(in, 1, inlen, g_out);
            fputc('\n', g_out);
            fflush(g_out);
        }
        if (out && outlen) memset(out, 0, outlen);
        if (out && outlen >= 2) { ((unsigned char*)out)[0] = 0xDB; ((unsigned char*)out)[1] = 0x0F; }
        if (ret) *ret = outlen;
        return TRUE;
    }
    return FALSE;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID r)
{
    (void)inst; (void)r; (void)reason;
    return TRUE;
}
