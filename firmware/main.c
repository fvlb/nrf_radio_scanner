/* USB CDC scaffold adapted from Nordic's nRF5 SDK usbd_cdc_acm example.
 * Copyright (c) 2017 - 2021, Nordic Semiconductor ASA.
 * Nordic license and disclaimer: see THIRD_PARTY.md.
 * Scanner implementation copyright (c) 2026 fvlb.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "app_error.h"
#include "app_timer.h"
#include "app_usbd.h"
#include "app_usbd_cdc_acm.h"
#include "app_usbd_core.h"
#include "app_usbd_serial_num.h"
#include "nrf.h"
#include "nrf_delay.h"
#include "nrf_drv_clock.h"
#include "nrf_drv_power.h"
#include "nrf_drv_usbd.h"
#include "nrf_gpio.h"

#define BIN_COUNT 81u
#define SAMPLE_COUNT 16u
#define SAMPLE_INTERVAL_US 128u
#define LED_GREEN NRF_GPIO_PIN_MAP(0, 6)
#define BUTTON NRF_GPIO_PIN_MAP(1, 6)

static bool port_open;
static bool tx_busy;
static bool boot_requested;
static uint8_t rx_byte;
static uint8_t packet[260] __attribute__((aligned(4)));
static char tx_buffer[1200];

static void cdc_handler(app_usbd_class_inst_t const *inst, app_usbd_cdc_acm_user_event_t event);

APP_USBD_CDC_ACM_GLOBAL_DEF(cdc, cdc_handler, 0, 1, NRF_DRV_USBD_EPIN2, NRF_DRV_USBD_EPIN1,
                            NRF_DRV_USBD_EPOUT1, APP_USBD_CDC_COMM_PROTOCOL_NONE);

static uint32_t now_us(void)
{
    NRF_TIMER2->TASKS_CAPTURE[0] = 1;
    return NRF_TIMER2->CC[0];
}

static void usb_process(void)
{
    while (app_usbd_event_queue_process()) {
    }
}

static void cdc_handler(app_usbd_class_inst_t const *inst, app_usbd_cdc_acm_user_event_t event)
{
    (void)inst;
    switch (event) {
    case APP_USBD_CDC_ACM_USER_EVT_PORT_OPEN:
        port_open = true;
        (void)app_usbd_cdc_acm_read(&cdc, &rx_byte, 1);
        break;
    case APP_USBD_CDC_ACM_USER_EVT_PORT_CLOSE:
        port_open = false;
        /* Do not reuse a transmit buffer until the transfer has completed. */
        break;
    case APP_USBD_CDC_ACM_USER_EVT_TX_DONE:
        tx_busy = false;
        break;
    case APP_USBD_CDC_ACM_USER_EVT_RX_DONE:
        do {
            if (rx_byte == 'b') {
                boot_requested = true;
            }
        } while (app_usbd_cdc_acm_read(&cdc, &rx_byte, 1) == NRF_SUCCESS);
        break;
    default:
        break;
    }
}

static void usb_handler(app_usbd_event_type_t event)
{
    switch (event) {
    case APP_USBD_EVT_STOPPED:
        port_open = false;
        tx_busy = false;
        app_usbd_disable();
        break;
    case APP_USBD_EVT_POWER_DETECTED:
        if (!nrf_drv_usbd_is_enabled()) {
            app_usbd_enable();
        }
        break;
    case APP_USBD_EVT_POWER_REMOVED:
        app_usbd_stop();
        break;
    case APP_USBD_EVT_POWER_READY:
        app_usbd_start();
        break;
    case APP_USBD_EVT_DRV_RESET:
        port_open = false;
        tx_busy = false;
        break;
    default:
        break;
    }
}

static bool wait_event(volatile uint32_t *event)
{
    uint32_t start = now_us();
    while (*event == 0) {
        if ((uint32_t)(now_us() - start) > 2000) {
            return false;
        }
    }
    return true;
}

static void radio_disable(void)
{
    if (NRF_RADIO->STATE != RADIO_STATE_STATE_Disabled) {
        NRF_RADIO->EVENTS_DISABLED = 0;
        NRF_RADIO->TASKS_DISABLE = 1;
        if (!wait_event(&NRF_RADIO->EVENTS_DISABLED)) {
            NVIC_SystemReset();
        }
    }
}

static void radio_init(void)
{
    NRF_RADIO->POWER = 1;
    NRF_RADIO->MODE = RADIO_MODE_MODE_Ble_1Mbit;
    NRF_RADIO->TXPOWER = 0;
    NRF_RADIO->PCNF0 = (8u << RADIO_PCNF0_LFLEN_Pos);
    NRF_RADIO->PCNF1 = (255u << RADIO_PCNF1_MAXLEN_Pos) | (3u << RADIO_PCNF1_BALEN_Pos);
    NRF_RADIO->BASE0 = 0x89BED600;
    NRF_RADIO->PREFIX0 = 0x8E;
    NRF_RADIO->RXADDRESSES = 1;
    NRF_RADIO->CRCCNF = 0;
    NRF_RADIO->PACKETPTR = (uint32_t)packet;
    /* No TX tasks, acknowledgements, SoftDevice, or RADIO interrupts. */
    NRF_RADIO->SHORTS = 0;
    NRF_RADIO->INTENCLR = 0xFFFFFFFF;
}

