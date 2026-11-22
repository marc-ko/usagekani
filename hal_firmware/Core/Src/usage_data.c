/* Bounded, allocation-free JSON/HTTP decoder shared with native tests. */
#include "usage_data.h"
#include <ctype.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

int Usage_WindowActive(const UsageWindow *window, uint32_t now)
{
    return window->valid && window->resets_at>now;
}
UsageFace Usage_GetFace(const UsageProvider *provider,uint32_t now,int network_ok,int connecting)
{
    if(!network_ok)return connecting?FACE_SLEEPY:FACE_XX;
    if(provider->status==USAGE_AUTH || provider->status==USAGE_ERROR)return FACE_XX;
    if(provider->status==USAGE_WAITING || provider->status==USAGE_STALE ||
       !provider->observed_at || (now>provider->observed_at && now-provider->observed_at>300U))return FACE_SLEEPY;
    uint16_t worst=0;
    for(unsigned k=0;k<2;k++)
        if(Usage_WindowActive(&provider->windows[k],now) && provider->windows[k].used_tenths>worst)
            worst=provider->windows[k].used_tenths;
    if(worst>=1000)return FACE_SLEEPY;
    return worst>=800?FACE_CONCERNED:FACE_HAPPY;
}
uint8_t Usage_AttentionMask(const UsageSnapshot *snapshot,uint32_t now,int network_ok)
{
    uint8_t mask=0;
    if(!network_ok)return 0;
    for(unsigned p=0;p<2;p++) {
        const UsageAttention *a=&snapshot->providers[p].attention;
        if(a->kind && a->count && a->observed_at &&
           (a->observed_at>=now || now-a->observed_at<=10U))mask|=1U<<p;
    }
    return mask;
}
void UsageRx_Push(UsageRxBuffer *ring, char byte)
{
    uint16_t next=(uint16_t)((ring->head+1U)&4095U);
    if(next==ring->tail){ring->overflow=1;return;}
    ring->data[ring->head]=byte;
    ring->head=next;
}
int UsageRx_Read(UsageRxBuffer *ring, char *out, size_t capacity)
{
    size_t count=0;
    if(ring->overflow)return -1;
    while(count<capacity && ring->tail!=ring->head) {
        out[count++]=ring->data[ring->tail];
        ring->tail=(uint16_t)((ring->tail+1U)&4095U);
    }
    return (int)count;
}

void Usage_FormatCountdown(uint32_t seconds, char *out, size_t capacity)
{
    if(!seconds)snprintf(out,capacity,"Refreshing");
    else if(seconds>=86400U)snprintf(out,capacity,"Resets in %lud %luh",(unsigned long)(seconds/86400U),(unsigned long)(seconds%86400U/3600U));
    else if(seconds>=3600U)snprintf(out,capacity,"Resets in %luh %lum",(unsigned long)(seconds/3600U),(unsigned long)(seconds%3600U/60U));
    else snprintf(out,capacity,"Resets in %lum",(unsigned long)((seconds+59U)/60U));
}

