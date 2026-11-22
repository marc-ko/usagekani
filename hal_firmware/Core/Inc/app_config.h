#ifndef APP_CONFIG_H
#define APP_CONFIG_H

#define APP_FW_NAME "Usagekani HAL"
#define APP_FW_VERSION "0.2.0-usage-monitor"

#define APP_UART_BAUDRATE 115200U
#define APP_LCD_WIDTH 240U
#define APP_LCD_HEIGHT 240U

#define APP_LCD_FLIP_BY_PRISM 1
#define APP_LCD_USE_HORIZONTAL 0

#define APP_ESP8266_RX_LEN 4096U
#define APP_USART6_RX_LEN 1500U
#define APP_DEBUG_RX_LEN 200U

#if defined(__has_include)
#if __has_include("app_config_local.h")
#include "app_config_local.h"
#endif
#endif

/*
 * Keep credentials out of git. Define these in CubeIDE project symbols or a
 * local, ignored app_config_local.h if the ESP8266 client is enabled.
 */
#ifndef VPC_WIFI_SSID
#define VPC_WIFI_SSID ""
#endif

#ifndef VPC_WIFI_PASSWORD
#define VPC_WIFI_PASSWORD ""
#endif

#ifndef VPC_OPENCLAW_HOST
#define VPC_OPENCLAW_HOST ""
#endif

#ifndef VPC_OPENCLAW_HTTP_HOST
#define VPC_OPENCLAW_HTTP_HOST ""
#endif

#ifndef VPC_OPENCLAW_HTTP_PORT
#define VPC_OPENCLAW_HTTP_PORT "80"
#endif

#ifndef VPC_OPENCLAW_TOKEN
#define VPC_OPENCLAW_TOKEN ""
#endif

#ifndef USAGEKANI_HTTP_HOST
#define USAGEKANI_HTTP_HOST ""
#endif
#ifndef USAGEKANI_HTTP_PORT
#define USAGEKANI_HTTP_PORT "8765"
#endif
#ifndef USAGEKANI_DEVICE_TOKEN
#define USAGEKANI_DEVICE_TOKEN ""
#endif

#endif
