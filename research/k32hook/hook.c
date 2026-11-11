/* k32hook.dll -- device interposer for NAND_Reader.exe under Wine.
 *
 * Wine has no \\.\NAND_Flash_Reader2 device.  Loaded into the vendor process
 * (rundll32 entry or injection), DllMain rewrites the process import table:
 * the six I/O names resolved from KERNEL32.dll are pointed at the wrappers
 * below.  A matching device name is answered here and relayed to
 * nanddev_proxy.py over AF_UNIX; every other call goes to the original
 * kernel32 function unchanged.
 *
 * Wire: 'O' open | 'W' u32 len bytes | 'R' u32 len | 'C' close
 *       reply: 6 zero bytes (open) | 1 byte (write) | u32 len bytes (read)
 */
#include <winsock2.h>
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define PIPE_PATH "\\\\.\\pipe\\nanddev"
#define LOG_PATH "C:\\nr\\nanddev_client.log"
#define DEV_HANDLE ((HANDLE)(ULONG_PTR)0x4E414E44)

typedef HANDLE (WINAPI *pCFA)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
typedef HANDLE (WINAPI *pCFW)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
typedef BOOL (WINAPI *pWF)(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED);
typedef BOOL (WINAPI *pRF)(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
typedef BOOL (WINAPI *pCH)(HANDLE);
typedef BOOL (WINAPI *pDIC)(HANDLE, DWORD, LPVOID, DWORD, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);

static pCFA r_cfa; static pCFW r_cfw; static pWF r_wf;
static pRF r_rf; static pCH r_ch; static pDIC r_dic;
static HANDLE g_pipe = INVALID_HANDLE_VALUE;
static int g_hooked;
static void log_(const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    int n;
    DWORD w;
    HANDLE h;
    va_start(ap, fmt);
    n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n <= 0) return;
    if (n > (int)sizeof buf - 1) n = sizeof buf - 1;
    h = r_cfa ? r_cfa(LOG_PATH, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                      NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL) : INVALID_HANDLE_VALUE;
    if (h == INVALID_HANDLE_VALUE) return;
    r_wf(h, buf, (DWORD)n, &w, NULL);
    r_ch(h);
}

static int is_dev(const char *n)
{
    return n && n[0] == '\\' && n[1] == '\\' && n[2] == '.' && n[3] == '\\' &&
           (n[4] == 'N' || n[4] == 'n') && (n[5] == 'A' || n[5] == 'a');
}

static int is_dev_w(const WCHAR *n)
{
    return n && n[0] == '\\' && n[1] == '\\' && n[2] == '.' && n[3] == '\\' &&
           (n[4] == 'N' || n[4] == 'n') && (n[5] == 'A' || n[5] == 'a');
}

static int proxy(void)
{
    char b[96];
    if (g_pipe != INVALID_HANDLE_VALUE) return 1;
    /* Wine 9 ws2_32 rejects AF_UNIX (10047); use a named pipe instead. */
    while (1) {
        g_pipe = CreateFileA(PIPE_PATH, GENERIC_READ | GENERIC_WRITE, 0, NULL,
                             OPEN_EXISTING, 0, NULL);
        if (g_pipe != INVALID_HANDLE_VALUE) return 1;
        if (GetLastError() != ERROR_PIPE_BUSY) {
            snprintf(b, sizeof b, "pipe err %lu", GetLastError()); log_(b);
            return 0;
        }
        if (!WaitNamedPipeA(PIPE_PATH, 2000)) { log_("pipe wait timeout"); return 0; }
    }
}

static int all(char *b, int n, int send_)
{
    DWORD done;
    while (n > 0) {
        if (send_ ? !WriteFile(g_pipe, b, n, &done, NULL)
                  : !ReadFile(g_pipe, b, n, &done, NULL) || !done)
            return -1;
        b += done;
        n -= done;
    }
    return 0;
}

/* Set by the wrapper that first runs after the loader finished: from then on
 * the IAT is final and each thread parks once, giving the external patcher
 * time to rewrite it before any of the hooked functions is called. */
static int t_settled;
/* Exported for the external patcher: a function name resolves to code, an
 * exported variable's address slot holds the address of the variable. */
__declspec(dllexport) LONG BusyFlag;

static void settle(void)
{
    char ok;
    int spins;
    if (t_settled) return;
    t_settled = 1;
    if (g_pipe == INVALID_HANDLE_VALUE && !proxy()) return;
    if (all("B", 1, 1) || all(&ok, 1, 0)) return;
    for (spins = 0; InterlockedCompareExchange(&BusyFlag, 1, 1) == 0 && spins < 5000; spins++)
        Sleep(1);
    log_("settled after %d ms", spins);
}

