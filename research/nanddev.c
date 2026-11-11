/* nanddev.dll -- kernel32 stand-in for NAND_Reader.exe under Wine.
 *
 * The vendor tool talks to \\.\NAND_Flash_Reader2 through a kernel driver that
 * Wine cannot provide.  Device calls are forwarded instead to nanddev_proxy.py
 * over a unix socket (NAND_DEV_SOCK, binary hidden under C:\tmp\nanddev.sock),
 * which drives the physical reader through libusb.  Everything else forwards
 * to kernel32_real.dll.
 *
 * Wire (little endian lengths):
 *   client -> proxy   'W' len payload          vendor WriteFile
 *                     'R' len                  vendor ReadFile
 *   proxy  -> client  1 byte 0                 write done
 *                     u32 len + bytes          read done
 */
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <winsock2.h>

#define SOCK_PATH "Z:\\tmp\\nanddev.sock"

static HMODULE g_real;
static HANDLE g_dev;
static SOCKET g_sock = INVALID_SOCKET;
static FILE *g_log;

typedef HANDLE (WINAPI *pCreateFileA)(LPCSTR,DWORD,DWORD,LPSECURITY_ATTRIBUTES,DWORD,DWORD,HANDLE);
typedef BOOL   (WINAPI *pWriteFile)(HANDLE,LPCVOID,DWORD,LPDWORD,LPOVERLAPPED);
typedef BOOL   (WINAPI *pReadFile)(HANDLE,LPVOID,DWORD,LPDWORD,LPOVERLAPPED);
typedef BOOL   (WINAPI *pCloseHandle)(HANDLE);

static pCreateFileA r_cfa; static pWriteFile r_wf;
static pReadFile r_rf; static pCloseHandle r_ch;


static void resolve(void)
{
    if (g_real) return;
    g_real = LoadLibraryA("kernel32_real.dll");
    if (!g_real) g_real = LoadLibraryA("C:\\windows\\system32\\kernel32_real.dll");
    r_cfa = (pCreateFileA)GetProcAddress(g_real, "CreateFileA");
    r_wf  = (pWriteFile)  GetProcAddress(g_real, "WriteFile");
    r_rf  = (pReadFile)   GetProcAddress(g_real, "ReadFile");
    r_ch  = (pCloseHandle)GetProcAddress(g_real, "CloseHandle");
}

static void logf_(const char *fmt, ...)
{
    va_list ap;
    if (!g_log) g_log = fopen("C:\\tmp\\nanddev_client.log", "a");
    if (!g_log) return;
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fflush(g_log);
}

/* AF_UNIX inside Wine is marshalled to the host by wine's unixlib, so the
 * shim and the proxy can share a plain socket file. */
static const struct sockaddr_un_ {
    unsigned short sun_family;
    char sun_path[108];
} UN = { 1, SOCK_PATH };

static SOCKET proxy_connect(void)
{
    static WSADATA wsastart;
    SOCKET s;
    if (g_sock != INVALID_SOCKET) return g_sock;
    WSAStartup(0x0202, &wsastart);
    s = socket(1 /*AF_UNIX*/, 1 /*SOCK_STREAM*/, 0);
    if (s == INVALID_SOCKET) { logf_("socket failed\n"); return s; }
    if (connect(s, (const struct sockaddr *)&UN, sizeof UN) != 0) {
        logf_("proxy connect failed\n");
        return INVALID_SOCKET;
    }
    g_sock = s;
    return s;
}

static int send_all(SOCKET s, const char *b, int n)
{
    while (n > 0) {
        int k = send(s, b, n, 0);
        if (k <= 0) return -1;
        b += k; n -= k;
    }
    return 0;
}

static int recv_all(SOCKET s, char *b, int n)
{
    while (n > 0) {
        int k = recv(s, b, n, 0);
        if (k <= 0) return -1;
        b += k; n -= k;
    }
    return 0;
}

static int is_dev(LPCSTR n) { return n && strstr(n, "NAND_Flash_Reader2"); }

__declspec(dllexport) HANDLE WINAPI CreateFileA(LPCSTR name, DWORD acc,
        DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disp, DWORD fl, HANDLE t)
{
    if (is_dev(name)) {
        logf_("OPEN %s\n", name);
        g_dev = (HANDLE)(ULONG_PTR)0x4E414E44;
        return g_dev;
    }
    resolve(); return r_cfa(name, acc, share, sa, disp, fl, t);
}

__declspec(dllexport) BOOL WINAPI WriteFile(HANDLE h, LPCVOID b, DWORD n,
                                            LPDWORD w, LPOVERLAPPED o)
{
    SOCKET s; char hdr[5]; char ok;
    if (h == g_dev) {
        s = proxy_connect();
        if (s == INVALID_SOCKET) { if (w) *w = 0; return FALSE; }
        hdr[0] = 'W';
        memcpy(hdr + 1, &n, 4);
        logf_("W %lu %02x%02x\n", (unsigned long)n,
              n ? ((const unsigned char *)b)[0] : 0, n > 1 ? ((const unsigned char *)b)[1] : 0);
        if (send_all(s, hdr, 5) || send_all(s, (const char *)b, (int)n) ||
            recv_all(s, &ok, 1)) { if (w) *w = 0; return FALSE; }
        if (w) *w = n;
        return TRUE;
    }
    resolve(); return r_wf(h, b, n, w, o);
}

__declspec(dllexport) BOOL WINAPI ReadFile(HANDLE h, LPVOID b, DWORD n,
                                           LPDWORD rn, LPOVERLAPPED o)
{
    SOCKET s; char hdr[5]; unsigned got = 0;
    if (h == g_dev) {
        s = proxy_connect();
        if (s == INVALID_SOCKET) { if (rn) *rn = 0; return FALSE; }
        hdr[0] = 'R';
        memcpy(hdr + 1, &n, 4);
        if (send_all(s, hdr, 5) || recv_all(s, (char *)&got, 4)) {
            if (rn) *rn = 0; return FALSE;
        }
        if (got > n) got = n;
        if (got && recv_all(s, (char *)b, (int)got)) { if (rn) *rn = 0; return FALSE; }
        logf_("R %lu -> %u\n", (unsigned long)n, got);
        if (rn) *rn = got;
        return got > 0;
    }
    resolve(); return r_rf(h, b, n, rn, o);
}

__declspec(dllexport) BOOL WINAPI CloseHandle(HANDLE h)
{
    if (h == g_dev) { logf_("CLOSE\n"); g_dev = NULL; return TRUE; }
    resolve(); return r_ch(h);
}

BOOL WINAPI DllMain(HINSTANCE i, DWORD r, LPVOID p)
{
    (void)i; (void)r; (void)p;
    return TRUE;
}
