#include "usage_data.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
    char json[2048], raw[4096], http[3072], body[2049];
    FILE *file;
    size_t length, header, split, rawlen;
    UsageSnapshot snapshot, unchanged;
    assert(argc == 2);
    file = fopen(argv[1], "rb");
    assert(file);
    length = fread(json, 1, sizeof(json) - 1, file);
    fclose(file);
    json[length] = 0;
    assert(Usage_Parse(json, length, &snapshot) == 0);
    assert(snapshot.now == 2000000000U);
    assert(snapshot.providers[0].status == USAGE_OK);
    assert(snapshot.providers[0].windows[0].used_tenths == 160);
    assert(snapshot.providers[0].windows[1].duration_mins == 10080);
    assert(Usage_WindowActive(&snapshot.providers[0].windows[0],2000000000U));
    assert(!Usage_WindowActive(&snapshot.providers[0].windows[0],2000004000U));
    UsageRxBuffer ring={0};char received[16];
    UsageRx_Push(&ring,'a');UsageRx_Push(&ring,'b');
    assert(UsageRx_Read(&ring,received,1)==1 && received[0]=='a');
    UsageRx_Push(&ring,'c'); /* A new UART burst must not be dropped after idle. */
    assert(UsageRx_Read(&ring,received,sizeof(received))==2);
    assert(received[0]=='b' && received[1]=='c');
    for(unsigned n=0;n<4096;n++)UsageRx_Push(&ring,'x');
    assert(UsageRx_Read(&ring,received,sizeof(received))<0);
    Usage_FormatCountdown(0, body, sizeof(body));
    assert(strcmp(body, "Refreshing") == 0);
    Usage_FormatCountdown(8100, body, sizeof(body));
    assert(strcmp(body, "Resets in 2h 15m") == 0);
    Usage_FormatCountdown(129600, body, sizeof(body));
    assert(strcmp(body, "Resets in 1d 12h") == 0);
    assert(!snapshot.providers[1].windows[0].valid);
    UsageProvider state={0};
    state.status=USAGE_OK;state.observed_at=2000000000U;
    state.windows[0]=(UsageWindow){1,50,300,2000004000U};
    state.windows[1]=(UsageWindow){1,160,10080,2000400000U};
    assert(Usage_GetFace(&state,2000000001U,1,0)==FACE_HAPPY);
    const char idle_json[]="{\"version\":1,\"now\":2000000000,\"codex\":{\"status\":\"waiting\",\"observed_at\":0,\"windows\":[null,null]},\"claude\":{\"status\":\"ok\",\"observed_at\":2000000000,\"windows\":[{\"used_pct\":0,\"duration_mins\":300,\"resets_at\":0},null]}}";
    UsageSnapshot idle_snapshot;
    assert(Usage_Parse(idle_json,strlen(idle_json),&idle_snapshot)==0);
    assert(idle_snapshot.providers[1].windows[0].valid);
    assert(idle_snapshot.providers[1].windows[0].resets_at==0);
    assert(Usage_GetFace(&idle_snapshot.providers[1],2000000001U,1,0)==FACE_HAPPY);
    state.status=USAGE_ERROR;
    assert(Usage_GetFace(&state,2000000001U,1,0)==FACE_XX);
    state.status=USAGE_AUTH;state.observed_at=0;
    assert(Usage_GetFace(&state,2000000001U,1,0)==FACE_XX);
    state.status=USAGE_OK;state.observed_at=2000000000U;
    assert(Usage_GetFace(&state,2000000301U,1,0)==FACE_SLEEPY);
    assert(Usage_GetFace(&state,2000000001U,0,0)==FACE_XX);
    assert(Usage_GetFace(&state,2000000001U,0,1)==FACE_SLEEPY);
    state.windows[1].used_tenths=850;
    assert(Usage_GetFace(&state,2000000001U,1,0)==FACE_CONCERNED);
    state.windows[1].resets_at=1999999999U;
    assert(Usage_GetFace(&state,2000000001U,1,0)==FACE_HAPPY);
    state.windows[0]=(UsageWindow){1,0,300,0};
    state.windows[1].used_tenths=160;state.windows[1].resets_at=2000400000U;
    assert(Usage_GetFace(&state,2000000001U,1,0)==FACE_HAPPY);
    unchanged = snapshot;
    char invalid[2048];
    strcpy(invalid,json);
    char *missing = strstr(invalid,"null");
    assert(missing);
    size_t offset=(size_t)(missing-invalid);
    memmove(invalid+offset+6,invalid+offset+4,strlen(invalid+offset+4)+1);
    memcpy(invalid+offset,"\"null\"",6);
    assert(Usage_Parse(invalid,strlen(invalid),&snapshot)!=0);
    assert(Usage_Parse(json, length - 4, &snapshot) != 0);
    assert(memcmp(&snapshot, &unchanged, sizeof(snapshot)) == 0);
    assert(Usage_Parse("{\"version\":1,\"version\":2}", 25, &snapshot) != 0);
    assert(Usage_Parse("{} trailing", 11, &snapshot) != 0);
    header = (size_t)snprintf(http, sizeof(http), "HTTP/1.0 200 OK\r\nContent-Length: %u\r\n\r\n%s", (unsigned)length, json);
    split = 33; /* Header and body deliberately arrive in different IPD frames. */
    rawlen = (size_t)snprintf(raw, sizeof(raw), "SEND OK\r\n+IPD,%u:%.*s\r\n+IPD,%u:%s\r\nCLOSED\r\n", (unsigned)split, (int)split, http, (unsigned)(header-split), http+split);
    assert(Usage_ExtractHttp(raw, rawlen, body, sizeof(body)) == 0);
    assert(strcmp(body, json) == 0);
    assert(Usage_ExtractHttp(raw, rawlen-30, body, sizeof(body)) != 0);
    assert(Usage_ExtractHttp(raw, rawlen, body, 10) != 0);
    http[9] = '4'; /* HTTP 400 must never be parsed as usage. */
    rawlen = (size_t)snprintf(raw, sizeof(raw), "+IPD,%u:%s", (unsigned)header, http);
    assert(Usage_ExtractHttp(raw, rawlen, body, sizeof(body)) != 0);
    puts("Native usage parser and fragmented HTTP tests passed");
    return 0;
}