static HANDLE dev_open(void)
{
    char ok[6];
    if (!proxy()) return INVALID_HANDLE_VALUE;
    if (all("O", 1, 1) || all(ok, 6, 0)) {
        CloseHandle(g_pipe);
        g_pipe = INVALID_HANDLE_VALUE;
        return INVALID_HANDLE_VALUE;
    }
    return DEV_HANDLE;
}

static HANDLE WINAPI h_CreateFileA(LPCSTR n, DWORD a, DWORD s, LPSECURITY_ATTRIBUTES sa,
                                   DWORD d, DWORD f, HANDLE t)
{
    { char b[160]; snprintf(b, sizeof b, "CFA %.150s", n ? n : "(null)"); log_(b); }
    if (is_dev(n)) {
        HANDLE h;
        log_("OPEN %s acc=%lx share=%lx", n, a, s);
        h = dev_open();
        if (h != INVALID_HANDLE_VALUE) return h;
        SetLastError(ERROR_ACCESS_DENIED);
        return INVALID_HANDLE_VALUE;
    }
    return r_cfa(n, a, s, sa, d, f, t);
}

static HANDLE WINAPI h_CreateFileW(LPCWSTR n, DWORD a, DWORD s, LPSECURITY_ATTRIBUTES sa,
                                   DWORD d, DWORD f, HANDLE t)
{
    if (is_dev_w(n)) {
        HANDLE h;
        log_("OPENW acc=%lx share=%lx", a, s);
        h = dev_open();
        if (h != INVALID_HANDLE_VALUE) return h;
        SetLastError(ERROR_ACCESS_DENIED);
        return INVALID_HANDLE_VALUE;
    }
    return r_cfw ? r_cfw(n, a, s, sa, d, f, t)
                 : (SetLastError(ERROR_PROC_NOT_FOUND), INVALID_HANDLE_VALUE);
}

static BOOL WINAPI h_WriteFile(HANDLE h, LPCVOID b, DWORD n, LPDWORD w, LPOVERLAPPED o)
{
    char hdr[5];
    char ok = 0;
    if (h != DEV_HANDLE) return r_wf(h, b, n, w, o);
    settle();
    if (w) *w = 0;
    if (g_pipe == INVALID_HANDLE_VALUE && !proxy()) return FALSE;
    hdr[0] = 'W';
    memcpy(hdr + 1, &n, 4);
    log_("W %lu", (unsigned long)n);
    if (all(hdr, 5, 1) || (n && all((char *)b, (int)n, 1)) || all(&ok, 1, 0)) return FALSE;
    if (w) *w = n;
    return TRUE;
}

static BOOL WINAPI h_ReadFile(HANDLE h, LPVOID b, DWORD n, LPDWORD rn, LPOVERLAPPED o)
{
    char hdr[5];
    DWORD got = 0;
    if (h != DEV_HANDLE) return r_rf(h, b, n, rn, o);
    settle();
    if (rn) *rn = 0;
    if (g_pipe == INVALID_HANDLE_VALUE && !proxy()) return FALSE;
    hdr[0] = 'R';
    memcpy(hdr + 1, &n, 4);
    if (all(hdr, 5, 1) || all((char *)&got, 4, 0)) return FALSE;
    if (got > n) got = n;
    if (got && all((char *)b, (int)got, 0)) return FALSE;
    log_("R %lu -> %lu", (unsigned long)n, (unsigned long)got);
    if (rn) *rn = got;
    return got > 0;
}

static BOOL WINAPI h_CloseHandle(HANDLE h)
{
    if (h != DEV_HANDLE) return r_ch(h);
    settle();
    log_("CLOSE");
    /* keep the pipe open across C: the proxy accepts O after C, and
     * reopening the named pipe from the same process races the server. */
    if (g_pipe != INVALID_HANDLE_VALUE) all("C", 1, 1);
    return TRUE;
}

static BOOL WINAPI h_DeviceIoControl(HANDLE h, DWORD c, LPVOID in, DWORD il, LPVOID out,
                                     DWORD ol, LPDWORD ret, LPOVERLAPPED ov)
{
    if (h != DEV_HANDLE)
    {
        BOOL r = r_dic ? r_dic(h, c, in, il, out, ol, ret, ov) : FALSE;
        settle();
        return r;
    }
    log_("IOCTL %lx in=%lu out=%lu", c, (unsigned long)il, (unsigned long)ol);
    if (out && ol) memset(out, 0, ol);
    if (ret) *ret = ol;
    return TRUE;
}

/* ---- import-table rewrite of the vendor process ---- */

struct hook { const char *name; void *fn; };

static const struct hook g_hooks[] = {
    { "CreateFileA", (void *)h_CreateFileA },
    { "CreateFileW", (void *)h_CreateFileW },
    { "WriteFile", (void *)h_WriteFile },
    { "ReadFile", (void *)h_ReadFile },
    { "CloseHandle", (void *)h_CloseHandle },
    { "DeviceIoControl", (void *)h_DeviceIoControl },
};

