/* fwd.c -- kernel32.dll replacement for NAND_Reader.exe under Wine.
 *
 * Every export the exe imports is a wrapper here; slot i of k_real_p holds the
 * kernel32_real.dll address of the same function.  The four device entry points
 * are in nand_k32.c and speak to nanddev_proxy.py.  Generated list.
 */
#include <windows.h>

void *k_real_p[107];

static const char *const k_real_names[107] = {
#include "kernel32_names.inc"
};

__attribute__((constructor)) static void k32_init(void)
{
    int i;
    HMODULE m = LoadLibraryA("kernel32_real.dll");
    if (!m) m = LoadLibraryA("C:\\windows\\system32\\kernel32_real.dll");
    if (!m) return;
    for (i = 0; i < 107; i++)
        k_real_p[i] = (void *)GetProcAddress(m, k_real_names[i]);
}

__declspec(dllexport) __attribute__((naked)) void w0(void)
{
    __asm__("jmp *_k_real_p+0*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w1(void)
{
    __asm__("jmp *_k_real_p+1*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w2(void)
{
    __asm__("jmp *_k_real_p+2*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w3(void)
{
    __asm__("jmp *_k_real_p+3*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w4(void)
{
    __asm__("jmp *_k_real_p+4*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w5(void)
{
    __asm__("jmp *_k_real_p+5*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w6(void)
{
    __asm__("jmp *_k_real_p+6*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w7(void)
{
    __asm__("jmp *_k_real_p+7*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w8(void)
{
    __asm__("jmp *_k_real_p+8*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w9(void)
{
    __asm__("jmp *_k_real_p+9*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w10(void)
{
    __asm__("jmp *_k_real_p+10*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w11(void)
{
    __asm__("jmp *_k_real_p+11*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w12(void)
{
    __asm__("jmp *_k_real_p+12*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w13(void)
{
    __asm__("jmp *_k_real_p+13*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w14(void)
{
    __asm__("jmp *_k_real_p+14*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w15(void)
{
    __asm__("jmp *_k_real_p+15*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w16(void)
{
    __asm__("jmp *_k_real_p+16*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w17(void)
{
    __asm__("jmp *_k_real_p+17*4");
    __asm__(".byte 0x90");
}
void n_WriteFile(void);
__declspec(dllexport) void *ex_WriteFile = (void *)&n_WriteFile;
__declspec(dllexport) __attribute__((naked)) void w19(void)
{
    __asm__("jmp *_k_real_p+19*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w20(void)
{
    __asm__("jmp *_k_real_p+20*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w21(void)
{
    __asm__("jmp *_k_real_p+21*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w22(void)
{
    __asm__("jmp *_k_real_p+22*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w23(void)
{
    __asm__("jmp *_k_real_p+23*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w24(void)
{
    __asm__("jmp *_k_real_p+24*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w25(void)
{
    __asm__("jmp *_k_real_p+25*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w26(void)
{
    __asm__("jmp *_k_real_p+26*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w27(void)
{
    __asm__("jmp *_k_real_p+27*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w28(void)
{
    __asm__("jmp *_k_real_p+28*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w29(void)
{
    __asm__("jmp *_k_real_p+29*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w30(void)
{
    __asm__("jmp *_k_real_p+30*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w31(void)
{
    __asm__("jmp *_k_real_p+31*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w32(void)
{
    __asm__("jmp *_k_real_p+32*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w33(void)
{
    __asm__("jmp *_k_real_p+33*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w34(void)
{
    __asm__("jmp *_k_real_p+34*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w35(void)
{
    __asm__("jmp *_k_real_p+35*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w36(void)
{
    __asm__("jmp *_k_real_p+36*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w37(void)
{
    __asm__("jmp *_k_real_p+37*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w38(void)
{
    __asm__("jmp *_k_real_p+38*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w39(void)
{
    __asm__("jmp *_k_real_p+39*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w40(void)
{
    __asm__("jmp *_k_real_p+40*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w41(void)
{
    __asm__("jmp *_k_real_p+41*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w42(void)
{
    __asm__("jmp *_k_real_p+42*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w43(void)
{
    __asm__("jmp *_k_real_p+43*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w44(void)
{
    __asm__("jmp *_k_real_p+44*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w45(void)
{
    __asm__("jmp *_k_real_p+45*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w46(void)
{
    __asm__("jmp *_k_real_p+46*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w47(void)
{
    __asm__("jmp *_k_real_p+47*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w48(void)
{
    __asm__("jmp *_k_real_p+48*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w49(void)
{
    __asm__("jmp *_k_real_p+49*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w50(void)
{
    __asm__("jmp *_k_real_p+50*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w51(void)
{
    __asm__("jmp *_k_real_p+51*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w52(void)
{
    __asm__("jmp *_k_real_p+52*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w53(void)
{
    __asm__("jmp *_k_real_p+53*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w54(void)
{
    __asm__("jmp *_k_real_p+54*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w55(void)
{
    __asm__("jmp *_k_real_p+55*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w56(void)
{
    __asm__("jmp *_k_real_p+56*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w57(void)
{
    __asm__("jmp *_k_real_p+57*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w58(void)
{
    __asm__("jmp *_k_real_p+58*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w59(void)
{
    __asm__("jmp *_k_real_p+59*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w60(void)
{
    __asm__("jmp *_k_real_p+60*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w61(void)
{
    __asm__("jmp *_k_real_p+61*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w62(void)
{
    __asm__("jmp *_k_real_p+62*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w63(void)
{
    __asm__("jmp *_k_real_p+63*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w64(void)
{
    __asm__("jmp *_k_real_p+64*4");
    __asm__(".byte 0x90");
}
void n_ReadFile(void);
__declspec(dllexport) void *ex_ReadFile = (void *)&n_ReadFile;
void n_CloseHandle(void);
__declspec(dllexport) void *ex_CloseHandle = (void *)&n_CloseHandle;
__declspec(dllexport) __attribute__((naked)) void w67(void)
{
    __asm__("jmp *_k_real_p+67*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w68(void)
{
    __asm__("jmp *_k_real_p+68*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w69(void)
{
    __asm__("jmp *_k_real_p+69*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w70(void)
{
    __asm__("jmp *_k_real_p+70*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w71(void)
{
    __asm__("jmp *_k_real_p+71*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w72(void)
{
    __asm__("jmp *_k_real_p+72*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w73(void)
{
    __asm__("jmp *_k_real_p+73*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w74(void)
{
    __asm__("jmp *_k_real_p+74*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w75(void)
{
    __asm__("jmp *_k_real_p+75*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w76(void)
{
    __asm__("jmp *_k_real_p+76*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w77(void)
{
    __asm__("jmp *_k_real_p+77*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w78(void)
{
    __asm__("jmp *_k_real_p+78*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w79(void)
{
    __asm__("jmp *_k_real_p+79*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w80(void)
{
    __asm__("jmp *_k_real_p+80*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w81(void)
{
    __asm__("jmp *_k_real_p+81*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w82(void)
{
    __asm__("jmp *_k_real_p+82*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w83(void)
{
    __asm__("jmp *_k_real_p+83*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w84(void)
{
    __asm__("jmp *_k_real_p+84*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w85(void)
{
    __asm__("jmp *_k_real_p+85*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w86(void)
{
    __asm__("jmp *_k_real_p+86*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w87(void)
{
    __asm__("jmp *_k_real_p+87*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w88(void)
{
    __asm__("jmp *_k_real_p+88*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w89(void)
{
    __asm__("jmp *_k_real_p+89*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w90(void)
{
    __asm__("jmp *_k_real_p+90*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w91(void)
{
    __asm__("jmp *_k_real_p+91*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w92(void)
{
    __asm__("jmp *_k_real_p+92*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w93(void)
{
    __asm__("jmp *_k_real_p+93*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w94(void)
{
    __asm__("jmp *_k_real_p+94*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w95(void)
{
    __asm__("jmp *_k_real_p+95*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w96(void)
{
    __asm__("jmp *_k_real_p+96*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w97(void)
{
    __asm__("jmp *_k_real_p+97*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w98(void)
{
    __asm__("jmp *_k_real_p+98*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w99(void)
{
    __asm__("jmp *_k_real_p+99*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w100(void)
{
    __asm__("jmp *_k_real_p+100*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w101(void)
{
    __asm__("jmp *_k_real_p+101*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w102(void)
{
    __asm__("jmp *_k_real_p+102*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w103(void)
{
    __asm__("jmp *_k_real_p+103*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w104(void)
{
    __asm__("jmp *_k_real_p+104*4");
    __asm__(".byte 0x90");
}
__declspec(dllexport) __attribute__((naked)) void w105(void)
{
    __asm__("jmp *_k_real_p+105*4");
    __asm__(".byte 0x90");
}
void n_CreateFileA(void);
__declspec(dllexport) void *ex_CreateFileA = (void *)&n_CreateFileA;

#pragma comment(linker, "/export:RtlUnwind=w0")
#pragma comment(linker, "/export:GetSystemTimeAsFileTime=w1")
#pragma comment(linker, "/export:InitializeCriticalSectionAndSpinCount=w2")
#pragma comment(linker, "/export:GetStringTypeA=w3")
#pragma comment(linker, "/export:GetStringTypeW=w4")
#pragma comment(linker, "/export:LCMapStringA=w5")
#pragma comment(linker, "/export:RaiseException=w6")
#pragma comment(linker, "/export:SetStdHandle=w7")
#pragma comment(linker, "/export:TerminateProcess=w8")
#pragma comment(linker, "/export:GetCurrentProcess=w9")
#pragma comment(linker, "/export:UnhandledExceptionFilter=w10")
#pragma comment(linker, "/export:GetCommandLineW=w11")
#pragma comment(linker, "/export:GetCurrentThreadId=w12")
#pragma comment(linker, "/export:CreateThread=w13")
#pragma comment(linker, "/export:GetCurrentDirectoryW=w14")
#pragma comment(linker, "/export:Sleep=w15")
#pragma comment(linker, "/export:RemoveDirectoryW=w16")
#pragma comment(linker, "/export:FindFirstFileW=w17")
#pragma comment(linker, "/export:WriteFile=ex_WriteFile")
#pragma comment(linker, "/export:SetUnhandledExceptionFilter=w19")
#pragma comment(linker, "/export:IsDebuggerPresent=w20")
#pragma comment(linker, "/export:GetCommandLineA=w21")
#pragma comment(linker, "/export:GetStartupInfoA=w22")
#pragma comment(linker, "/export:HeapAlloc=w23")
#pragma comment(linker, "/export:HeapFree=w24")
#pragma comment(linker, "/export:GetModuleFileNameA=w25")
#pragma comment(linker, "/export:GetModuleHandleW=w26")
#pragma comment(linker, "/export:TlsGetValue=w27")
#pragma comment(linker, "/export:TlsAlloc=w28")
#pragma comment(linker, "/export:TlsSetValue=w29")
#pragma comment(linker, "/export:TlsFree=w30")
#pragma comment(linker, "/export:InterlockedIncrement=w31")
#pragma comment(linker, "/export:SetLastError=w32")
#pragma comment(linker, "/export:InterlockedDecrement=w33")
#pragma comment(linker, "/export:HeapSize=w34")
#pragma comment(linker, "/export:ExitProcess=w35")
#pragma comment(linker, "/export:FreeEnvironmentStringsA=w36")
#pragma comment(linker, "/export:GetEnvironmentStrings=w37")
#pragma comment(linker, "/export:FreeEnvironmentStringsW=w38")
#pragma comment(linker, "/export:GetEnvironmentStringsW=w39")
#pragma comment(linker, "/export:SetHandleCount=w40")
#pragma comment(linker, "/export:GetFileType=w41")
#pragma comment(linker, "/export:HeapCreate=w42")
#pragma comment(linker, "/export:VirtualFree=w43")
#pragma comment(linker, "/export:QueryPerformanceCounter=w44")
#pragma comment(linker, "/export:GetCurrentProcessId=w45")
#pragma comment(linker, "/export:VirtualAlloc=w46")
#pragma comment(linker, "/export:HeapReAlloc=w47")
#pragma comment(linker, "/export:LoadLibraryA=w48")
#pragma comment(linker, "/export:GetCPInfo=w49")
#pragma comment(linker, "/export:GetACP=w50")
#pragma comment(linker, "/export:GetOEMCP=w51")
#pragma comment(linker, "/export:IsValidCodePage=w52")
#pragma comment(linker, "/export:GetLocaleInfoA=w53")
#pragma comment(linker, "/export:SetFilePointer=w54")
#pragma comment(linker, "/export:GetConsoleCP=w55")
#pragma comment(linker, "/export:GetConsoleMode=w56")
#pragma comment(linker, "/export:LCMapStringW=w57")
#pragma comment(linker, "/export:WriteConsoleA=w58")
#pragma comment(linker, "/export:GetConsoleOutputCP=w59")
#pragma comment(linker, "/export:FlushFileBuffers=w60")
#pragma comment(linker, "/export:GlobalLock=w61")
#pragma comment(linker, "/export:FileTimeToSystemTime=w62")
#pragma comment(linker, "/export:SystemTimeToFileTime=w63")
#pragma comment(linker, "/export:GetTickCount=w64")
#pragma comment(linker, "/export:ReadFile=ex_ReadFile")
#pragma comment(linker, "/export:CloseHandle=ex_CloseHandle")
#pragma comment(linker, "/export:FindNextFileW=w67")
#pragma comment(linker, "/export:GetLastError=w68")
#pragma comment(linker, "/export:CreateMutexA=w69")
#pragma comment(linker, "/export:InitializeCriticalSection=w70")
#pragma comment(linker, "/export:DeleteCriticalSection=w71")
#pragma comment(linker, "/export:EnterCriticalSection=w72")
#pragma comment(linker, "/export:LeaveCriticalSection=w73")
#pragma comment(linker, "/export:MulDiv=w74")
#pragma comment(linker, "/export:GlobalUnlock=w75")
#pragma comment(linker, "/export:GlobalAlloc=w76")
#pragma comment(linker, "/export:GetProcAddress=w77")
#pragma comment(linker, "/export:GetModuleHandleA=w78")
#pragma comment(linker, "/export:FreeLibrary=w79")
#pragma comment(linker, "/export:GetStdHandle=w80")
#pragma comment(linker, "/export:AttachConsole=w81")
#pragma comment(linker, "/export:lstrcmpW=w82")
#pragma comment(linker, "/export:FindClose=w83")
#pragma comment(linker, "/export:WideCharToMultiByte=w84")
#pragma comment(linker, "/export:GetLocalTime=w85")
#pragma comment(linker, "/export:MultiByteToWideChar=w86")
#pragma comment(linker, "/export:GetSystemTime=w87")
#pragma comment(linker, "/export:SetFilePointerEx=w88")
#pragma comment(linker, "/export:LocalFree=w89")
#pragma comment(linker, "/export:FormatMessageW=w90")
#pragma comment(linker, "/export:GetFileSizeEx=w91")
#pragma comment(linker, "/export:SetEndOfFile=w92")
#pragma comment(linker, "/export:CreateFileW=w93")
#pragma comment(linker, "/export:DeviceIoControl=w94")
#pragma comment(linker, "/export:CreateDirectoryW=w95")
#pragma comment(linker, "/export:DeleteFileW=w96")
#pragma comment(linker, "/export:GetModuleFileNameW=w97")
#pragma comment(linker, "/export:GetExitCodeProcess=w98")
#pragma comment(linker, "/export:WaitForSingleObject=w99")
#pragma comment(linker, "/export:CreateProcessW=w100")
#pragma comment(linker, "/export:LoadLibraryW=w101")
#pragma comment(linker, "/export:WriteConsoleW=w102")
#pragma comment(linker, "/export:OutputDebugStringW=w103")
#pragma comment(linker, "/export:GetFileTime=w104")
#pragma comment(linker, "/export:GetFileAttributesExW=w105")
#pragma comment(linker, "/export:CreateFileA=ex_CreateFileA")
#pragma comment(linker, "/export:k_real_export")
