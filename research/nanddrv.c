/* nanddrv.sys -- stand-in for the vendor's NAND reader kernel driver (Wine).
 *
 * Wine's winedevice loads this as a kernel driver.  The DRIVER_OBJECT the
 * service manager hands DriverEntry is a stub: we create our own with a
 * minimal DRIVER_EXTENSION and return it.  NAND_Reader.exe then opens
 * \\.\NAND_Flash_Reader2, and the create/write/read IRPs dispatched here are
 * relayed to nanddev_proxy.py over AF_UNIX, which drives the physical reader
 * through libusb.
 *
 * PROBE stage: only creates the device object and logs every IRP so the
 * on-the-wire layout of Wine's IRP structs can be confirmed before payload
 * exchange is added.
 */
#include <winsock2.h>
#include <windows.h>

#define SOCK_PATH "Z:\\tmp\\nanddev.sock"
#define LOG_PATH "Z:\\tmp\\nanddev_kernel.log"

#define IOCTL_WINE_DRIVER_GET_OBJECT 0x80000e7c
#define IOCTL_WINE_DRIVER_GET_DEVICE_LIST 0x80000e78
#define METHOD_BUFFERED 0

typedef long NTSTATUS;
typedef NTSTATUS (__stdcall *fn_ntdll)(void);
typedef NTSTATUS (__stdcall *fn_nt)(void *, void *, void *, unsigned long, void *, unsigned long, void *, unsigned long, void *);
typedef NTSTATUS (__stdcall *fn_io)(void *, ULONG, const char *, ULONG, ULONG, void *, void **);
typedef long (__stdcall *fn_iocf)(HANDLE, void *, void *, void *, void *, ULONG, void *, ULONG, void *, ULONG);
typedef void (__stdcall *fn_done)(void *, char);

typedef struct {
    void *DriverObject;
    void *DeviceObject;
    void *FileObject;
    void *CompletionRoutine;
    void *Context;
    char Control;
} IO_STACK_LOCATION;

typedef struct {
    long Status;
    void *Pointer;
    long Status2, Information;
    unsigned char RequestorMode;
    unsigned char PendingReturned;
    char StackCount;
    char CurrentLocation;
    unsigned char Cancel;
    unsigned char CancelIrql;
    char ApcEnvironment;
    unsigned char AllocationFlags;
    void *UserIosb;
    void *UserEvent;
    void *Arg1, *Arg2, *Arg3, *Arg4;
    void *CancelRoutine;
    void *UserBuffer;
} IRP_FULL;

#define MAJOR(irp) (((unsigned char *)(irp))[25])
#define THIS_SP(irp) ((IO_STACK_LOCATION *)((char *)(irp) + 0x44 + 4 * MAJOR(irp)))

static void *g_drv, *g_dev;
static fn_io p_IoCreateDevice;
static fn_done p_IoCompleteRequest;

static SOCKET g_sock;

