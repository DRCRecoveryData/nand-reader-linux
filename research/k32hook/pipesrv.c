/* pipesrv.exe -- Wine-side bridge.
 *
 * Vendor shim (k32hook.dll) connects to \\.\pipe\nanddev.  For each accepted
 * pipe connection we open a TCP connection to 127.0.0.1:PORT and shovel bytes
 * in both directions until either side closes.  The Linux side
 * (pipe_bridge.py) accepts on PORT and relays to the AF_UNIX nanddev proxy.
 *
 * This exists because Wine 9.0's ws2_32 rejects AF_UNIX (10047) but its
 * WinSock + named pipes are fine.
 */
#include <winsock2.h>
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

#define PIPE_NAME "\\\\.\\pipe\\nanddev"

static volatile LONG g_clients;


/* Relay one pipe instance <-> one TCP socket using blocking threads. */
typedef struct { HANDLE pipe; SOCKET sock; } pair_t;

static DWORD WINAPI pump_pipe_to_sock(LPVOID a)
{
    pair_t *q = (pair_t *)a;
    char buf[65536];
    DWORD n;
    while (ReadFile(q->pipe, buf, sizeof buf, &n, NULL) && n)
        send(q->sock, buf, n, 0);
    shutdown(q->sock, SD_SEND);
    return 0;
}

static DWORD WINAPI pump_sock_to_pipe(LPVOID a)
{
    pair_t *q = (pair_t *)a;
    char buf[65536];
    int n;
    while ((n = recv(q->sock, buf, sizeof buf, 0)) > 0)
        WriteFile(q->pipe, buf, n, NULL, NULL);
    return 0;
}

int main(int argc, char **argv)
{
    int port = argc > 1 ? atoi(argv[1]) : 47199;
    WSADATA w;
    if (WSAStartup(0x0202, &w)) { fprintf(stderr, "wsa fail\n"); return 1; }

    for (;;) {
        OVERLAPPED ov;
        BOOL ok;
        HANDLE pipe = CreateNamedPipeA(PIPE_NAME,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, NULL);
        pair_t q;
        SOCKET sk;
        struct sockaddr_in sa;
        HANDLE t1, t2;
        DWORD id;

        if (pipe == INVALID_HANDLE_VALUE) { fprintf(stderr, "cnp %lu\n", GetLastError()); return 1; }
        fprintf(stderr, "pipe up, waiting for client\n");
        if (!ConnectNamedPipe(pipe, NULL) && GetLastError() != ERROR_PIPE_CONNECTED) {
            fprintf(stderr, "connect %lu\n", GetLastError());
            CloseHandle(pipe);
            continue;
        }
        fprintf(stderr, "client connected\n");

        sk = socket(AF_INET, SOCK_STREAM, 0);
        sa.sin_family = AF_INET;
        sa.sin_port = htons((u_short)port);
        sa.sin_addr.s_addr = htonl(0x7f000001);
        if (sk == INVALID_SOCKET || connect(sk, (struct sockaddr *)&sa, sizeof sa)) {
            fprintf(stderr, "tcp connect %d\n", WSAGetLastError());
            if (sk != INVALID_SOCKET) closesocket(sk);
            DisconnectNamedPipe(pipe);
            CloseHandle(pipe);
            continue;
        }
        fprintf(stderr, "tcp connected\n");

        q.pipe = pipe; q.sock = sk;
        t1 = CreateThread(NULL, 0, pump_pipe_to_sock, &q, 0, &id);
        t2 = CreateThread(NULL, 0, pump_sock_to_pipe, &q, 0, &id);
        WaitForMultipleObjects(2, (HANDLE[]){t1, t2}, TRUE, INFINITE);
        CloseHandle(t1); CloseHandle(t2);
        closesocket(sk);
        DisconnectNamedPipe(pipe);
        CloseHandle(pipe);
        fprintf(stderr, "session closed\n");
    }
    return 0;
}