typedef struct { size_t start, end; int next, type; } Token;
typedef struct { const char *s; size_t n, p; Token t[128]; int count; } Json;
static void space(Json *j) { while (j->p < j->n && strchr(" \r\n\t", j->s[j->p])) j->p++; }
static int value(Json *j, unsigned depth)
{
    int index, type;
    size_t begin;
    char end;
    space(j);
    if (depth > 8 || j->p >= j->n || j->count >= 128) return -1;
    index = j->count++;
    type = (unsigned char)j->s[j->p];
    j->t[index].type = type;
    j->t[index].start = j->p;
    if (type == '{' || type == '[') {
        end = type == '{' ? '}' : ']';
        j->p++;
        space(j);
        if (j->p < j->n && j->s[j->p] != end) {
            for (;;) {
                if (type == '{') {
                    space(j);
                    if (j->p >= j->n || j->s[j->p] != '"' || value(j, depth+1) < 0) return -1;
                    space(j);
                    if (j->p >= j->n || j->s[j->p++] != ':') return -1;
                }
                if (value(j, depth+1) < 0) return -1;
                space(j);
                if (j->p >= j->n) return -1;
                if (j->s[j->p] != ',') break;
                j->p++;
            }
        }
        if (j->p >= j->n || j->s[j->p++] != end) return -1;
    } else if (type == '"') {
        j->p++;
        j->t[index].start = j->p;
        while (j->p < j->n && j->s[j->p] != '"') {
            unsigned char c = (unsigned char)j->s[j->p++];
            if (c < 32) return -1;
            if (c == '\\') {
                if (j->p >= j->n) return -1;
                c = (unsigned char)j->s[j->p++];
                if (c == 'u') {
                    for (int k=0; k<4; k++) if (j->p >= j->n || !isxdigit((unsigned char)j->s[j->p++])) return -1;
                } else if (!strchr("\"\\/bfnrt", c)) return -1;
            }
        }
        if (j->p >= j->n) return -1;
        j->t[index].end = j->p++;
        j->t[index].next = j->count;
        return index;
    } else {
        begin = j->p;
        while (j->p < j->n && !strchr(" \r\n\t,]}", j->s[j->p])) j->p++;
        size_t len = j->p - begin;
        if (!len || len > 31) return -1;
        if (!((len==4 && !memcmp(j->s+begin,"null",4)) ||
              (len==4 && !memcmp(j->s+begin,"true",4)) ||
              (len==5 && !memcmp(j->s+begin,"false",5)))) {
            /* JSON number grammar: no NaN, hex, leading zeros or unary plus. */
            size_t p=begin;
            if (j->s[p]=='-') p++;
            if (p >= j->p || !isdigit((unsigned char)j->s[p])) return -1;
            if (j->s[p++] != '0') while (p<j->p && isdigit((unsigned char)j->s[p])) p++;
            if (p<j->p && j->s[p]=='.') {
                p++; size_t first=p;
                while(p<j->p && isdigit((unsigned char)j->s[p])) p++;
                if (p==first) return -1;
            }
            if (p<j->p && (j->s[p]=='e' || j->s[p]=='E')) {
                p++; if(p<j->p && (j->s[p]=='+' || j->s[p]=='-')) p++;
                size_t first=p;
                while(p<j->p && isdigit((unsigned char)j->s[p])) p++;
                if(p==first) return -1;
            }
            if(p!=j->p) return -1;
        }
    }
    j->t[index].end=j->p;
    j->t[index].next=j->count;
    return index;
}
static int equal(const Json *j, int i, const char *text)
{
    return i>=0 && j->t[i].end-j->t[i].start==strlen(text) &&
           !memcmp(j->s+j->t[i].start,text,strlen(text));
}
static int field(const Json *j, int object, const char *key)
{
    int found=-1;
    if(object<0 || j->t[object].type!='{') return -1;
    for(int i=object+1;i<j->t[object].next;) {
        int v=i+1;
        if(equal(j,i,key)) { if(found>=0) return -2; found=v; }
        i=j->t[v].next;
    }
    return found;
}
static int number(const Json *j,int i,double max,double *out)
{
    char text[32], *end;
    if(i<0 || (j->t[i].type!='-' && !isdigit(j->t[i].type))) return -1;
    size_t len=j->t[i].end-j->t[i].start;
    if(len>=sizeof(text)) return -1;
    memcpy(text,j->s+j->t[i].start,len);text[len]=0;
    double result=strtod(text,&end);
    if(*end || !isfinite(result) || result<0 || result>max) return -1;
    *out=result;return 0;
}
static int integer(const Json *j,int i,uint32_t *out)
{
    double result;
    if(number(j,i,4102444800.0,&result) || result!=(uint32_t)result) return -1;
    *out=(uint32_t)result;return 0;
}
static int provider(const Json *j,int i,UsageProvider *out)
{
    const char *names[]={"waiting","ok","stale","auth","error"};
    int status=field(j,i,"status"), array=field(j,i,"windows"), known=0;
    for(int k=0;k<5;k++) if(status>=0 && j->t[status].type=='"' && equal(j,status,names[k])) {out->status=(UsageStatus)k;known=1;}
    if(!known || integer(j,field(j,i,"observed_at"),&out->observed_at) || array<0 || j->t[array].type!='[') return -1;
    int attention=field(j,i,"attention");
    if(attention==-2)return -1;
    if(attention>=0 && !(j->t[attention].type=='n' && equal(j,attention,"null"))) {
        uint32_t count;
        int kind=field(j,attention,"kind");
        if(kind<0 || j->t[kind].type!='"' ||
           integer(j,field(j,attention,"count"),&count) || !count || count>64 ||
           integer(j,field(j,attention,"observed_at"),&out->attention.observed_at) ||
           !out->attention.observed_at)return -1;
        if(equal(j,kind,"question"))out->attention.kind=ATTENTION_QUESTION;
        else if(equal(j,kind,"approval"))out->attention.kind=ATTENTION_APPROVAL;
        else return -1;
        out->attention.count=(uint8_t)count;
    }
    int w=array+1;
    for(int k=0;k<2;k++) {
        double used;
        if(w>=j->t[array].next) return -1;
        if(!(j->t[w].type=='n' && equal(j,w,"null"))) {
            if(number(j,field(j,w,"used_pct"),100,&used) ||
               integer(j,field(j,w,"duration_mins"),&out->windows[k].duration_mins) ||
               !out->windows[k].duration_mins || out->windows[k].duration_mins>525600 ||
               integer(j,field(j,w,"resets_at"),&out->windows[k].resets_at)) return -1;
            out->windows[k].valid=1;
            out->windows[k].used_tenths=(uint16_t)(used*10+0.5);
            if(!out->windows[k].resets_at && used!=0.0)return -1;
        }
        w=j->t[w].next;
    }
    return w==j->t[array].next ? 0 : -1;
}
int Usage_Parse(const char *json,size_t length,UsageSnapshot *out)
{
    Json j={0};
    UsageSnapshot result={0};
    uint32_t version;
    if(!json || !out || !length || length>2048) return -1;
    j.s=json;j.n=length;
    if(value(&j,0)!=0) return -1;
    space(&j);
    if(j.p!=length || integer(&j,field(&j,0,"version"),&version) || version!=1 ||
       integer(&j,field(&j,0,"now"),&result.now) || result.now<1577836800U ||
       provider(&j,field(&j,0,"codex"),&result.providers[0]) ||
       provider(&j,field(&j,0,"claude"),&result.providers[1])) return -1;
    for(int k=0;k<2;k++) if(result.providers[k].observed_at>result.now+60U ||
        result.providers[k].attention.observed_at>result.now+60U)return -1;
    *out=result;return 0;
}