static bool measure_bin(uint32_t offset_mhz, int *mean, int *peak)
{
    radio_disable();
    NRF_RADIO->FREQUENCY = offset_mhz;
    NRF_RADIO->EVENTS_READY = 0;
    NRF_RADIO->TASKS_RXEN = 1;
    if (!wait_event(&NRF_RADIO->EVENTS_READY)) {
        return false;
    }
    NRF_RADIO->EVENTS_END = 0;
    NRF_RADIO->TASKS_START = 1;
    nrf_delay_us(40);

    int sum = 0;
    int strongest = -127;
    for (unsigned sample = 0; sample < SAMPLE_COUNT; ++sample) {
        /* An accidental address match may finish RX; keep receiving. */
        if (NRF_RADIO->EVENTS_END) {
            NRF_RADIO->EVENTS_END = 0;
            NRF_RADIO->TASKS_START = 1;
        }
        NRF_RADIO->EVENTS_RSSIEND = 0;
        NRF_RADIO->TASKS_RSSISTART = 1;
        if (!wait_event(&NRF_RADIO->EVENTS_RSSIEND)) {
            return false;
        }
        int rssi = -(int)NRF_RADIO->RSSISAMPLE;
        sum += rssi;
        if (rssi > strongest) {
            strongest = rssi;
        }
        nrf_delay_us(SAMPLE_INTERVAL_US);
        usb_process();
    }
    *mean = sum / (int)SAMPLE_COUNT;
    *peak = strongest;
    return true;
}

static void enter_bootloader(void)
{
    radio_disable();
    /* Standard Nordic SDK PCA10059 bootloader enter-DFU marker. */
    NRF_POWER->GPREGRET = 0xB1;
    __DSB();
    NVIC_SystemReset();
}

int main(void)
{
    /* USB DFU image never writes MBR, bootloader, or UICR. */
    SCB->VTOR = 0x1000;
    __DSB();
    __ISB();
    nrf_gpio_cfg_output(LED_GREEN);
    nrf_gpio_pin_set(LED_GREEN);
    nrf_gpio_cfg_input(BUTTON, NRF_GPIO_PIN_PULLUP);

    NRF_TIMER2->MODE = TIMER_MODE_MODE_Timer;
    NRF_TIMER2->BITMODE = TIMER_BITMODE_BITMODE_32Bit;
    NRF_TIMER2->PRESCALER = 4;
    NRF_TIMER2->TASKS_START = 1;

    APP_ERROR_CHECK(nrf_drv_clock_init());
    nrf_drv_clock_lfclk_request(NULL);
    while (!nrf_drv_clock_lfclk_is_running()) {
    }
    APP_ERROR_CHECK(app_timer_init());
    app_usbd_serial_num_generate();
    static const app_usbd_config_t config = {.ev_state_proc = usb_handler};
    APP_ERROR_CHECK(app_usbd_init(&config));
    APP_ERROR_CHECK(app_usbd_class_append(app_usbd_cdc_acm_class_inst_get(&cdc)));
    APP_ERROR_CHECK(app_usbd_power_events_enable());

    uint32_t sequence = 0;
    uint32_t button_since = 0;
    while (true) {
        usb_process();
        if (nrf_gpio_pin_read(BUTTON) == 0) {
            if (button_since == 0) {
                button_since = now_us();
            } else if ((uint32_t)(now_us() - button_since) > 1000000) {
                boot_requested = true;
            }
        } else {
            button_since = 0;
        }
        if (boot_requested) {
            enter_bootloader();
        }
        if (!port_open || tx_busy) {
            continue;
        }

        /* Let USB own HFCLK startup; request an additional reference for RF
         * only after enumeration, as in the SDK USB peripheral lifecycle. */
        static bool radio_ready;
        if (!radio_ready) {
            nrf_drv_clock_hfclk_request(NULL);
            while (!nrf_drv_clock_hfclk_is_running()) {
                usb_process();
            }
            radio_init();
            radio_ready = true;
        }

        int means[BIN_COUNT];
        int peaks[BIN_COUNT];
        uint32_t start = now_us();
        bool valid = true;
        for (unsigned bin = 0; bin < BIN_COUNT; ++bin) {
            if (!measure_bin(bin, &means[bin], &peaks[bin])) {
                valid = false;
                break;
            }
        }
        radio_disable();
        if (!valid) {
            NVIC_SystemReset();
        }
        uint32_t elapsed = now_us() - start;
        size_t pos = (size_t)snprintf(tx_buffer, sizeof(tx_buffer),
                                      "{\"v\":1,\"seq\":%lu,\"start_us\":%lu,\"sweep_us\":%lu,"
                                      "\"first_mhz\":2400,\"step_mhz\":1,\"samples\":%u,\"mean\":[",
                                      (unsigned long)sequence++, (unsigned long)start,
                                      (unsigned long)elapsed, SAMPLE_COUNT);
        for (unsigned bin = 0; bin < BIN_COUNT; ++bin) {
            pos += (size_t)snprintf(tx_buffer + pos, sizeof(tx_buffer) - pos, "%s%d",
                                    bin ? "," : "", means[bin]);
        }
        pos += (size_t)snprintf(tx_buffer + pos, sizeof(tx_buffer) - pos, "],\"peak\":[");
        for (unsigned bin = 0; bin < BIN_COUNT; ++bin) {
            pos += (size_t)snprintf(tx_buffer + pos, sizeof(tx_buffer) - pos, "%s%d",
                                    bin ? "," : "", peaks[bin]);
        }
        pos += (size_t)snprintf(tx_buffer + pos, sizeof(tx_buffer) - pos, "]}\n");
        /* Worst case is under 1000 bytes, including uint32 decimal timestamps. */
        APP_ERROR_CHECK_BOOL(pos < sizeof(tx_buffer));
        if (port_open) {
            tx_busy = true;
            ret_code_t result = app_usbd_cdc_acm_write(&cdc, tx_buffer, pos);
            if (result != NRF_SUCCESS) {
                tx_busy = false;
            }
        }
        nrf_gpio_pin_toggle(LED_GREEN);
    }
}
