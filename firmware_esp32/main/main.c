#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sdkconfig.h"
#include "driver/gpio.h"
#include "driver/gptimer.h"
#include "driver/uart.h"
#include "esp_attr.h"
#include "esp_intr_alloc.h"
#include "esp_cpu.h"
#include "soc/soc_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "se_protocol.h"
#include "wire.h"

#if !CONFIG_IDF_TARGET_ESP32
#error "Esta pasta de firmware é exclusiva para ESP32-WROOM."
#endif

#if CONFIG_FREERTOS_UNICORE || SOC_CPU_CORES_NUM < 2
#error "Este firmware requer um alvo com dois núcleos habilitados."
#endif
#define REQ CONFIG_SE_REQ_GPIO
#define ACK CONFIG_SE_ACK_GPIO
#define QUEUE_SIZE 1024
#define UART_TX_GPIO CONFIG_SE_UART_TX_GPIO
#define UART_RX_GPIO CONFIG_SE_UART_RX_GPIO
static DRAM_ATTR struct se_protocol protocol;
static DRAM_ATTR gptimer_handle_t clock_timer;
static DRAM_ATTR portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
static DRAM_ATTR QueueHandle_t records;
static DRAM_ATTR uint32_t overflow, io_errors;
static DRAM_ATTR bool running;
static uint32_t run_tag, sequence;
static uint32_t offer_count, warmup_count;