static const char *find(const char *p,size_t n,const char *needle)
{
    size_t len=strlen(needle);
    for(size_t k=0;k+len<=n;k++) if(!memcmp(p+k,needle,len)) return p+k;
    return NULL;
}
static int header_equal(const char *p,const char *key,size_t n)
{
    if(n!=strlen(key)) return 0;
    for(size_t k=0;k<n;k++) if(tolower((unsigned char)p[k])!=tolower((unsigned char)key[k])) return 0;
    return 1;
}
int Usage_ExtractHttp(const char *raw,size_t length,char *body,size_t capacity)
{
    char http[3072];
    size_t p=0,used=0;
    if(!raw || !body || !capacity || length>4095) return -1;
    while(p<length) {
        const char *frame=find(raw+p,length-p,"+IPD,");
        if(!frame) break;
        p=(size_t)(frame-raw)+5;
        size_t count=0,first=p;
        while(p<length && isdigit((unsigned char)raw[p])) {
            count=count*10+(unsigned)(raw[p++]-'0');
            if(count>sizeof(http)) return -1;
        }
        if(p==first || p>=length || raw[p++]!=':' || count>length-p || count>sizeof(http)-used) return -1;
        memcpy(http+used,raw+p,count);used+=count;p+=count;
    }
    if(used<16 || (memcmp(http,"HTTP/1.0 200 ",13) && memcmp(http,"HTTP/1.1 200 ",13))) return -1;
    const char *separator=find(http,used,"\r\n\r\n");
    if(!separator) return -1;
    size_t head=(size_t)(separator-http)+4, content=0;
    int seen=0;
    const char *line=find(http,head,"\r\n");
    if(!line) return -1;
    line+=2;
    while(line<separator) {
        const char *end=find(line,(size_t)(separator-line)+2,"\r\n");
        const char *colon=end ? memchr(line,':',(size_t)(end-line)) : NULL;
        if(!colon) return -1;
        if(header_equal(line,"Transfer-Encoding",(size_t)(colon-line))) return -1;
        if(header_equal(line,"Content-Length",(size_t)(colon-line))) {
            if(seen++) return -1;
            const char *v=colon+1;
            while(v<end && *v==' ')v++;
            if(v==end || !isdigit((unsigned char)*v))return -1;
            for(;v<end;v++) {
                if(!isdigit((unsigned char)*v))return -1;
                content=content*10+(unsigned)(*v-'0');
                if(content>2048)return -1;
            }
        }
        line=end+2;
    }
    if(!seen || !content || content>=capacity || used!=head+content)return -1;
    memcpy(body,http+head,content);body[content]=0;
    return 0;
}
