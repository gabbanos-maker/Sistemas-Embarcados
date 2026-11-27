#ifndef SE_WIRE_H
#define SE_WIRE_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
#define SE_WIRE_SIZE 64
#define SE_WIRE_VERSION 2
#define SE_ROW_SIZE 48
#define SE_BEGIN 1
#define SE_ROW 2
#define SE_INTEGRITY 3
#define SE_END 4
struct se_packet {
    uint8_t kind, state, phase;
    uint32_t seq, run, index, flags, extra;
    uint64_t a,b,c,d;
};
size_t se_encode(uint8_t out[SE_WIRE_SIZE], const struct se_packet *p);
#ifdef __cplusplus
}
#endif
#endif