static void klog(const char *m, unsigned long a, unsigned long b)
{
    char buf[128];
    int n = 0;
    HANDLE h;
    DWORD wr;
    while (m[n]) { buf[n] = m[n]; n++; }
    unsigned vals[2] = { a, b };
    for (int v = 0; v < 2; v++) {
        buf[n++] = ' ';
        for (int sh = 28; sh >= 0; sh -= 4) {
            unsigned d = (vals[v] >> sh) & 0xf;
            buf[n++] = d < 10 ? '0' + d : 'a' + d - 10;
        }
    }
    buf[n++] = '\n';
    h = CreateFileA(LOG_PATH, 0x40000000, 3, NULL, 4, 0x80, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    SetFilePointer(h, 0, NULL, 2);
    WriteFile(h, buf, n, &wr, NULL);
    CloseHandle(h);
}

static int proxy(void)
{
    static WSADATA w;
    static const struct sockaddr_un_ { unsigned short f; char p[108]; } u = { 1, SOCK_PATH };
    if (g_sock) return 1;
    WSAStartup(0x0202, &w);
    g_sock = socket(1, 1, 0);
    if (g_sock == INVALID_SOCKET) { klog("socket", 0, 0); return 0; }
    if (connect(g_sock, (const struct sockaddr *)&u, sizeof u) != 0) {
        klog("connect", 0, 0);
        g_sock = 0;
        return 0;
    }
    klog("connected", 0, 0);
    return 1;
}

static int all(int n, char *b, int send_)
{
    while (n > 0) {
        int k = send_ ? send(g_sock, b, n, 0) : recv(g_sock, b, n, 0);
        if (k <= 0) return -1;
        b += k; n -= k;
    }
    return 0;
}

static int g_opened;

static NTSTATUS dispatch(void *dev, void *irp)
{
    IRP_FULL *r = (IRP_FULL *)irp;
    IO_STACK_LOCATION *sp = THIS_SP(irp);
    unsigned char major = MAJOR(irp);
    NTSTATUS st = 0;
    ULONG done = 0;
    char *p = (char *)r->UserBuffer;
    char hdr[5];
    ULONG len = 0;
    (void)dev;

    klog("IRP", major, 0);
    if (major == 0xb || major == 0x12) {           /* create / close */
        if (major == 0xb) {
            g_opened = 1;
            p = (char *)&g_opened;
            done = sizeof(g_opened);
        } else {
            g_opened = 0;
        }
        r->Status = 0;
        r->Information = done;
        r->Pointer = NULL;
        p_IoCompleteRequest(irp, 0);
        return 0;
    }
    if (major == 0 || major == 3) {                /* create is 0 here? read=3 write=4 */
        ;
    }
    if (major == 3 || major == 4) {                /* read / write */
        int wr = major == 4;
        len = *(ULONG *)((char *)sp + 16);
        klog(wr ? "W" : "R", len, 0);
        if (!len) {
            r->Status = 0; r->Information = 0;
            p_IoCompleteRequest(irp, 0);
            return 0;
        }
        if (!proxy()) { st = -1073741823; goto fin; }
        hdr[0] = wr ? 'W' : 'R';
        *(ULONG *)(hdr + 1) = len;
        if (all(5, hdr, 1)) { st = -1073741823; goto fin; }
        if (wr) {
            if (all((int)len, p, 1) || all(1, hdr, 0)) { st = -1073741823; goto fin; }
            done = len;
        } else {
            ULONG got = 0;
            if (all(4, (char *)&got, 0)) { st = -1073741823; goto fin; }
            if (got > len) got = len;
            if (got && all((int)got, p, 0)) { st = -1073741823; goto fin; }
            klog("R got", got, 0);
            done = got;
        }
        goto fin;
    }
fin:
    r->Status = st;
    r->Information = done;
    r->Pointer = NULL;
    p_IoCompleteRequest(irp, 0);
    return st;
}

static void *g_drv_ext[8];
static void *g_major[64];

NTSTATUS __stdcall DriverEntry(void *drv, void *path)
{
    void *dev = NULL;
    NTSTATUS st;
    static char devname[] = "\\Device\\NAND_Flash_Reader2";
    (void)path;
    klog("DriverEntry", (unsigned long)drv, 0);

    {
        HMODULE ntdll = GetModuleHandleA("ntdll.dll");
        HMODULE io = LoadLibraryA("ntoskrnl.exe");
        fn_iocf pNtDeviceIoControlFile = (fn_iocf)GetProcAddress(ntdll, "NtDeviceIoControlFile");
        p_IoCreateDevice = (fn_io)GetProcAddress(io, "IoCreateDevice");
        p_IoCompleteRequest = (fn_done)GetProcAddress(io, "IoCompleteRequest");
        klog("addrs", (unsigned long)p_IoCreateDevice, (unsigned long)p_IoCompleteRequest);
        if (pNtDeviceIoControlFile) {
            void *out = NULL;
            st = pNtDeviceIoControlFile((HANDLE)drv, NULL, NULL, NULL, &out,
                                        IOCTL_WINE_DRIVER_GET_OBJECT, NULL, 0, &dev, sizeof(dev));
            klog("GET_OBJECT", (unsigned long)st, (unsigned long)dev);
        }
    }
    if (!dev && p_IoCreateDevice) {
        st = p_IoCreateDevice(drv, 0, devname, 0x22, 0, 0, &dev);
        klog("IoCreateDevice", (unsigned long)st, (unsigned long)dev);
    }
    g_drv = drv;
    g_dev = dev;
    {
        /* patch the driver object's major function table and extension */
        char *d = (char *)drv;
        *(void **)(d + 0x04) = g_drv_ext;           /* DriverExtension */
        *(void **)(d + 0x2c) = g_major;             /* MajorFunction   */
        for (int i = 0; i < 64; i++) g_major[i] = (void *)dispatch;
        g_drv_ext[0] = drv;
    }
    return 0;
}
