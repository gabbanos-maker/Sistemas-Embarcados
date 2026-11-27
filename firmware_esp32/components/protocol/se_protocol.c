#include "se_protocol.h"
/* O firmware mantém este módulo e dados na RAM durante a captura. */
#ifdef ESP_PLATFORM
#include "esp_attr.h"
#define FAST IRAM_ATTR
#else
#define FAST
#endif
static void FAST publish(struct se_protocol *p, struct se_record *r,
                         se_record_fn output, void *context)
{
    if (r->phase && r->state <= SE_NO_RESPONSE) p->counts[r->state]++;
    output(context, r);
}
void se_init(struct se_protocol *p, uint64_t start_us, uint32_t offers, uint32_t warmup)
{
    *p=(struct se_protocol){.start_us=start_us, .offers=offers, .warmup=warmup};
    p->finish_us=start_us+((uint64_t)offers+warmup-1u)*SE_PERIOD_US+SE_GRACE_US;
}
uint64_t FAST se_next_alarm(const struct se_protocol *p)
{
    if (p->next < p->offers+p->warmup)
        return p->start_us+(uint64_t)p->next*SE_PERIOD_US;
    return p->finish_us;
}
bool FAST se_offer(struct se_protocol *p, uint64_t now, se_emit_fn emit,
                   se_record_fn output, void *context)
{
    if (p->finished || p->next>=p->offers+p->warmup) return false;
    uint64_t planned=se_next_alarm(p);
    if (now<planned) return false;
    uint32_t index=p->next++;
    struct se_record r={.planned_us=planned, .req_us=SE_NONE, .ack_us=SE_NONE,
        .delay_us=now-planned, .index=index<p->warmup?index:index-p->warmup,
        .phase=index>=p->warmup};
    if (r.delay_us>p->max_generator_delay) p->max_generator_delay=r.delay_us;
    /* Não fabricar emissões em rajada para ofertas cujo slot já passou.
       Isso é falha do gerador: estado inválido e ensaio sem validade. */
    if (now-planned>=SE_PERIOD_US) {
        p->generator_missed++;
        r.flags=SE_GENERATOR_MISSED;
        publish(p,&r,output,context);
    } else if (p->pending) {
        r.state=SE_BUSY;
        publish(p,&r,output,context);
    } else {
        p->req_level^=1u;
        p->pending=true;
        /* O callback lê o relógio imediatamente antes de alterar REQ. */
        r.req_us=emit(context,p->req_level);
        r.delay_us=r.req_us-planned;
        if (r.delay_us>p->max_generator_delay) p->max_generator_delay=r.delay_us;
        if (r.delay_us>=SE_PERIOD_US) { r.flags|=SE_GENERATOR_MISSED; p->generator_missed++; }
        p->request=r;
    }
    return true;
}
void FAST se_ack(struct se_protocol *p, uint64_t now, unsigned level,
                 se_record_fn output, void *context)
{
    if (p->finished || !p->pending || level!=p->req_level ||
        now<p->request.req_us || now>p->finish_us) {
        p->unexpected_ack++;
        return;
    }
    p->request.ack_us=now;
    p->request.state=now-p->request.req_us<=SE_DEADLINE_US?SE_OK:SE_LATE;
    publish(p,&p->request,output,context);
    p->pending=false;
}
void FAST se_finish(struct se_protocol *p, uint64_t now, se_record_fn output, void *context)
{
    if (p->finished || now<p->finish_us || p->next<p->offers+p->warmup) return;
    if (p->pending) {
        p->request.state=SE_NO_RESPONSE;
        publish(p,&p->request,output,context);
        p->pending=false;
    }
    p->finished=true;
}