static void *g_k32;

/* Lowercase compare that stops at NUL. */
static int ci_eq(const char *a, const char *b)
{
    for (;;) {
        char x = *a++, y = *b++;
        if (x >= 'A' && x <= 'Z') x += 32;
        if (y >= 'A' && y <= 'Z') y += 32;
        if (x != y) return 0;
        if (!x) return 1;
    }
}

/* The image that owns addr: PE images sit at (or just above) an aligned base;
 * kernel32 in Wine loads at 0x7bc00000 + 0x1000, so walking down page by page
 * until an MZ header appears finds the base without Toolhelp. */
static void *image_of(void *addr)
{
    char *p = (char *)addr;
    int i;
    for (i = 0; i < 0x10000; i += 0x1000) {
        char *q = p - i;
        MEMORY_BASIC_INFORMATION mbi;
        if (VirtualQuery(q, &mbi, sizeof mbi) != sizeof mbi) return NULL;
        if (mbi.Type != MEM_PRIVATE && mbi.Type != MEM_IMAGE) return NULL;
        if (mbi.AllocationBase && (char *)mbi.AllocationBase != p - i && i) return mbi.AllocationBase;
        if (*(unsigned short *)q == 0x5a4d) return q;
    }
    return NULL;
}

static int hook_iat(void *base, void *slot_addr, void *fn)
{
    DWORD old;
    if (!VirtualProtect(slot_addr, 4, PAGE_READWRITE, &old)) return 0;
    *(void **)slot_addr = fn;
    VirtualProtect(slot_addr, 4, old, &old);
    return 1;
}

