/* proxyexe.exe <target.exe> [args] -- start the vendor tool suspended, point
 * its KERNEL32 device imports at k32hook.dll (loaded here), then resume it.
 * The child never executes user code before the patch, so only its own
 * KERNEL32 imports are redirected and nothing else changes. */
#include <windows.h>
#include <ntstatus.h>
#include <stdio.h>
#include <string.h>

typedef LONG (WINAPI *pNtReadVirtualMemory)(HANDLE, void *, void *, ULONG, ULONG *);
typedef LONG (WINAPI *pNtWriteVirtualMemory)(HANDLE, void *, const void *, ULONG, ULONG *);
typedef LONG (WINAPI *pNtProtectVirtualMemory)(HANDLE, void **, ULONG *, ULONG, ULONG *);

static pNtReadVirtualMemory NtRead;
static pNtWriteVirtualMemory NtWrite;
static pNtProtectVirtualMemory NtProtect;

static int peek(HANDLE p, void *addr, void *out, unsigned n)
{
    SIZE_T got = 0;
    MEMORY_BASIC_INFORMATION mbi;
    return NtRead(p, addr, out, n, (ULONG *)&got) == 0 && got == n;
}

static int poke(HANDLE p, void *addr, const void *in, unsigned n)
{
    SIZE_T put = 0;
    void *page = addr;
    ULONG size = n, old = 0;
    if (NtProtect(p, &page, &size, PAGE_READWRITE, &old) != 0) return 0;
    if (NtWrite(p, addr, in, n, (ULONG *)&put) != 0 || put != n) return 0;
    NtProtect(p, &page, &size, old, &old);
    return 1;
}

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

