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
typedef struct {
    lv_obj_t *body, *title, *footer_root, *footer, *led;
    lv_obj_t *values[2], *kinds[2], *bars[2], *resets[2];
} ProviderPage;
static ProviderPage pages[2];
static lv_obj_t *mascot, *face_layer, *clock_label, *date_label, *page_indicator;
static uint8_t shown_page, transition_from, transition_to, transitioning;
static uint32_t transition_started;
static MascotFace page_faces[2];
static MascotFace current_face=(MascotFace)255;
/* SWD-readable diagnostics for boards without the USB serial cable attached.
 * No credentials, hostnames or account identifiers are stored here. */
volatile uint32_t Usage_Debug[16]={0x554b414eU};
/* Completed slides, frame callbacks, last duration (ms), LVGL free bytes. */
volatile uint32_t Usage_AnimationDebug[4];
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
    lv_obj_t *heading_clip=box(screen,126,0,114,36,0,0);
    lv_obj_set_style_bg_opa(heading_clip,LV_OPA_TRANSP,0);
    clock_label=label(screen,128,40,106,&lv_font_montserrat_24,0xe6edf5);
    date_label=label(screen,128,74,110,&lv_font_montserrat_12,0x8c9aab);
    box(screen,10,98,220,1,0x1c2938,0);
    lv_obj_t *body_clip=box(screen,0,101,240,119,0,0);
    lv_obj_t *footer_clip=box(screen,38,221,194,19,0,0);
    lv_obj_set_style_bg_opa(body_clip,LV_OPA_TRANSP,0);
    lv_obj_set_style_bg_opa(footer_clip,LV_OPA_TRANSP,0);
    for(unsigned page=0;page<2;page++) {
        ProviderPage *w=&pages[page];
        w->title=label(heading_clip,2,14,106,&lv_font_montserrat_16,page?0xf25745:0x63e6be);
        w->body=box(body_clip,0,0,240,119,0,0);
        w->footer_root=box(footer_clip,0,0,194,19,0,0);
        lv_obj_set_style_bg_opa(w->body,LV_OPA_TRANSP,0);
        lv_obj_set_style_bg_opa(w->footer_root,LV_OPA_TRANSP,0);
        for(int k=0;k<2;k++) {
            lv_obj_t *card=box(w->body,8,3+k*60,224,54,0x101925,8);
            w->values[k]=label(card,10,2,100,&lv_font_montserrat_24,0xe6edf5);
            w->kinds[k]=label(card,112,8,102,&lv_font_montserrat_12,0xaab8c8);
            lv_obj_set_style_text_align(w->kinds[k],LV_TEXT_ALIGN_RIGHT,0);
            w->bars[k]=lv_bar_create(card);lv_obj_remove_style_all(w->bars[k]);
            lv_obj_set_pos(w->bars[k],10,32);lv_obj_set_size(w->bars[k],204,5);lv_bar_set_range(w->bars[k],0,1000);
            lv_obj_set_style_bg_color(w->bars[k],lv_color_hex(0x263348),LV_PART_MAIN);
            lv_obj_set_style_bg_opa(w->bars[k],LV_OPA_COVER,LV_PART_MAIN);
            lv_obj_set_style_bg_opa(w->bars[k],LV_OPA_COVER,LV_PART_INDICATOR);
            lv_obj_set_style_radius(w->bars[k],2,LV_PART_MAIN);lv_obj_set_style_radius(w->bars[k],2,LV_PART_INDICATOR);
            w->resets[k]=label(card,10,39,208,&lv_font_montserrat_12,0xaab8c8);
        }
        w->led=box(w->footer_root,0,8,4,4,0x63e6be,2);
        w->footer=label(w->footer_root,8,1,184,&lv_font_montserrat_12,0x8997a9);
        if(page) {
            lv_obj_add_flag(w->body,LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(w->title,LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(w->footer_root,LV_OBJ_FLAG_HIDDEN);
        }
    }
    box(screen,11,228,5,5,0x334258,3);box(screen,21,228,5,5,0x334258,3);
    page_indicator=box(screen,11,228,7,5,0x63e6be,3);
}

/* One timeline keeps the label, cards, footer and indicator synchronized.
 * Each has its own clipped viewport, so nothing slides across Clawd/clock. */
static void slide_frame(void *unused,int32_t progress)
{
    (void)unused;
    int direction=transition_to>transition_from?1:-1;
    ProviderPage *out=&pages[transition_from], *in=&pages[transition_to];
    lv_obj_set_x(out->body,-direction*240*progress/1024);
    lv_obj_set_x(in->body,direction*240*(1024-progress)/1024);
    lv_obj_set_x(out->title,2-direction*114*progress/1024);
    lv_obj_set_x(in->title,2+direction*114*(1024-progress)/1024);
    lv_obj_set_x(out->footer_root,-direction*194*progress/1024);
    lv_obj_set_x(in->footer_root,direction*194*(1024-progress)/1024);
    lv_obj_set_x(page_indicator,11+10*transition_from+direction*10*progress/1024);
    lv_color_t from=lv_color_hex(transition_from?0xf25745:0x63e6be);
    lv_color_t to=lv_color_hex(transition_to?0xf25745:0x63e6be);
    lv_obj_set_style_bg_color(page_indicator,lv_color_mix(to,from,(uint8_t)(progress*255/1024)),0);
    Usage_AnimationDebug[1]++;
}
static void slide_ready(lv_anim_t *animation)
{
    (void)animation;
    ProviderPage *out=&pages[transition_from];
    lv_obj_add_flag(out->body,LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(out->title,LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(out->footer_root,LV_OBJ_FLAG_HIDDEN);
    shown_page=transition_to;transitioning=0;
    set_face(page_faces[shown_page]);
    Usage_Debug[2]=shown_page;Usage_Debug[15]++;
    Usage_AnimationDebug[0]++;
    Usage_AnimationDebug[2]=lv_tick_elaps(transition_started);
    lv_mem_monitor_t memory;lv_mem_monitor(&memory);
    Usage_AnimationDebug[3]=(uint32_t)memory.free_size;
}
static void slide_to(uint8_t page)
{
    if(page==shown_page || transitioning)return;
    transition_from=shown_page;transition_to=page;transitioning=1;
    Usage_AnimationDebug[1]=0;
    slide_frame(NULL,0); /* Position the incoming widgets before making them visible. */
    ProviderPage *in=&pages[page];
    lv_obj_clear_flag(in->body,LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(in->title,LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(in->footer_root,LV_OBJ_FLAG_HIDDEN);
    transition_started=lv_tick_get();
    lv_anim_t animation;lv_anim_init(&animation);
    lv_anim_set_var(&animation,&transitioning);lv_anim_set_exec_cb(&animation,slide_frame);
    lv_anim_set_values(&animation,0,1024);lv_anim_set_time(&animation,360);
    lv_anim_set_path_cb(&animation,lv_anim_path_ease_out);
    lv_anim_set_ready_cb(&animation,slide_ready);lv_anim_start(&animation);
}
static MascotFace render_provider(unsigned page,const UsageSnapshot *snapshot,uint32_t now,
                                  NetState net,uint8_t valid,unsigned seconds)
{
    ProviderPage *widgets=&pages[page];
    lv_obj_t *title=widgets->title,*footer=widgets->footer,*led=widgets->led;
    lv_obj_t **values=widgets->values,**kinds=widgets->kinds,**bars=widgets->bars,**resets=widgets->resets;
    const UsageProvider *provider=&snapshot->providers[page];
    uint32_t age=now>provider->observed_at?now-provider->observed_at:0;
    uint8_t disconnected=net!=NET_ONLINE;
    uint8_t stale=valid && (age>300U || provider->status!=USAGE_OK || disconnected);
    uint16_t worst=0;uint8_t refreshing=0;
    uint32_t accent=page?0xf25745:0x63e6be;char text[64];
    lv_label_set_text(title,page?"CLAUDE":"CODEX");lv_obj_set_style_text_color(title,lv_color_hex(accent),0);
    for(unsigned k=0;k<2;k++) {
        const UsageWindow *w=&provider->windows[k];
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
        lv_label_set_text(resets[k],text);
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
    return face;
}
static void update_dashboard(TickType_t tick)
{
    UsageSnapshot snapshot;TickType_t received;uint8_t valid,time_valid;NetState net;
    taskENTER_CRITICAL();snapshot=shared_snapshot;received=shared_received;valid=shared_valid;
    net=network_state;time_valid=rtc_valid;taskEXIT_CRITICAL();
    unsigned seconds=tick/configTICK_RATE_HZ;
    uint32_t now=valid?snapshot.now+(tick-received)/configTICK_RATE_HZ:0;
    for(unsigned page=0;page<2;page++)page_faces[page]=render_provider(page,&snapshot,now,net,valid,seconds);
    slide_to((uint8_t)((seconds/8U)%2U));
    set_face(page_faces[shown_page]);lv_obj_set_x(mascot,8+(seconds/6U)%5U);
    Usage_Debug[1]=seconds;Usage_Debug[2]=shown_page;Usage_Debug[3]=(uint32_t)net;
    Usage_Debug[11]=(uint32_t)xPortGetFreeHeapSize();
    Usage_Debug[12]=(uint32_t)uxTaskGetStackHighWaterMark(NULL);
    char text[64];
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
