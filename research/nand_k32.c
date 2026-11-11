/* nand_k32.c -- device entry points of the NAND_Reader.exe Wine shim.
 *
 * The vendor tool opens \\.\NAND_Flash_Reader2 through a kernel driver Wine
 * cannot provide.  These wrappers answer that handle and relay writes/reads to
 * nanddev_proxy.py over AF_UNIX (Z:\tmp\nanddev.sock), which drives the
 * physical reader through libusb.  All other kernel32 exports are forwarders
 * in fwd.c.
 *
 * Wire protocol (little endian lengths):
 *   client -> proxy  'W' u32 len payload     vendor WriteFile
 *                    'R' u32 len             vendor ReadFile
 *   proxy  -> client u8 0                    write done
 *                    u32 len + bytes         read done
 */
#include <winsock2.h>
#include <windows.h>

static void *k_real[107];
__declspec(dllexport) void *k_real_export(void) { return k_real; }

#define SOCK_PATH "Z:\\tmp\\nanddev.sock"
#define LOG_PATH "Z:\\tmp\\nanddev_client.log"
#define DEV_HANDLE ((HANDLE)(ULONG_PTR)0x4E414E44)

static SOCKET g_sock;

static void log_(const char *m, unsigned long a)
{
    HANDLE h = ((HANDLE (WINAPI *)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD,
                                   DWORD, HANDLE))k_real[102])
        (LOG_PATH, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
         OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    char buf[96];
    int n = 0;
    while (m[n]) { buf[n] = m[n]; n++; }
    buf[n++] = ' ';
    for (int sh = 24; sh >= 0; sh -= 8) {
        unsigned v = (a >> sh) & 0xff;
        buf[n++] = v < 10 ? '0' + v : 'a' + v - 10;
    }
    buf[n++] = '\n';
    if (h != INVALID_HANDLE_VALUE) {
        DWORD wr;
        ((DWORD (WINAPI *)(HANDLE, LONG, PLONG, DWORD))k_real[59])(h, 0, NULL, FILE_END);
        ((BOOL (WINAPI *)(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED))k_real[4])
            (h, buf, n, &wr, NULL);
        ((BOOL (WINAPI *)(HANDLE))k_real[68])(h);
    }
}

static int proxy(void)
{
    static WSADATA w;
    static const struct sockaddr_un_ { unsigned short f; char p[108]; } u =
        { 1, SOCK_PATH };
    if (g_sock) return 1;
    WSAStartup(0x0202, &w);
    g_sock = socket(1, 1, 0);
    if (g_sock == INVALID_SOCKET) { log_("socket", 0); return 0; }
    if (connect(g_sock, (const struct sockaddr *)&u, sizeof u) != 0) {
        log_("connect", 0);
        g_sock = 0;
        return 0;
    }
    return 1;
}

static int all(int s, const char *b, int n, int send_)
{
    while (n > 0) {
        int k = send_ ? send(s, b, n, 0) : recv(s, (char *)b, n, 0);
        if (k <= 0) return -1;
        b += k; n -= k;
    }
    return 0;
}

static int is_dev(LPCSTR n) { return n && n[0] == '\\' && n[1] == '\\' && n[2] == '.' &&
                                     n[3] == '\\' && n[4] == 'N'; }

__declspec(dllexport) HANDLE WINAPI n_CreateFileA(LPCSTR name, DWORD acc,
        DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disp, DWORD fl, HANDLE t)
{
    if (is_dev(name)) {
        log_("OPEN", acc);
        return DEV_HANDLE;
    }
    return ((HANDLE (WINAPI *)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD,
                               DWORD, HANDLE))k_real[102])(name, acc, share, sa, disp, fl, t);
}

__declspec(dllexport) BOOL WINAPI n_WriteFile(HANDLE h, LPCVOID b, DWORD n,
                                              LPDWORD w, LPOVERLAPPED o)
{
    char hdr[5];
    char ok;
    if (h != DEV_HANDLE)
        return ((BOOL (WINAPI *)(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED))
                k_real[4])(h, b, n, w, o);
    if (!proxy()) return FALSE;
    hdr[0] = 'W';
    *(DWORD *)(hdr + 1) = n;
    log_("W", n);
    if (all(g_sock, hdr, 5, 1) || all(g_sock, (const char *)b, (int)n, 1) ||
        all(g_sock, &ok, 1, 0)) {
        log_("W failed", n);
        return FALSE;
    }
    if (w) *w = n;
    return TRUE;
}

__declspec(dllexport) BOOL WINAPI n_ReadFile(HANDLE h, LPVOID b, DWORD n,
                                             LPDWORD rn, LPOVERLAPPED o)
{
    char hdr[5];
    DWORD got = 0;
    if (h != DEV_HANDLE)
        return ((BOOL (WINAPI *)(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED))
                k_real[70])(h, b, n, rn, o);
    if (!proxy()) return FALSE;
    hdr[0] = 'R';
    *(DWORD *)(hdr + 1) = n;
    if (all(g_sock, hdr, 5, 1) || all(g_sock, (char *)&got, 4, 0)) {
        log_("R failed", n);
        return FALSE;
    }
    if (got > n) got = n;
    if (got && all(g_sock, (char *)b, (int)got, 0)) {
        log_("R body failed", got);
        return FALSE;
    }
    log_("R", got);
    if (rn) *rn = got;
    return got > 0;
}

__declspec(dllexport) BOOL WINAPI n_CloseHandle(HANDLE h)
{
    if (h == DEV_HANDLE) {
        log_("CLOSE", 0);
        return TRUE;
    }
    return ((BOOL (WINAPI *)(HANDLE))k_real[68])(h);
}