int main(int argc, char **argv)
{
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    char cmd[4096] = {0};
    HANDLE hp;
    HMODULE shim, nt;
    void *mod_base;
    MEMORY_BASIC_INFORMATION mbi;
    unsigned char b[4096];
    unsigned long e_lfanew, opt, dir_rva;
    unsigned short magic = 0;
    unsigned short nsec;
    int i, n = 0, hits = 0;
    const char *names[16];
    void *fns[16];
    fprintf(stderr, "argc=%d argv1=%s\n", argc, argc > 1 ? argv[1] : "-");
    if (argc < 2) {
        fprintf(stderr, "usage: proxyexe target.exe [args]\n");
        return 2;
    }
    snprintf(cmd, sizeof cmd, "\"%s\"", argv[1]);
    for (i = 2; i < argc; i++) {
        strcat(cmd, " \"");
        strcat(cmd, argv[i]);
        strcat(cmd, "\"");
        if (strlen(cmd) > 4000) return 2;
    }
    shim = LoadLibraryA("C:\\nr\\k32hook.dll");
    if (!shim) { fprintf(stderr, "shim load failed %lu\n", GetLastError()); return 1; }
    {
        const void *const *t = (const void *const *)((void *(*WINAPI)(void))
            GetProcAddress(shim, "HookTable"))();
        while (t[2 * n] && n < 16) {
            names[n] = (const char *)t[2 * n];
            fns[n] = (void *)t[2 * n + 1];
            n++;
        }
    }
    fprintf(stderr, "shim module at %p\n", (void *)shim);
    nt = LoadLibraryA("ntdll.dll");
    NtRead = (pNtReadVirtualMemory)GetProcAddress(nt, "NtReadVirtualMemory");
    NtWrite = (pNtWriteVirtualMemory)GetProcAddress(nt, "NtWriteVirtualMemory");
    NtProtect = (pNtProtectVirtualMemory)GetProcAddress(nt, "NtProtectVirtualMemory");
    if (!NtRead || !NtWrite || !NtProtect) return 1;

    memset(&si, 0, sizeof si);
    si.cb = sizeof si;
    if (!CreateProcessA(NULL, cmd, NULL, NULL, FALSE,
                        DEBUG_ONLY_THIS_PROCESS | CREATE_SUSPENDED, NULL, NULL, &si, &pi)) {
        fprintf(stderr, "spawn failed %lu\n", GetLastError());
        return 1;
    }
    hp = pi.hProcess;
    Sleep(100);
    mod_base = NULL;
    /* find the main image in the child's memory by locating its MZ header at
     * the address the loader reports through the debug event */
    {
        DEBUG_EVENT de;
        int got = 0;
        for (i = 0; i < 50; i++) {
            if (!WaitForDebugEvent(&de, 50)) continue;
            if (de.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT)
                mod_base = de.u.CreateProcessInfo.lpBaseOfImage;
            if (de.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT) {
                IMAGE_DOS_HEADER *dos = NULL;
                (void)dos;
                got = 1;
            }
            if (de.dwDebugEventCode == EXCEPTION_DEBUG_EVENT) {
                ContinueDebugEvent(de.dwProcessId, de.dwThreadId, DBG_EXCEPTION_NOT_HANDLED);
                if (mod_base) break;
                continue;
            }
            ContinueDebugEvent(de.dwProcessId, de.dwThreadId, DBG_CONTINUE);
            if (got && mod_base) break;
        }
    }
    if (!mod_base) { fprintf(stderr, "no image base\n"); TerminateProcess(hp, 1); return 1; }
    fprintf(stderr, "dbg got base %p\n", mod_base);
    while (VirtualQueryEx(hp, mod_base, &mbi, sizeof mbi) && mbi.State == MEM_FREE)
        mod_base = (char *)mod_base + mbi.RegionSize;
    if (!peek(hp, mod_base, b, sizeof b)) { fprintf(stderr, "peek header %p\n", mod_base); return 1; }
    if (b[0] != 'M' || b[1] != 'Z') { fprintf(stderr, "no MZ\n"); return 1; }
    memcpy(&e_lfanew, b + 0x3c, 4);
    if (!peek(hp, (char *)mod_base + e_lfanew, b, 512)) { fprintf(stderr, "peek nt %lx\n", e_lfanew); return 1; }
    fprintf(stderr, "nt peek e_lfanew=%lx sig=%lx%lx%lx%lx magic=%x\n", e_lfanew, (unsigned long)b[0],(unsigned long)b[1],(unsigned long)b[2],(unsigned long)b[3], (int)*(unsigned short *)(b+24));
    memcpy(&nsec, b + 6, 2);
    memcpy(&opt, b + 20, 2);
    dir_rva = 0;
    {
        HMODULE self;
        HANDLE fp;
        unsigned char db[8];
        DWORD rd = 0;
        GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                           GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCSTR)(void *)&main, &self);
        char selfpath[260];
        if (!GetModuleFileNameA(self, selfpath, sizeof selfpath)) return 1;
        fp = CreateFileA(selfpath, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
        if (fp == INVALID_HANDLE_VALUE) return 1;
        SetFilePointer(fp, e_lfanew + 24 + (magic == 0x10b ? 96 : 112) + 16, NULL, FILE_BEGIN);
        if (!ReadFile(fp, db, 8, &rd, NULL) || rd != 8) return 1;
        memcpy(&dir_rva, db, 4);
        CloseHandle(fp);
    }
    if (!dir_rva || dir_rva >= 0x100000) { fprintf(stderr, "no imports at %lx magic %x hdr %02x%02x%02x%02x%02x%02x\n", dir_rva, (int)magic, b[0],b[1],b[2],b[3],b[4],b[5]); return 1; }
    {
        IMAGE_IMPORT_DESCRIPTOR d;
        void *p = (char *)mod_base + dir_rva;
        for (i = 0; i < 64; i++, p = (char *)p + sizeof d) {
            const char *dll;
            IMAGE_THUNK_DATA t;
            void *ilt, *iat;
            int j;
            char dname[64];
            if (!peek(hp, p, &d, sizeof d) || !d.Name) { fprintf(stderr, "desc %d stop name=%lx\n", i, (unsigned long)d.Name); break; }
            fprintf(stderr, "desc %d name=%lx oft=%lx iat=%lx\n", i, (unsigned long)d.Name, (unsigned long)d.OriginalFirstThunk, (unsigned long)d.FirstThunk);
            if (!peek(hp, (char *)mod_base + d.Name, dname, sizeof dname)) break;
            dname[63] = 0;
            dll = dname;
            if (!ci_eq(dll, "KERNEL32.dll") && !ci_eq(dll, "KERNEL32")) continue;
            ilt = (char *)mod_base + (d.OriginalFirstThunk ? d.OriginalFirstThunk : d.FirstThunk);
            iat = (char *)mod_base + d.FirstThunk;
            for (j = 0;; j++) {
                IMAGE_IMPORT_BY_NAME ibn;
                int k;
                if (!peek(hp, (char *)ilt + j * 4, &t, 4) || !t.u1.AddressOfData) break;
                if (t.u1.Ordinal & IMAGE_ORDINAL_FLAG) continue;
                if (!peek(hp, (char *)mod_base + t.u1.AddressOfData, &ibn, sizeof ibn)) break;
                for (k = 0; k < n; k++) {
                    if (ci_eq((char *)ibn.Name, names[k])) {
                        if (poke(hp, (char *)iat + j * 4, &fns[k], 4)) {
                            hits++;
                            fprintf(stderr, "hook %s\n", names[k]);
                        }
                    }
                }
            }
        }
    }
    fprintf(stderr, "imports hooked %d, base %p\n", hits, mod_base);
    DebugActiveProcessStop(pi.dwProcessId);
    ResumeThread(pi.hThread);
    WaitForSingleObject(hp, INFINITE);
    return 0;
}
