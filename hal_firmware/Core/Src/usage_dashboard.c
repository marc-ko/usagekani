#include "main.h"
#include "app_debug.h"
#include "esp8266_common_hal.h"
#include "FreeRTOS.h"
#include "task.h"
#include "lvgl.h"
#include "usage_codex_mascot.h"
#include <stdio.h>
#include <string.h>
#include <time.h>

/* Network task owns transport. Only the UI task may call LVGL. */
typedef enum { NET_BOOT, NET_WIFI, NET_ONLINE, NET_OFFLINE, NET_CONFIG } NetState;
typedef UsageFace MascotFace;
static UsageSnapshot shared_snapshot;
static TickType_t shared_received;
static uint8_t shared_valid, rtc_valid;
static NetState network_state=NET_BOOT;
typedef struct {
    lv_obj_t *title, *footer_root, *footer, *led;
    lv_obj_t *values[2], *kinds[2], *resets[2];
    uint16_t bar_values[2];
    uint32_t bar_colors[2];
} ProviderPage;
static ProviderPage pages[2];
static lv_obj_t *mascot, *mascot_pages[2], *codex_error;
static lv_obj_t *mascot_viewport, *attention_root, *attention_provider, *attention_state;
static lv_obj_t *attention_title, *attention_reason, *attention_destination, *attention_count;
static lv_obj_t *attention_action_bg, *attention_cue, *attention_cue_label;
static uint8_t attention_active, attention_page;
static lv_obj_t *face_layer, *clock_label, *date_label, *page_indicator;
static lv_obj_t *usage_bars[2];
static uint8_t shown_page, transition_from, transition_to, transitioning;
static uint32_t transition_started;
static MascotFace page_faces[2];
static MascotFace current_face=(MascotFace)255;
/* SWD-readable diagnostics for boards without the USB serial cable attached.
 * No credentials, hostnames or account identifiers are stored here. */
volatile uint32_t Usage_Debug[16]={0x554b414eU};
/* Completed slides, callbacks, duration, LVGL free bytes, progress, visibility
 * mask and outgoing/incoming value positions at the last intermediate step. */
volatile uint32_t Usage_AnimationDebug[8];
/* Active provider (1/2, zero=none), kind, count, mask, enters, exits, page,
 * and age of the most recent successful local feed. No prompt text. */
