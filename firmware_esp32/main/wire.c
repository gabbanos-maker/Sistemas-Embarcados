#include "wire.h"
static void u32(uint8_t *p,uint32_t v) { for(unsigned i=0;i<4;i++) p[i]=(uint8_t)(v>>(8*i)); }
static void u64(uint8_t *p,uint64_t v) { for(unsigned i=0;i<8;i++) p[i]=(uint8_t)(v>>(8*i)); }
/* Intervalos de até UINT32_MAX-1 us; UINT32_MAX representa ausência.
   Fora do intervalo, sinalizar falha em vez de truncar silenciosamente. */
static uint32_t interval(uint64_t value,uint32_t *flags)
{
    if (value==UINT64_MAX) return UINT32_MAX;
    if (value>=UINT32_MAX) { *flags|=UINT32_C(0x80000000); return UINT32_MAX; }
    return (uint32_t)value;
}
size_t se_encode(uint8_t out[SE_WIRE_SIZE], const struct se_packet *p)
{
    out[0]='S'; out[1]='E'; out[2]='1'; out[3]='0';
    out[4]=SE_WIRE_VERSION; out[5]=p->kind; out[6]=p->state; out[7]=p->phase;
    u32(out+8,p->seq); u32(out+12,p->run); u32(out+16,p->index);
    uint32_t flags=p->flags;
    size_t size=SE_WIRE_SIZE;
    if (p->kind==SE_ROW) {
        size=SE_ROW_SIZE;
        uint64_t req=UINT64_MAX, response=UINT64_MAX;
        if (p->b!=UINT64_MAX) {
            if (p->b>=p->a) req=p->b-p->a;
            else flags|=UINT32_C(0x80000000);
        }
        if (p->c!=UINT64_MAX) {
            if (p->b!=UINT64_MAX && p->c>=p->b) response=p->c-p->b;
            else flags|=UINT32_C(0x80000000);
        }
        u64(out+24,p->a);
        u32(out+32,interval(req,&flags));
        u32(out+36,interval(response,&flags));
        u32(out+40,interval(p->d,&flags));
    } else {
        u64(out+24,p->a); u64(out+32,p->b); u64(out+40,p->c); u64(out+48,p->d);
        u32(out+56,p->extra);
    }
    u32(out+20,flags);
    uint32_t crc=UINT32_MAX;
    for(size_t i=0;i<size-4;i++) {
        crc^=out[i];
        for(unsigned j=0;j<8;j++) crc=(crc>>1)^((crc&1u)?UINT32_C(0xedb88320):0u);
    }
    u32(out+size-4,crc^UINT32_MAX);
    return size;
}