static int patch_imports(void)
{
    char *base = (char *)GetModuleHandleA(NULL);
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS *nt;
    IMAGE_IMPORT_DESCRIPTOR *imp;
    int hits = 0;
    unsigned i, j;
    if (!base || dos->e_magic != IMAGE_DOS_SIGNATURE) return 0;
    nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return 0;
    imp = (IMAGE_IMPORT_DESCRIPTOR *)(base +
          nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for (; imp->Name; imp++) {
        const char *dll = base + imp->Name;
        IMAGE_THUNK_DATA *ilt = (IMAGE_THUNK_DATA *)(base + (imp->OriginalFirstThunk ?
                                                             imp->OriginalFirstThunk : imp->FirstThunk));
        IMAGE_THUNK_DATA *iat = (IMAGE_THUNK_DATA *)(base + imp->FirstThunk);
        if (!ci_eq(dll, "KERNEL32.DLL") && !ci_eq(dll, "KERNEL32") &&
            !ci_eq(dll, "kernel32.dll")) continue;
        for (j = 0; ilt[j].u1.AddressOfData; j++) {
            IMAGE_IMPORT_BY_NAME *ibn;
            if (ilt[j].u1.Ordinal & IMAGE_ORDINAL_FLAG) continue;
            ibn = (IMAGE_IMPORT_BY_NAME *)(base + ilt[j].u1.AddressOfData);
            for (i = 0; i < sizeof g_hooks / sizeof g_hooks[0]; i++) {
                if (ci_eq((const char *)ibn->Name, g_hooks[i].name)) {
                    hook_iat(base, &iat[j], g_hooks[i].fn);
                    hits++;
                    log_("HOOK %s", g_hooks[i].name);
                }
            }
        }
    }
    return hits;
}

static void resolve_real(void)
{
    /* this module is itself named kernel32.dll, so the original functions
     * must come from the private copy next to the vendor exe */
    HMODULE k = GetModuleHandleA("kernel32_real.dll");
    if (!k) k = LoadLibraryA("kernel32_real.dll");
    g_k32 = k;
    r_cfa = (pCFA)GetProcAddress(k, "CreateFileA");
    r_cfw = (pCFW)GetProcAddress(k, "CreateFileW");
    r_wf = (pWF)GetProcAddress(k, "WriteFile");
    r_rf = (pRF)GetProcAddress(k, "ReadFile");
    r_ch = (pCH)GetProcAddress(k, "CloseHandle");
    r_dic = (pDIC)GetProcAddress(k, "DeviceIoControl");
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID r)
{
    (void)inst; (void)r;
    if (reason == DLL_PROCESS_ATTACH) {
        int n;
        g_hooked = 1;
        resolve_real();
        n = patch_imports();
        log_("STAGE loaded, %d imports patched, k32=%p", n, g_k32);
    }
    return TRUE;
}

/* Export surface for an external patcher: name -> replacement, NULL terminated. */
__declspec(dllexport) const void *const *WINAPI HookTable(void)
{
    static void *tab[2 * (sizeof g_hooks / sizeof g_hooks[0]) + 1];
    unsigned i;
    for (i = 0; i < sizeof g_hooks / sizeof g_hooks[0]; i++) {
        tab[2 * i] = (void *)g_hooks[i].name;
        tab[2 * i + 1] = g_hooks[i].fn;
    }
    tab[2 * i] = NULL;
    return (const void *const *)tab;
}

/* rundll32 k32hook.dll,Stage -- lets the DLL be loaded into an already
 * running process by CreateRemoteThread(LoadLibrary) + this entry. */
__declspec(dllexport) void WINAPI Stage(void)
{
    resolve_real();
    log_("Stage re-run, %d patched", patch_imports());
}

/* Exported under the names the vendor imports; the def file maps them. */
HANDLE WINAPI x_CreateFileA(LPCSTR n, DWORD a, DWORD s, LPSECURITY_ATTRIBUTES sa, DWORD d, DWORD f, HANDLE t)
{ return h_CreateFileA(n, a, s, sa, d, f, t); }
HANDLE WINAPI x_CreateFileW(LPCWSTR n, DWORD a, DWORD s, LPSECURITY_ATTRIBUTES sa, DWORD d, DWORD f, HANDLE t)
{ return h_CreateFileW(n, a, s, sa, d, f, t); }
BOOL WINAPI x_WriteFile(HANDLE h, LPCVOID b, DWORD n, LPDWORD w, LPOVERLAPPED o)
{ return h_WriteFile(h, b, n, w, o); }
BOOL WINAPI x_ReadFile(HANDLE h, LPVOID b, DWORD n, LPDWORD rn, LPOVERLAPPED o)
{ return h_ReadFile(h, b, n, rn, o); }
BOOL WINAPI x_CloseHandle(HANDLE h) { return h_CloseHandle(h); }
BOOL WINAPI x_DeviceIoControl(HANDLE h, DWORD c, LPVOID in, DWORD il, LPVOID out, DWORD ol, LPDWORD ret, LPOVERLAPPED ov)
{ return h_DeviceIoControl(h, c, in, il, out, ol, ret, ov); }

/* rundll32 k32hook.dll,ClickAt -- reports where the pointer sits in the
 * active window, to tell whether an X click reached the vendor client area. */
__declspec(dllexport) void WINAPI ClickAt(void)
{
    HWND w = GetActiveWindow();
    POINT p;
    char buf[96];
    if (!w) { log_("no active window"); return; }
    GetCursorPos(&p);
    ScreenToClient(w, &p);
    snprintf(buf, sizeof buf, "click at %ld,%ld", (long)p.x, (long)p.y);
    log_(buf);
}

/* rundll32 k32hook.dll,Poke -- click the vendor window's client area at
 * (x,y) from POKE_X/POKE_Y, through PostMessage so a covered window works. */
static HWND find_vendor(void)
{
    HWND h = FindWindowA(NULL, "NAND Flash Reader v570");
    return h ? h : GetForegroundWindow();
}

__declspec(dllexport) void WINAPI Poke(void)
{
    char line[64] = {0};
    int x = 0, y = 0;
    FILE *f = fopen("Z:\\tmp\\poke.txt", "r");
    if (f) { if (fgets(line, sizeof line, f)) sscanf(line, "%d %d", &x, &y); fclose(f); }
    HWND w = find_vendor();
    LPARAM xy = MAKELPARAM(x, y);
    log_("POKE %p at %d,%d", (void *)w, x, y);
    PostMessageA(w, WM_LBUTTONDOWN, MK_LBUTTON, xy);
    PostMessageA(w, WM_LBUTTONUP, 0, xy);
}

/* Logs the client-area size and every child window rect of the vendor GUI. */
__declspec(dllexport) void WINAPI Dump(void)
{
    HWND w = find_vendor(), c;
    RECT r;
    char b[128];
    GetClientRect(w, &r);
    snprintf(b, sizeof b, "client %ldx%ld", (long)r.right, (long)r.bottom);
    log_(b);
    for (c = GetWindow(w, GW_CHILD); c; c = GetWindow(c, GW_HWNDNEXT)) {
        GetWindowRect(c, &r);
        MapWindowPoints(NULL, w, (POINT *)&r, 2);
        snprintf(b, sizeof b, "child %p %ld,%ld %ldx%ld", (void *)c,
                 (long)r.left, (long)r.top, (long)(r.right - r.left), (long)(r.bottom - r.top));
        log_(b);
    }
}

/* rundll32 k32hook.dll,PipeTest -- open the pipe and log rc. */
__declspec(dllexport) void WINAPI PipeTest(void)
{
    int ok = proxy();
    char b[96];
    snprintf(b, sizeof b, "PT ok=%d", ok); log_(b);
    if (ok && !all("P", 1, 1)) {
        char r;
        if (!all(&r, 1, 0)) { snprintf(b, sizeof b, "PT reply=%d", (int)(unsigned char)r); log_(b); }
    }
}
