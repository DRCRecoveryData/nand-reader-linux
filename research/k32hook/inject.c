/* inject.exe <pid> <dll> -- LoadLibraryA(dll) in the target process. */
#include <windows.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    HMODULE k = GetModuleHandleA("kernel32.dll");
    void *fn = (void *)GetProcAddress(k, "LoadLibraryA");
    DWORD pid = (DWORD)atol(argv[1]);
    fprintf(stderr, "pid arg=%lu\n", (unsigned long)pid);
    HANDLE pr = OpenProcess(PROCESS_ALL_ACCESS, 0, pid);
    if (!pr) pr = OpenProcess(PROCESS_CREATE_THREAD|PROCESS_VM_OPERATION|PROCESS_VM_WRITE|PROCESS_VM_READ|PROCESS_QUERY_INFORMATION, 0, pid);
    char path[512] = {0};
    void *mem;
    HANDLE th;
    if (!pr) { fprintf(stderr, "open %lu\n", GetLastError()); return 1; }
    GetFullPathNameA(argv[2], sizeof path, path, NULL);
    mem = VirtualAllocEx(pr, NULL, strlen(path) + 1, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    WriteProcessMemory(pr, mem, path, strlen(path) + 1, NULL);
    th = CreateRemoteThread(pr, NULL, 0, (LPTHREAD_START_ROUTINE)fn, mem, 0, NULL);
    if (!th) { fprintf(stderr, "thread %lu\n", GetLastError()); return 1; }
    WaitForSingleObject(th, 15000);
    printf("injected %s into %lu\n", path, pid);
    return 0;
}
