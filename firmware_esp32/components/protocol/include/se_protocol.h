#ifndef SE_PROTOCOL_H
#define SE_PROTOCOL_H
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define SE_PERIOD_US UINT64_C(5000)
#define SE_DEADLINE_US UINT64_C(3000)
#define SE_GRACE_US UINT64_C(1000000)
#define SE_NONE UINT64_MAX
#define SE_GENERATOR_MISSED 1u

enum se_state { SE_INVALID=0, SE_OK=1, SE_LATE=2, SE_BUSY=3, SE_NO_RESPONSE=4 };
struct se_record {
    uint64_t planned_us, req_us, ack_us, delay_us;
    uint32_t index, flags;
    uint8_t state, phase;
};
/* Chamadas serializadas pelo adaptador; callbacks não podem reentrar. */
typedef uint64_t (*se_emit_fn)(void *context, unsigned level);
typedef void (*se_record_fn)(void *context, const struct se_record *record);
struct se_protocol {
    uint64_t start_us, finish_us, counts[5], unexpected_ack, generator_missed;
    uint64_t max_generator_delay;
    uint32_t offers, warmup, next;
    unsigned req_level;
    bool pending, finished;
    struct se_record request;
};
void se_init(struct se_protocol *p, uint64_t start_us, uint32_t offers, uint32_t warmup);
bool se_offer(struct se_protocol *p, uint64_t now, se_emit_fn emit,
              se_record_fn output, void *context);
void se_ack(struct se_protocol *p, uint64_t now, unsigned level,
            se_record_fn output, void *context);
void se_finish(struct se_protocol *p, uint64_t now, se_record_fn output, void *context);
uint64_t se_next_alarm(const struct se_protocol *p);
#ifdef __cplusplus
}
#endif
#endif
