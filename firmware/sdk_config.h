/* Overrides for the SDK's USB CDC example configuration. */
#ifndef SCANNER_SDK_CONFIG_H
#define SCANNER_SDK_CONFIG_H

#define NRF_LOG_ENABLED 0
#define NRF_CLI_ENABLED 0
/* PCA10059 has no 32.768 kHz crystal. */
#define CLOCK_CONFIG_LF_SRC 0
#define NRFX_CLOCK_CONFIG_LF_SRC 0
#define APP_USBD_CONFIG_SELF_POWERED 0
#define APP_USBD_STRINGS_PRODUCT APP_USBD_STRING_DESC("nRF Radio Scanner")

#include "sdk_defaults.h"
#endif
