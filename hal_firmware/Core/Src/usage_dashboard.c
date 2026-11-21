#include "main.h"
#include "app_debug.h"
#include "esp8266_common_hal.h"
#include "FreeRTOS.h"
#include "task.h"
#include "lvgl.h"
#include <stdio.h>
#include <string.h>
#include <time.h>

/* Network task owns transport. Only the UI task may call LVGL. */
typedef enum { NET_BOOT, NET_WIFI, NET_ONLINE, NET_OFFLINE, NET_CONFIG } NetState;
typedef enum { FACE_HAPPY, FACE_BLINK, FACE_SLEEPY, FACE_CONCERNED, FACE_XX } MascotFace;
static UsageSnapshot shared_snapshot;
static TickType_t shared_received;
static uint8_t shared_valid, rtc_valid;
static NetState network_state=NET_BOOT;
static lv_obj_t *mascot, *face_layer, *title, *clock_label, *date_label, *footer, *led;
static lv_obj_t *values[2], *kinds[2], *bars[2], *resets[2], *dots[2];
static MascotFace current_face=(MascotFace)255;
/* SWD-readable diagnostics for boards without the USB serial cable attached.
 * No credentials, hostnames or account identifiers are stored here. */
volatile uint32_t Usage_Debug[16]={0x554b414eU};
extern void lv_port_disp_init(void);

