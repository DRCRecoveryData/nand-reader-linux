/* Shim builder: wraps hook.c as an in-memory "kernel32.dll" replacement.
 * Only needs to export what the vendor process imports from kernel32. */
#include <windows.h>
#include <stdio.h>

HANDLE WINAPI x_CreateFileA(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
HANDLE WINAPI x_CreateFileW(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
BOOL WINAPI x_WriteFile(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED);
BOOL WINAPI x_ReadFile(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
BOOL WINAPI x_CloseHandle(HANDLE);
BOOL WINAPI x_DeviceIoControl(HANDLE, DWORD, LPVOID, DWORD, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);

int main(void) { return 0; }