static uint64_t IRAM_ATTR now_us(void)
{
    uint64_t t=0;
    if (gptimer_get_raw_count(clock_timer,&t)!=ESP_OK) io_errors++;
    return t;
}
static uint64_t IRAM_ATTR emit(void *unused,unsigned level)
{
    (void)unused;
    uint64_t t=now_us(); /* Última operação antes de alterar REQ. */
    if (gpio_set_level(REQ,(int)level)!=ESP_OK) io_errors++;
    return t;
}
static void IRAM_ATTR output(void *unused,const struct se_record *r)
{
    (void)unused;
    /* A serialização/CRC e a UART ficam na tarefa do outro núcleo. */
    if (xQueueSendFromISR(records,r,NULL)!=pdTRUE) overflow++;
}
static void IRAM_ATTR ack_isr(void *unused)
{
    (void)unused;
    uint64_t t=now_us(); /* Timestamp no início da ISR, mesmo relógio de REQ. */
    unsigned level=(unsigned)gpio_get_level(ACK);
    portENTER_CRITICAL_ISR(&mux);
    if (running) se_ack(&protocol,t,level,output,NULL);
    portEXIT_CRITICAL_ISR(&mux);
}
static bool IRAM_ATTR alarm_isr(gptimer_handle_t timer,
    const gptimer_alarm_event_data_t *event,void *unused)
{
    (void)event; (void)unused;
    portENTER_CRITICAL_ISR(&mux);
    uint64_t t=now_us();
    /* Recuperação limitada de slots perdidos; não emitir rajadas de REQ. */
    for(unsigned i=0;i<8;i++) {
        if (!se_offer(&protocol,t,emit,output,NULL)) break;
        t=now_us();
    }
    se_finish(&protocol,t,output,NULL);
    if (protocol.finished) {
        running=false;
    } else {
        uint64_t next=se_next_alarm(&protocol);
        if (next<=t) next=t+1; /* O calendário original do protocolo não muda. */
        gptimer_alarm_config_t alarm={.alarm_count=next};
        if (gptimer_set_alarm_action(timer,&alarm)!=ESP_OK) {
            io_errors++; running=false; protocol.finished=true;
        }
    }
    portEXIT_CRITICAL_ISR(&mux);
    return false;
}
static void send_packet(struct se_packet *p)
{
    uint8_t data[SE_WIRE_SIZE];
    p->seq=sequence++; p->run=run_tag;
    size_t size=se_encode(data,p);
    if (uart_write_bytes(UART_NUM_0,data,size)!=(int)size) abort();
}
static void writer(void *unused)
{
    (void)unused;
    for (;;) {
        struct se_record r;
        if (xQueueReceive(records,&r,pdMS_TO_TICKS(10))==pdTRUE) {
            struct se_packet p={.kind=SE_ROW,.state=r.state,.phase=r.phase,
                .index=r.index,.flags=r.flags,.a=r.planned_us,.b=r.req_us,
                .c=r.ack_us,.d=r.delay_us};
            send_packet(&p);
        } else {
            portENTER_CRITICAL(&mux);
            bool finished=protocol.finished;
            portEXIT_CRITICAL(&mux);
            if (!finished || uxQueueMessagesWaiting(records)!=0) continue;
            /* O produtor terminou antes do receive: a fila está vazia. */
            uint32_t flags=(overflow?1u:0u)|(protocol.unexpected_ack?2u:0u)|
                (protocol.generator_missed?4u:0u)|(io_errors?8u:0u);
            struct se_packet stats={.kind=SE_INTEGRITY,.flags=flags,
                .a=overflow,.b=protocol.unexpected_ack,.c=protocol.generator_missed,
                .d=protocol.max_generator_delay,.extra=io_errors};
            send_packet(&stats);
            struct se_packet end={.kind=SE_END,.index=offer_count,.flags=flags,
                .a=protocol.counts[SE_OK],.b=protocol.counts[SE_LATE],
                .c=protocol.counts[SE_BUSY],.d=protocol.counts[SE_NO_RESPONSE]};
            send_packet(&end);
            uart_wait_tx_done(UART_NUM_0,pdMS_TO_TICKS(3000));
            vTaskDelete(NULL);
        }
    }
}
static void measurement(void *unused)
{
    (void)unused;
    /* Drivers/ISRs alocados no núcleo 1. A UART foi instalada no núcleo 0. */
    gpio_config_t req={.pin_bit_mask=UINT64_C(1)<<REQ,.mode=GPIO_MODE_OUTPUT};
    ESP_ERROR_CHECK(gpio_config(&req));
    ESP_ERROR_CHECK(gpio_set_level(REQ,0));
    gpio_config_t ack={.pin_bit_mask=UINT64_C(1)<<ACK,.mode=GPIO_MODE_INPUT,
        .pull_down_en=GPIO_PULLDOWN_ENABLE,.intr_type=GPIO_INTR_ANYEDGE};
    ESP_ERROR_CHECK(gpio_config(&ack));
    gptimer_config_t config={.clk_src=GPTIMER_CLK_SRC_DEFAULT,
        .direction=GPTIMER_COUNT_UP,.resolution_hz=1000000,.intr_priority=1};
    ESP_ERROR_CHECK(gptimer_new_timer(&config,&clock_timer));
    uint32_t resolution=0;
    ESP_ERROR_CHECK(gptimer_get_resolution(clock_timer,&resolution));
    ESP_ERROR_CHECK(resolution==1000000?ESP_OK:ESP_FAIL);
    ESP_ERROR_CHECK(gpio_install_isr_service(ESP_INTR_FLAG_IRAM|ESP_INTR_FLAG_LEVEL1));
    ESP_ERROR_CHECK(gpio_isr_handler_add(ACK,ack_isr,NULL));
    ESP_ERROR_CHECK(gpio_get_level(ACK)==0?ESP_OK:ESP_ERR_INVALID_STATE);
    gptimer_event_callbacks_t callbacks={.on_alarm=alarm_isr};
    ESP_ERROR_CHECK(gptimer_register_event_callbacks(clock_timer,&callbacks,NULL));
    ESP_ERROR_CHECK(gptimer_enable(clock_timer));
    portENTER_CRITICAL(&mux);
    se_init(&protocol,1000000,offer_count,warmup_count);
    portEXIT_CRITICAL(&mux);
    gptimer_alarm_config_t alarm={.alarm_count=protocol.start_us};
    ESP_ERROR_CHECK(gptimer_set_alarm_action(clock_timer,&alarm));
    portENTER_CRITICAL(&mux); running=true; portEXIT_CRITICAL(&mux);
    ESP_ERROR_CHECK(gptimer_start(clock_timer));
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(100));
        portENTER_CRITICAL(&mux);
        bool finished=protocol.finished;
        portEXIT_CRITICAL(&mux);
        if (!finished) continue;
        ESP_ERROR_CHECK(gpio_intr_disable(ACK));
        ESP_ERROR_CHECK(gptimer_stop(clock_timer));
        /* REQ permanece no último nível até reset entre execuções. */
        vTaskDelete(NULL);
    }
}
void app_main(void)
{
    ESP_ERROR_CHECK(esp_cpu_get_core_id()==0?ESP_OK:ESP_ERR_INVALID_STATE);
    /* Não inicializar Wi-Fi, Bluetooth, NVS ou gravação em flash na coleta. */
    ESP_ERROR_CHECK(GPIO_IS_VALID_OUTPUT_GPIO(REQ)&&GPIO_IS_VALID_GPIO(ACK)&&
        REQ!=ACK&&REQ!=UART_TX_GPIO&&REQ!=UART_RX_GPIO&&
        ACK!=UART_TX_GPIO&&ACK!=UART_RX_GPIO?ESP_OK:ESP_ERR_INVALID_ARG);
    ESP_ERROR_CHECK(gpio_set_direction(REQ,GPIO_MODE_OUTPUT));
    ESP_ERROR_CHECK(gpio_set_level(REQ,0));
    uart_config_t uart={.baud_rate=115200,.data_bits=UART_DATA_8_BITS,
        .parity=UART_PARITY_DISABLE,.stop_bits=UART_STOP_BITS_1,
        .flow_ctrl=UART_HW_FLOWCTRL_DISABLE,.source_clk=UART_SCLK_DEFAULT};
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_0,&uart));
    ESP_ERROR_CHECK(uart_set_pin(UART_NUM_0,UART_TX_GPIO,UART_RX_GPIO,UART_PIN_NO_CHANGE,UART_PIN_NO_CHANGE));
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0,256,4096,0,NULL,0));
    const char ready[]="SE10 READY: START tag offers warmup_offers\n";
    uart_write_bytes(UART_NUM_0,ready,sizeof(ready)-1);
    char line[96]; size_t used=0;
    for (;;) {
        uint8_t c;
        if (uart_read_bytes(UART_NUM_0,&c,1,pdMS_TO_TICKS(1000))!=1) continue;
        if (c!='\n') {
            if (used+1<sizeof(line)) line[used++]=(char)c;
            else used=0;
            continue;
        }
        line[used]=0; used=0;
        char extra;
        if (sscanf(line,"START %"SCNu32" %"SCNu32" %"SCNu32" %c",
            &run_tag,&offer_count,&warmup_count,&extra)!=3 ||
            !offer_count || offer_count>720000 || warmup_count>12000) {
            const char error[]="SE10 ERROR invalid START\n";
            uart_write_bytes(UART_NUM_0,error,sizeof(error)-1);
            continue;
        }
        break;
    }
    records=xQueueCreate(QUEUE_SIZE,sizeof(struct se_record));
    ESP_ERROR_CHECK(records?ESP_OK:ESP_ERR_NO_MEM);
    struct se_packet begin={.kind=SE_BEGIN,.index=offer_count,
        .flags=(uint32_t)REQ|((uint32_t)ACK<<16),.a=SE_PERIOD_US,.b=SE_DEADLINE_US,
        .c=SE_GRACE_US,.d=warmup_count,.extra=QUEUE_SIZE};
    send_packet(&begin);
    ESP_ERROR_CHECK(xTaskCreatePinnedToCore(writer,"capture_writer",4096,NULL,5,NULL,0)==pdPASS?ESP_OK:ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreatePinnedToCore(measurement,"measurement",4096,NULL,10,NULL,1)==pdPASS?ESP_OK:ESP_ERR_NO_MEM);
}