static lv_obj_t *box(lv_obj_t *parent,int x,int y,int w,int h,uint32_t color,int radius)
{
    lv_obj_t *obj=lv_obj_create(parent);
    lv_obj_remove_style_all(obj);lv_obj_clear_flag(obj,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_pos(obj,x,y);lv_obj_set_size(obj,w,h);
    lv_obj_set_style_bg_color(obj,lv_color_hex(color),0);
    lv_obj_set_style_bg_opa(obj,LV_OPA_COVER,0);lv_obj_set_style_radius(obj,radius,0);
    return obj;
}
static lv_obj_t *label(lv_obj_t *parent,int x,int y,int w,const lv_font_t *font,uint32_t color)
{
    lv_obj_t *obj=lv_label_create(parent);lv_obj_set_pos(obj,x,y);lv_obj_set_width(obj,w);
    lv_obj_set_style_text_font(obj,font,0);lv_obj_set_style_text_color(obj,lv_color_hex(color),0);
    lv_label_set_long_mode(obj,LV_LABEL_LONG_CLIP);lv_label_set_text(obj,"");return obj;
}
static void pixel(lv_obj_t *parent,int x,int y,int w,int h,uint32_t color)
{
    box(parent,x*6,y*6,w*6,h*6,color,0);
}
static void set_face(MascotFace face)
{
    if(face==current_face)return;
    current_face=face;lv_obj_clean(face_layer);
    const uint32_t eye=0x02080a;
    if(face==FACE_BLINK || face==FACE_SLEEPY) {
        pixel(face_layer,face==FACE_BLINK?5:4,3,face==FACE_BLINK?2:3,1,eye);
        pixel(face_layer,11,3,face==FACE_BLINK?2:3,1,eye);
        if(face==FACE_SLEEPY)pixel(face_layer,8,6,1,1,eye);
    } else if(face==FACE_XX) {
        for(int x=4;x<=11;x+=7) {
            pixel(face_layer,x,1,1,1,eye);pixel(face_layer,x+2,1,1,1,eye);
            pixel(face_layer,x+1,2,1,1,eye);pixel(face_layer,x,3,1,1,eye);pixel(face_layer,x+2,3,1,1,eye);
        }
        pixel(face_layer,8,6,1,1,eye);
    } else {
        pixel(face_layer,5,2,2,2,eye);pixel(face_layer,11,2,2,2,eye);
        if(face==FACE_CONCERNED) {
            pixel(face_layer,4,1,3,1,eye);pixel(face_layer,11,1,3,1,eye);pixel(face_layer,8,6,3,1,eye);
        }
    }
}
static void create_mascot(lv_obj_t *screen)
{
    /* Original Clawd pixel unit, silhouette, palette and facial geometry. */
    mascot=box(screen,10,8,108,84,0,0);lv_obj_set_style_bg_opa(mascot,LV_OPA_TRANSP,0);
    pixel(mascot,3,0,12,4,0xf25745);pixel(mascot,0,4,18,4,0xf25745);pixel(mascot,3,8,12,2,0xf25745);
    const int legs[]={3,6,10,13};
    for(unsigned k=0;k<4;k++)pixel(mascot,legs[k],10,2,4,0xf25745);
    face_layer=box(mascot,0,0,108,48,0,0);lv_obj_set_style_bg_opa(face_layer,LV_OPA_TRANSP,0);
    set_face(FACE_HAPPY);
    lv_anim_t anim;lv_anim_init(&anim);lv_anim_set_var(&anim,mascot);
    lv_anim_set_exec_cb(&anim,(lv_anim_exec_xcb_t)lv_obj_set_y);
    lv_anim_set_values(&anim,8,4);lv_anim_set_time(&anim,850);lv_anim_set_playback_time(&anim,850);
    lv_anim_set_repeat_count(&anim,LV_ANIM_REPEAT_INFINITE);lv_anim_set_path_cb(&anim,lv_anim_path_ease_in_out);lv_anim_start(&anim);
}
static void create_dashboard(void)
{
    lv_obj_t *screen=lv_scr_act();lv_obj_clean(screen);lv_obj_clear_flag(screen,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(screen,lv_color_hex(0x04070c),0);lv_obj_set_style_bg_opa(screen,LV_OPA_COVER,0);
    create_mascot(screen);
    title=label(screen,128,14,106,&lv_font_montserrat_16,0x63e6be);
    clock_label=label(screen,128,40,106,&lv_font_montserrat_24,0xe6edf5);
    date_label=label(screen,128,74,110,&lv_font_montserrat_12,0x8c9aab);
    box(screen,10,98,220,1,0x1c2938,0);
    for(int k=0;k<2;k++) {
        lv_obj_t *card=box(screen,8,104+k*60,224,54,0x101925,8);
        values[k]=label(card,10,2,100,&lv_font_montserrat_24,0xe6edf5);
        kinds[k]=label(card,112,8,102,&lv_font_montserrat_12,0xaab8c8);
        lv_obj_set_style_text_align(kinds[k],LV_TEXT_ALIGN_RIGHT,0);
        bars[k]=lv_bar_create(card);lv_obj_remove_style_all(bars[k]);
        lv_obj_set_pos(bars[k],10,32);lv_obj_set_size(bars[k],204,5);lv_bar_set_range(bars[k],0,1000);
        lv_obj_set_style_bg_color(bars[k],lv_color_hex(0x263348),LV_PART_MAIN);
        lv_obj_set_style_bg_opa(bars[k],LV_OPA_COVER,LV_PART_MAIN);
        lv_obj_set_style_bg_opa(bars[k],LV_OPA_COVER,LV_PART_INDICATOR);
        lv_obj_set_style_radius(bars[k],2,LV_PART_MAIN);lv_obj_set_style_radius(bars[k],2,LV_PART_INDICATOR);
        resets[k]=label(card,10,39,208,&lv_font_montserrat_12,0xaab8c8);
    }
    dots[0]=box(screen,11,228,5,5,0x63e6be,3);dots[1]=box(screen,21,228,5,5,0x334258,3);
    led=box(screen,38,229,4,4,0x63e6be,2);footer=label(screen,46,222,184,&lv_font_montserrat_12,0x8997a9);
}
static void update_dashboard(TickType_t tick)
{
    UsageSnapshot snapshot;TickType_t received;uint8_t valid,time_valid;NetState net;
    taskENTER_CRITICAL();snapshot=shared_snapshot;received=shared_received;valid=shared_valid;
    net=network_state;time_valid=rtc_valid;taskEXIT_CRITICAL();
    unsigned seconds=tick/configTICK_RATE_HZ,page=(seconds/8U)%2U;
    Usage_Debug[1]=seconds;Usage_Debug[2]=page;Usage_Debug[3]=(uint32_t)net;
    Usage_Debug[11]=(uint32_t)xPortGetFreeHeapSize();
    Usage_Debug[12]=(uint32_t)uxTaskGetStackHighWaterMark(NULL);
    uint32_t now=valid?snapshot.now+(tick-received)/configTICK_RATE_HZ:0;
    UsageProvider *provider=&snapshot.providers[page];
    uint32_t age=now>provider->observed_at?now-provider->observed_at:0;
    uint8_t disconnected=net!=NET_ONLINE;
    uint8_t stale=valid && (age>300U || provider->status!=USAGE_OK || disconnected);
    uint16_t worst=0;uint8_t refreshing=0;
    uint32_t accent=page?0xf25745:0x63e6be;char text[64];
    lv_label_set_text(title,page?"CLAUDE":"CODEX");lv_obj_set_style_text_color(title,lv_color_hex(accent),0);
    for(unsigned k=0;k<2;k++) {
        UsageWindow *w=&provider->windows[k];
        uint8_t expired=w->valid && !Usage_WindowActive(w,now);
        if(expired)refreshing=1;
        if(Usage_WindowActive(w,now) && w->used_tenths>worst)worst=w->used_tenths;
        if(w->valid)snprintf(text,sizeof(text),"%u%%",(unsigned)((w->used_tenths+5U)/10U));else snprintf(text,sizeof(text),"--");
        lv_label_set_text(values[k],text);lv_obj_set_style_text_color(values[k],lv_color_hex(stale||expired?0x7f8c9e:0xe6edf5),0);
        if(!w->valid)snprintf(text,sizeof(text),k?"Weekly used":"5h used");
        else if(w->duration_mins==10080U)snprintf(text,sizeof(text),"Weekly used");
        else if(w->duration_mins%60U==0)snprintf(text,sizeof(text),"%luh used",(unsigned long)(w->duration_mins/60U));
        else snprintf(text,sizeof(text),"%lum used",(unsigned long)w->duration_mins);
        lv_label_set_text(kinds[k],text);lv_bar_set_value(bars[k],w->valid?w->used_tenths:0,LV_ANIM_OFF);
        lv_obj_set_style_bg_color(bars[k],lv_color_hex(stale||expired?0x526176:(w->used_tenths>=800?0xf2b65b:accent)),LV_PART_INDICATOR);
        if(!w->valid)snprintf(text,sizeof(text),"Waiting for report");
        else Usage_FormatCountdown(w->resets_at>now?w->resets_at-now:0,text,sizeof(text));
        lv_label_set_text(resets[k],text);lv_obj_set_style_bg_color(dots[k],lv_color_hex(k==page?accent:0x334258),0);
    }
    if(net==NET_CONFIG)snprintf(text,sizeof(text),"Configure companion");
    else if(net==NET_BOOT || net==NET_WIFI)snprintf(text,sizeof(text),"Connecting WiFi");
    else if(disconnected)snprintf(text,sizeof(text),"PC disconnected");
    else if(provider->status==USAGE_AUTH)snprintf(text,sizeof(text),"Login required");
    else if(provider->status==USAGE_ERROR)snprintf(text,sizeof(text),"Provider unavailable");
    else if(!provider->observed_at || provider->status==USAGE_WAITING)snprintf(text,sizeof(text),"Waiting for %s",page?"Claude":"Codex");
    else if(stale)snprintf(text,sizeof(text),"Stale - %lum ago",(unsigned long)(age/60));
    else if(worst>=800)snprintf(text,sizeof(text),worst>=1000?"Limit reached":"Approaching limit");
    else if(refreshing)snprintf(text,sizeof(text),"Refreshing usage");
    else if(age<60)snprintf(text,sizeof(text),"%s %lus ago",page?"Reported":"Updated",(unsigned long)age);
    else snprintf(text,sizeof(text),"%s %lum ago",page?"Reported":"Updated",(unsigned long)(age/60));
    lv_label_set_text(footer,text);lv_obj_set_style_bg_color(led,lv_color_hex(stale||disconnected?0xf2b65b:0x63e6be),0);
    MascotFace face=FACE_HAPPY;
    if(disconnected && valid)face=FACE_XX;
    else if(worst>=1000 || !provider->observed_at || provider->status==USAGE_WAITING)face=FACE_SLEEPY;
    else if(stale || worst>=800)face=FACE_CONCERNED;
    else if(seconds%7U==0)face=FACE_BLINK;
    set_face(face);lv_obj_set_x(mascot,8+(seconds/6U)%5U);
    RTC_TimeTypeDef rt={0};RTC_DateTypeDef rd={0};
    if(time_valid && HAL_RTC_GetTime(&hrtc,&rt,RTC_FORMAT_BIN)==HAL_OK && HAL_RTC_GetDate(&hrtc,&rd,RTC_FORMAT_BIN)==HAL_OK) {
        snprintf(text,sizeof(text),"%02u:%02u",rt.Hours,rt.Minutes);lv_label_set_text(clock_label,text);
        const char *months[]={"JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"};
        snprintf(text,sizeof(text),"%02u %s HKT",rd.Date,rd.Month>=1&&rd.Month<=12?months[rd.Month-1]:"---");lv_label_set_text(date_label,text);
    } else {lv_label_set_text(clock_label,"--:--");lv_label_set_text(date_label,"Waiting for HKT");}
}
static void ui_task(void *arg)
{
    (void)arg;lv_init();HAL_TIM_Base_Start_IT(&htim3);lv_port_disp_init();create_dashboard();
    printf("Usagekani LVGL dashboard ready\r\n");uint32_t shown=UINT32_MAX;
    for(;;) {
        TickType_t now=xTaskGetTickCount();
        if(now/configTICK_RATE_HZ!=shown){shown=now/configTICK_RATE_HZ;update_dashboard(now);}
        lv_timer_handler();vTaskDelay(pdMS_TO_TICKS(10));
    }
}
static void network_task(void *arg)
{
    (void)arg;taskENTER_CRITICAL();network_state=NET_WIFI;taskEXIT_CRITICAL();
    if(!USAGEKANI_HTTP_HOST[0] || !USAGEKANI_DEVICE_TOKEN[0]) {
        taskENTER_CRITICAL();network_state=NET_CONFIG;taskEXIT_CRITICAL();
        printf("Usagekani local configuration missing\r\n");vTaskDelete(NULL);
    }
    esp8266_sta_connect();unsigned failures=0;
    for(;;) {
        UsageSnapshot next;u8 error=ESP8266_GetUsage(&next);
        Usage_Debug[5]=error;Usage_Debug[13]=(uint32_t)uxTaskGetStackHighWaterMark(NULL);
        if(!error) {
            time_t epoch=(time_t)next.now+8*3600;struct tm *calendar=gmtime(&epoch);uint8_t synced=0;
            if(calendar) {
                TimeStruct t={0};t.year=(uint16_t)(calendar->tm_year+1900);t.month=(u8)(calendar->tm_mon+1);
                t.day=(u8)calendar->tm_mday;t.hour=(u8)calendar->tm_hour;t.minute=(u8)calendar->tm_min;t.second=(u8)calendar->tm_sec;
                t.week=(u8)(calendar->tm_wday?calendar->tm_wday:7);synced=My_RTC_Init(t)==0;
            }
            taskENTER_CRITICAL();shared_snapshot=next;shared_received=xTaskGetTickCount();shared_valid=1;
            network_state=NET_ONLINE;if(synced)rtc_valid=1;taskEXIT_CRITICAL();
            printf("Usagekani feed OK: codex=%u/%u claude=%u/%u\r\n",next.providers[0].windows[0].used_tenths,
                next.providers[0].windows[1].used_tenths,next.providers[1].windows[0].used_tenths,next.providers[1].windows[1].used_tenths);
            failures=0;
            Usage_Debug[4]++;
            Usage_Debug[10]=0;
            for(unsigned p=0;p<2;p++)for(unsigned w=0;w<2;w++) {
                Usage_Debug[6+p*2+w]=next.providers[p].windows[w].used_tenths;
                if(next.providers[p].windows[w].valid)Usage_Debug[10]|=1U<<(p*2+w);
            }
            Usage_Debug[14]=next.now;
        } else {
            taskENTER_CRITICAL();network_state=NET_OFFLINE;taskEXIT_CRITICAL();printf("Usagekani feed error %u\r\n",error);
            if(++failures>=3){esp8266_sta_connect();failures=0;}
        }
        vTaskDelay(pdMS_TO_TICKS(30000));
    }
}
void Usage_CreateTasks(void)
{
    if(xTaskCreate(ui_task,"usage-ui",1536,NULL,3,NULL)!=pdPASS ||
       xTaskCreate(network_task,"usage-net",2560,NULL,2,NULL)!=pdPASS)Error_Handler();
}