volatile uint32_t Usage_AttentionDebug[8];
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
    lv_obj_t *viewport=box(screen,0,0,122,98,0,0);mascot_viewport=viewport;
    lv_obj_set_style_bg_opa(viewport,LV_OPA_TRANSP,0);
    for(unsigned page=0;page<2;page++) {
        mascot_pages[page]=box(viewport,0,0,122,98,0,0);
        lv_obj_set_style_bg_opa(mascot_pages[page],LV_OPA_TRANSP,0);
    }
    /* The approved white-on-dark badge is an alpha-only image in flash.
     * No decoder, image allocation or runtime scaling is needed. */
    lv_obj_t *badge=lv_img_create(mascot_pages[0]);
    lv_img_set_src(badge,&usage_codex_mascot);lv_obj_set_pos(badge,18,2);
    lv_obj_set_style_img_recolor(badge,lv_color_hex(0xffffff),0);
    lv_obj_set_style_img_recolor_opa(badge,LV_OPA_COVER,0);
    /* Cover only the terminal glyph on failure, keeping the badge silhouette. */
    codex_error=label(mascot_pages[0],41,33,44,&lv_font_montserrat_16,0x04070c);
    lv_obj_set_height(codex_error,32);lv_label_set_text(codex_error,"X.X");
    lv_obj_set_style_text_align(codex_error,LV_TEXT_ALIGN_CENTER,0);
    lv_obj_set_style_pad_top(codex_error,8,0);
    lv_obj_set_style_bg_color(codex_error,lv_color_hex(0xffffff),0);
    lv_obj_set_style_bg_opa(codex_error,LV_OPA_COVER,0);
    lv_obj_add_flag(codex_error,LV_OBJ_FLAG_HIDDEN);
    /* Original Clawd pixel unit, silhouette, palette and facial geometry. */
    mascot=box(mascot_pages[1],10,8,108,84,0,0);lv_obj_set_style_bg_opa(mascot,LV_OPA_TRANSP,0);
    pixel(mascot,3,0,12,4,0xf25745);pixel(mascot,0,4,18,4,0xf25745);pixel(mascot,3,8,12,2,0xf25745);
    const int legs[]={3,6,10,13};
    for(unsigned k=0;k<4;k++)pixel(mascot,legs[k],10,2,4,0xf25745);
    face_layer=box(mascot,0,0,108,48,0,0);lv_obj_set_style_bg_opa(face_layer,LV_OPA_TRANSP,0);
    set_face(FACE_HAPPY);
    lv_anim_t anim;lv_anim_init(&anim);lv_anim_set_var(&anim,mascot);
    lv_anim_set_exec_cb(&anim,(lv_anim_exec_xcb_t)lv_obj_set_y);
    lv_anim_set_values(&anim,8,4);lv_anim_set_time(&anim,850);lv_anim_set_playback_time(&anim,850);
    lv_anim_set_repeat_count(&anim,LV_ANIM_REPEAT_INFINITE);lv_anim_set_path_cb(&anim,lv_anim_path_ease_in_out);lv_anim_start(&anim);
    lv_obj_add_flag(mascot_pages[1],LV_OBJ_FLAG_HIDDEN);
}
static void update_mascot_states(void)
{
    set_face(attention_active && attention_page==1?FACE_HAPPY:page_faces[1]);
    if(page_faces[0]==FACE_XX && !(attention_active && attention_page==0))lv_obj_clear_flag(codex_error,LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(codex_error,LV_OBJ_FLAG_HIDDEN);
}
static lv_obj_t *clip(lv_obj_t *parent,int x,int y,int w,int h)
{
    lv_obj_t *obj=box(parent,x,y,w,h,0,0);
    lv_obj_set_style_bg_opa(obj,LV_OPA_TRANSP,0);
    return obj;
}
static void page_hidden(ProviderPage *page,uint8_t hidden)
{
    lv_obj_t *objects[]={page->title,page->footer_root,page->values[0],page->values[1],
                         page->kinds[0],page->kinds[1],page->resets[0],page->resets[1]};
    for(unsigned k=0;k<sizeof(objects)/sizeof(objects[0]);k++) {
        if(hidden)lv_obj_add_flag(objects[k],LV_OBJ_FLAG_HIDDEN);
        else lv_obj_clear_flag(objects[k],LV_OBJ_FLAG_HIDDEN);
    }
}
static void create_attention(lv_obj_t *screen)
{
    attention_root=box(screen,0,0,240,240,0x04070c,0);
    attention_provider=label(attention_root,14,14,116,&lv_font_montserrat_12,0x63e6be);
    lv_obj_t *state_bg=box(attention_root,151,12,77,21,0x282012,6);
    attention_state=label(state_bg,2,3,73,&lv_font_montserrat_12,0xf4c874);
    lv_obj_set_style_text_align(attention_state,LV_TEXT_ALIGN_CENTER,0);
    attention_cue=box(attention_root,159,41,25,25,0x63e6be,6);
    attention_cue_label=label(attention_cue,0,3,25,&lv_font_montserrat_16,0x04070c);
    lv_obj_set_style_text_align(attention_cue_label,LV_TEXT_ALIGN_CENTER,0);
    attention_title=label(attention_root,8,143,224,&lv_font_montserrat_24,0xedf3f9);
    attention_reason=label(attention_root,8,175,224,&lv_font_montserrat_12,0xaebccc);
    attention_action_bg=box(attention_root,29,198,182,24,0x112a27,6);
    attention_destination=label(attention_action_bg,0,5,182,&lv_font_montserrat_12,0x63e6be);
    attention_count=label(attention_root,8,225,224,&lv_font_montserrat_12,0x8e9bab);
    lv_obj_t *centered[]={attention_title,attention_reason,attention_destination,attention_count};
    for(unsigned i=0;i<sizeof(centered)/sizeof(centered[0]);i++)
        lv_obj_set_style_text_align(centered[i],LV_TEXT_ALIGN_CENTER,0);
    lv_obj_add_flag(attention_root,LV_OBJ_FLAG_HIDDEN);
}
static void create_dashboard(void)
{
    lv_obj_t *screen=lv_scr_act();lv_obj_clean(screen);lv_obj_clear_flag(screen,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(screen,lv_color_hex(0x04070c),0);lv_obj_set_style_bg_opa(screen,LV_OPA_COVER,0);
    create_mascot(screen);
    lv_obj_t *heading_clip=clip(screen,126,0,114,36);
    clock_label=label(screen,128,40,106,&lv_font_montserrat_24,0xe6edf5);
    date_label=label(screen,128,74,110,&lv_font_montserrat_12,0x8c9aab);
    box(screen,10,98,220,1,0x1c2938,0);
    lv_obj_t *body_clip=clip(screen,0,101,240,119);
    lv_obj_t *footer_clip=clip(screen,38,221,194,19);
    for(unsigned page=0;page<2;page++) {
        ProviderPage *w=&pages[page];
        w->title=label(heading_clip,2,14,106,&lv_font_montserrat_16,page?0xf25745:0x63e6be);
        w->footer_root=clip(footer_clip,0,0,194,19);
        w->led=box(w->footer_root,0,8,4,4,0x63e6be,2);
        w->footer=label(w->footer_root,8,1,184,&lv_font_montserrat_12,0x8997a9);
    }
    for(unsigned k=0;k<2;k++) {
        lv_obj_t *card=box(body_clip,8,3+k*60,224,54,0x101925,8);
        lv_obj_t *value_clip=clip(card,10,2,100,29);
        lv_obj_t *kind_clip=clip(card,112,8,102,16);
        lv_obj_t *reset_clip=clip(card,10,39,208,15);
        usage_bars[k]=lv_bar_create(card);lv_obj_remove_style_all(usage_bars[k]);
        lv_obj_set_pos(usage_bars[k],10,32);lv_obj_set_size(usage_bars[k],204,5);lv_bar_set_range(usage_bars[k],0,1000);
        lv_obj_set_style_bg_color(usage_bars[k],lv_color_hex(0x263348),LV_PART_MAIN);
        lv_obj_set_style_bg_opa(usage_bars[k],LV_OPA_COVER,LV_PART_MAIN);
        lv_obj_set_style_bg_opa(usage_bars[k],LV_OPA_COVER,LV_PART_INDICATOR);
        lv_obj_set_style_radius(usage_bars[k],2,LV_PART_MAIN);lv_obj_set_style_radius(usage_bars[k],2,LV_PART_INDICATOR);
        for(unsigned page=0;page<2;page++) {
            pages[page].values[k]=label(value_clip,0,0,100,&lv_font_montserrat_24,0xe6edf5);
            pages[page].kinds[k]=label(kind_clip,0,0,102,&lv_font_montserrat_12,0xaab8c8);
            lv_obj_set_style_text_align(pages[page].kinds[k],LV_TEXT_ALIGN_RIGHT,0);
            pages[page].resets[k]=label(reset_clip,0,0,208,&lv_font_montserrat_12,0xaab8c8);
        }
    }
    page_hidden(&pages[1],1);
    box(screen,11,228,5,5,0x334258,3);box(screen,21,228,5,5,0x334258,3);
    page_indicator=box(screen,11,228,7,5,0x63e6be,3);
    create_attention(screen);
}

/* Both providers stay visible throughout a real push transition. Shared card
 * backgrounds remain stationary; only their clipped text and bars redraw. */
static void slide_frame(void *unused,int32_t progress)
{
    (void)unused;
    int direction=transition_to>transition_from?1:-1;
    ProviderPage *in=&pages[transition_to],*out=&pages[transition_from];
    lv_obj_set_x(mascot_pages[transition_from],-direction*122*progress/1024);
    lv_obj_set_x(mascot_pages[transition_to],direction*122*(1024-progress)/1024);
    lv_obj_set_x(out->title,2-direction*114*progress/1024);
    lv_obj_set_x(in->title,2+direction*114*(1024-progress)/1024);
    lv_obj_set_x(out->footer_root,-direction*194*progress/1024);
    lv_obj_set_x(in->footer_root,direction*194*(1024-progress)/1024);
    for(unsigned k=0;k<2;k++) {
        lv_obj_set_x(out->values[k],-direction*100*progress/1024);
        lv_obj_set_x(in->values[k],direction*100*(1024-progress)/1024);
        lv_obj_set_x(out->kinds[k],-direction*102*progress/1024);
        lv_obj_set_x(in->kinds[k],direction*102*(1024-progress)/1024);
        lv_obj_set_x(out->resets[k],-direction*208*progress/1024);
        lv_obj_set_x(in->resets[k],direction*208*(1024-progress)/1024);
        int32_t from=out->bar_values[k],to=in->bar_values[k];
        lv_bar_set_value(usage_bars[k],(int16_t)(from+(to-from)*progress/1024),LV_ANIM_OFF);
        lv_obj_set_style_bg_color(usage_bars[k],lv_color_mix(lv_color_hex(in->bar_colors[k]),
            lv_color_hex(out->bar_colors[k]),(uint8_t)(progress*255/1024)),LV_PART_INDICATOR);
    }
    lv_obj_set_x(page_indicator,11+10*transition_from+direction*10*progress/1024);
    lv_color_t from=lv_color_hex(transition_from?0xf25745:0x63e6be);
    lv_color_t to=lv_color_hex(transition_to?0xf25745:0x63e6be);
    lv_obj_set_style_bg_color(page_indicator,lv_color_mix(to,from,(uint8_t)(progress*255/1024)),0);
    if(progress>0 && progress<1024) {
        Usage_AnimationDebug[4]=(uint32_t)progress;
        Usage_AnimationDebug[5]=(!lv_obj_has_flag(out->values[0],LV_OBJ_FLAG_HIDDEN)&&
            !lv_obj_has_flag(out->title,LV_OBJ_FLAG_HIDDEN)?1U:0U) |
            (!lv_obj_has_flag(in->values[0],LV_OBJ_FLAG_HIDDEN)&&
            !lv_obj_has_flag(in->title,LV_OBJ_FLAG_HIDDEN)?2U:0U);
        Usage_AnimationDebug[6]=(uint32_t)(-direction*100*progress/1024);
        Usage_AnimationDebug[7]=(uint32_t)(direction*100*(1024-progress)/1024);
    }
    Usage_AnimationDebug[1]++;
}
static void slide_ready(lv_anim_t *animation)
{
    (void)animation;
    ProviderPage *out=&pages[transition_from];
    page_hidden(out,1);
    lv_obj_add_flag(mascot_pages[transition_from],LV_OBJ_FLAG_HIDDEN);
    shown_page=transition_to;transitioning=0;
    update_mascot_states();
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
    page_hidden(&pages[page],0);
    lv_obj_clear_flag(mascot_pages[page],LV_OBJ_FLAG_HIDDEN);
    transition_started=lv_tick_get();
    lv_anim_t animation;lv_anim_init(&animation);
    lv_anim_set_var(&animation,&transitioning);lv_anim_set_exec_cb(&animation,slide_frame);
    lv_anim_set_values(&animation,0,1024);lv_anim_set_time(&animation,420);
    lv_anim_set_path_cb(&animation,lv_anim_path_ease_in_out);
    lv_anim_set_ready_cb(&animation,slide_ready);lv_anim_start(&animation);
}
static void update_attention(const UsageSnapshot *snapshot,uint32_t now,NetState net,
                             uint8_t valid,uint32_t feed_age,unsigned seconds)
{
    uint8_t mask=Usage_AttentionMask(snapshot,now,valid && net==NET_ONLINE && feed_age<=10U);
    Usage_AttentionDebug[3]=mask;Usage_AttentionDebug[7]=feed_age;
    if(!mask) {
        if(attention_active) {
            attention_active=0;Usage_AttentionDebug[5]++;
            lv_obj_set_parent(mascot_viewport,lv_scr_act());lv_obj_set_pos(mascot_viewport,0,0);
            for(unsigned p=0;p<2;p++) {
                lv_obj_set_x(mascot_pages[p],0);
                if(p==shown_page)lv_obj_clear_flag(mascot_pages[p],LV_OBJ_FLAG_HIDDEN);
                else lv_obj_add_flag(mascot_pages[p],LV_OBJ_FLAG_HIDDEN);
            }
            lv_obj_add_flag(attention_root,LV_OBJ_FLAG_HIDDEN);
        }
        Usage_AttentionDebug[0]=0;Usage_AttentionDebug[1]=0;Usage_AttentionDebug[2]=0;
        return;
    }
    if(transitioning) {
        lv_anim_del(&transitioning,slide_frame);slide_frame(NULL,1024);slide_ready(NULL);
    }
    if(!attention_active) {
        attention_active=1;Usage_AttentionDebug[4]++;
        lv_obj_set_parent(mascot_viewport,attention_root);lv_obj_set_pos(mascot_viewport,60,32);
        lv_obj_move_foreground(attention_cue);
        lv_obj_clear_flag(attention_root,LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(attention_root);
    }
    attention_page=mask==3?(uint8_t)((seconds/4U)%2U):(mask==1?0:1);
    for(unsigned p=0;p<2;p++) {
        lv_obj_set_x(mascot_pages[p],0);
        if(p==attention_page)lv_obj_clear_flag(mascot_pages[p],LV_OBJ_FLAG_HIDDEN);
        else lv_obj_add_flag(mascot_pages[p],LV_OBJ_FLAG_HIDDEN);
    }
    const UsageAttention *a=&snapshot->providers[attention_page].attention;
    uint8_t approval=a->kind==ATTENTION_APPROVAL;
    uint32_t accent=attention_page?0xf25745:0x63e6be;
    lv_label_set_text(attention_provider,attention_page?"CLAUDE":"CODEX");
    lv_obj_set_style_text_color(attention_provider,lv_color_hex(accent),0);
    lv_label_set_text(attention_state,approval?"APPROVAL":"WAITING");
    lv_label_set_text(attention_cue_label,approval?"!":"?");
    lv_obj_set_style_bg_color(attention_cue,lv_color_hex(accent),0);
    lv_label_set_text(attention_title,approval?"Your approval?":"I need you.");
    lv_label_set_text(attention_reason,approval?"Permission to continue":"Waiting for your answer");
    lv_label_set_text(attention_destination,attention_page?
        (approval?"Review in Claude":"Reply in Claude"):(approval?"Review in Codex":"Reply in Codex"));
    lv_obj_set_style_text_color(attention_destination,lv_color_hex(accent),0);
    lv_obj_set_style_bg_color(attention_action_bg,lv_color_hex(attention_page?0x2b1919:0x112a27),0);
    char text[32];snprintf(text,sizeof(text),"%u request%s waiting",a->count,a->count==1?"":"s");
    lv_label_set_text(attention_count,text);
    Usage_AttentionDebug[0]=attention_page+1;Usage_AttentionDebug[1]=a->kind;
    Usage_AttentionDebug[2]=a->count;Usage_AttentionDebug[6]=attention_page;
}
static MascotFace render_provider(unsigned page,const UsageSnapshot *snapshot,uint32_t now,
                                  NetState net,uint8_t valid,unsigned seconds)
{
    ProviderPage *widgets=&pages[page];
    lv_obj_t *title=widgets->title,*footer=widgets->footer,*led=widgets->led;
    lv_obj_t **values=widgets->values,**kinds=widgets->kinds,**resets=widgets->resets;
    const UsageProvider *provider=&snapshot->providers[page];
    uint32_t age=now>provider->observed_at?now-provider->observed_at:0;
    uint8_t disconnected=net!=NET_ONLINE;
    uint8_t stale=valid && (age>300U || provider->status!=USAGE_OK || disconnected);
    uint16_t worst=0;unsigned worst_window=0;uint8_t refreshing=0;
    uint32_t accent=page?0xf25745:0x63e6be;char text[64];
    lv_label_set_text(title,page?"CLAUDE":"CODEX");lv_obj_set_style_text_color(title,lv_color_hex(accent),0);
    for(unsigned k=0;k<2;k++) {
        const UsageWindow *w=&provider->windows[k];
        uint8_t expired=w->valid && w->resets_at && !Usage_WindowActive(w,now);
        if(expired)refreshing=1;
        if(Usage_WindowActive(w,now) && w->used_tenths>worst){worst=w->used_tenths;worst_window=k;}
        if(w->valid)snprintf(text,sizeof(text),"%u%%",(unsigned)((w->used_tenths+5U)/10U));else snprintf(text,sizeof(text),"--");
        lv_label_set_text(values[k],text);lv_obj_set_style_text_color(values[k],lv_color_hex(stale||expired?0x7f8c9e:0xe6edf5),0);
        if(!w->valid)snprintf(text,sizeof(text),k?"Weekly used":"5h used");
        else if(w->duration_mins==10080U)snprintf(text,sizeof(text),"Weekly used");
        else if(w->duration_mins%60U==0)snprintf(text,sizeof(text),"%luh used",(unsigned long)(w->duration_mins/60U));
        else snprintf(text,sizeof(text),"%lum used",(unsigned long)w->duration_mins);
        lv_label_set_text(kinds[k],text);
        widgets->bar_values[k]=w->valid?w->used_tenths:0;
        widgets->bar_colors[k]=stale||expired?0x526176:(w->used_tenths>=800?0xf2b65b:accent);
        if(!w->valid)snprintf(text,sizeof(text),"Waiting for report");
        else if(disconnected || provider->status==USAGE_ERROR || provider->status==USAGE_AUTH)
            snprintf(text,sizeof(text),"Last known - %lum ago",(unsigned long)(age/60U));
        else if(!w->resets_at)snprintf(text,sizeof(text),"No active window");
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
    else if(worst>=800) {
        uint32_t minutes=provider->windows[worst_window].duration_mins;
        const char *warning=worst>=1000?"limit reached":"near limit";
        if(minutes==10080U)snprintf(text,sizeof(text),"Weekly %s",warning);
        else if(minutes%60U==0)snprintf(text,sizeof(text),"%luh %s",(unsigned long)(minutes/60U),warning);
        else snprintf(text,sizeof(text),"%lum %s",(unsigned long)minutes,warning);
    }
    else if(refreshing)snprintf(text,sizeof(text),"Refreshing usage");
    else if(age<60)snprintf(text,sizeof(text),"%s %lus ago",page?"Reported":"Updated",(unsigned long)age);
    else snprintf(text,sizeof(text),"%s %lum ago",page?"Reported":"Updated",(unsigned long)(age/60));
    lv_label_set_text(footer,text);lv_obj_set_style_bg_color(led,lv_color_hex(stale||disconnected?0xf2b65b:0x63e6be),0);
    MascotFace face=Usage_GetFace(provider,now,net==NET_ONLINE,net==NET_BOOT||net==NET_WIFI);
    if(face==FACE_HAPPY && seconds%7U==0)face=FACE_BLINK;
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
    update_attention(&snapshot,now,net,valid,(tick-received)/configTICK_RATE_HZ,seconds);
    if(!attention_active)slide_to((uint8_t)((seconds/8U)%2U));
    if(!attention_active && !transitioning)for(unsigned k=0;k<2;k++) {
        lv_bar_set_value(usage_bars[k],pages[shown_page].bar_values[k],LV_ANIM_OFF);
        lv_obj_set_style_bg_color(usage_bars[k],lv_color_hex(pages[shown_page].bar_colors[k]),LV_PART_INDICATOR);
    }
    update_mascot_states();lv_obj_set_x(mascot,8+(seconds/6U)%5U);
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
        lv_timer_handler();
        /* A blocking flush already consumes most of an animation frame.
         * Avoid adding a further 10 ms gap while the short reveal is active. */
        vTaskDelay(pdMS_TO_TICKS(transitioning?1:10));
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
        /* Fast local activity delivery; external quota collectors keep their
         * independent 30/60-second schedules on the PC. */
        vTaskDelay(pdMS_TO_TICKS(error?5000:2000));
    }
}
void Usage_CreateTasks(void)
{
    if(xTaskCreate(ui_task,"usage-ui",1536,NULL,3,NULL)!=pdPASS ||
       xTaskCreate(network_task,"usage-net",2560,NULL,2,NULL)!=pdPASS)Error_Handler();
}
