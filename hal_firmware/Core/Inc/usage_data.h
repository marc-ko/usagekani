#ifndef USAGE_DATA_H
#define USAGE_DATA_H
#include <stdint.h>
#include <stddef.h>

typedef enum { USAGE_WAITING, USAGE_OK, USAGE_STALE, USAGE_AUTH, USAGE_ERROR } UsageStatus;
typedef enum { FACE_HAPPY, FACE_BLINK, FACE_SLEEPY, FACE_CONCERNED, FACE_XX } UsageFace;
typedef struct {
    uint8_t valid;
    uint16_t used_tenths;
    uint32_t duration_mins;
    uint32_t resets_at;
} UsageWindow;
typedef struct {
    UsageStatus status;
    uint32_t observed_at;
    UsageWindow windows[2];
} UsageProvider;
typedef struct {
    uint32_t now;
    UsageProvider providers[2];
} UsageSnapshot;

/* Return zero only for a complete, validated result. Failure leaves out intact. */
int Usage_Parse(const char *json, size_t length, UsageSnapshot *out);
int Usage_ExtractHttp(const char *raw, size_t length, char *body, size_t capacity);
void Usage_FormatCountdown(uint32_t seconds, char *out, size_t capacity);
int Usage_WindowActive(const UsageWindow *window, uint32_t now);
UsageFace Usage_GetFace(const UsageProvider *provider,uint32_t now,int network_ok,int connecting);
typedef struct {
    volatile uint16_t head, tail;
    volatile uint8_t overflow;
    volatile char data[4096];
} UsageRxBuffer;
void UsageRx_Push(UsageRxBuffer *ring, char byte);
int UsageRx_Read(UsageRxBuffer *ring, char *out, size_t capacity);
#endif
