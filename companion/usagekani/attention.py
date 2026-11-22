"""Bounded, persistent, text-free pending requests shared by observer hooks."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import time

from .process_identity import owner_alive

MAX_REQUESTS = 64
MAX_BYTES = 65536


def _empty():
    return {'version': 1, 'requests': {}, 'closed': {}, 'sessions': {}, 'tools': {}, 'forms': {}}


def _identity(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError('Invalid attention identifier')
    return hashlib.sha256(value.encode()).hexdigest()


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**64


def _owner(value):
    return (isinstance(value, dict) and set(value) == {'pid', 'started'}
            and _integer(value['pid']) and value['pid'] < 2**32 and _integer(value['started']))


def _load(path):
    try:
        with path.open('rb') as source:
            raw = source.read(MAX_BYTES+1)
        if len(raw) > MAX_BYTES:
            return _empty()
        state = json.loads(raw)
        if (not isinstance(state, dict) or state.get('version') != 1
                or any(not isinstance(state.get(k), dict) for k in ('requests', 'closed', 'sessions'))):
            return _empty()
        result = _empty()
        for key, entry in list(state['requests'].items())[:MAX_REQUESTS]:
            if (not re.fullmatch('[0-9a-f]{64}', key) or not isinstance(entry, dict)
                    or entry.get('provider') not in ('codex', 'claude')
                    or entry.get('kind') not in ('question', 'approval')
                    or not isinstance(entry.get('session'), str)
                    or not re.fullmatch('[0-9a-f]{64}', entry['session'])
                    or not _integer(entry.get('event_ms')) or not _owner(entry.get('owner'))):
                continue
            result['requests'][key] = {k: entry[k] for k in ('provider', 'kind', 'session', 'event_ms', 'owner')}
            if entry.get('source')=='codex_record':
                if not _integer(entry.get('seen_ms')):
                    result['requests'].pop(key,None);continue
                result['requests'][key].update(source='codex_record',seen_ms=entry['seen_ms'])
        for name, limit in (('closed', 128), ('sessions', 64)):
            result[name] = {key: value for key, value in list(state[name].items())[:limit]
                            if re.fullmatch('[0-9a-f]{64}', key) and type(value) is int and _integer(abs(value))}
        for name,limit in (('tools',64),('forms',16)):
            entries=state.get(name,{})
            if not isinstance(entries,dict):continue
            for key,entry in list(entries.items())[:limit]:
                if (not re.fullmatch('[0-9a-f]{64}',key) or not isinstance(entry,dict)
                    or entry.get('provider') not in ('codex','claude') or not _owner(entry.get('owner'))
                    or not isinstance(entry.get('session'),str) or not re.fullmatch('[0-9a-f]{64}',entry['session'])
                    or not _integer(entry.get('event_ms'))):continue
                clean={k:entry[k] for k in ('provider','session','owner','event_ms')}
                if name=='tools':
                    if not isinstance(entry.get('fingerprint'),str) or not re.fullmatch('[0-9a-f]{64}',entry['fingerprint']):continue
                    clean.update(fingerprint=entry['fingerprint'],claimed=entry.get('claimed') is True)
                else:
                    if type(entry.get('balance')) is not int or abs(entry['balance'])>64:continue
                    clean['balance']=entry['balance']
                result[name][key]=clean
        return result
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return _empty()


@contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    deadline = time.monotonic()+2
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time()-lock.stat().st_mtime > 10:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError('Attention state busy')
            time.sleep(0.01)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _save(path,state):
    for name,limit in (('requests',64),('tools',64),('forms',16)):
        state[name]=dict(sorted(state[name].items(),key=lambda item:item[1]['event_ms'])[-limit:])
    for name,limit in (('closed',128),('sessions',64)):
        state[name]=dict(sorted(state[name].items(),key=lambda item:abs(item[1]))[-limit:])
    temporary=path.with_name(path.name+f'.{os.getpid()}.tmp')
    try:
        temporary.write_text(json.dumps(state,separators=(',',':')),encoding='utf-8')
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def apply_event(path, provider, session, request, *, action='raise', kind='question', owner=None,
                event_ms=None, terminal=False, source='hook', scope=None):
    path = Path(path)
    event_ms = time.time_ns()//1000000 if event_ms is None else event_ms
    if provider not in ('codex', 'claude') or action not in ('raise', 'clear', 'clear_session') or not _integer(event_ms):
        raise ValueError('Invalid attention event')
    if action == 'raise' and (kind not in ('question', 'approval') or not _owner(owner)):
        raise ValueError('Invalid attention owner or kind')
    session_key = _identity(provider+'/'+session)
    request_key = _identity(session_key+'/'+request)
    with _lock(path):
        state = _load(path)
        if action == 'raise':
            entry = state['requests'].get(request_key)
            closed=state['closed'].get(request_key,0)
            barrier = max(abs(closed), state['sessions'].get(session_key, 0),
                          state['sessions'].get(_identity(session_key+'/'+kind),0))
            if closed<0 or event_ms <= closed or event_ms < barrier or (entry and event_ms < entry['event_ms']):
                return
            state['requests'][request_key] = {'provider': provider, 'session': session_key,
                'kind': kind, 'owner': dict(owner), 'event_ms': event_ms}
            if source=='codex_record':state['requests'][request_key].update(source=source,seen_ms=event_ms)
        elif action == 'clear':
            prior=state['closed'].get(request_key,0)
            state['closed'][request_key] = -max(event_ms,abs(prior)) if terminal or prior<0 else max(event_ms,prior)
            entry = state['requests'].get(request_key)
            if entry and (terminal or entry['event_ms'] <= event_ms):
                del state['requests'][request_key]
            state['tools'].pop(request_key,None)
        else:
            barrier_key=session_key if scope is None else _identity(session_key+'/'+scope)
            state['sessions'][barrier_key] = max(event_ms, state['sessions'].get(barrier_key, 0))
            for key,entry in list(state['requests'].items())+list(state['tools'].items()):
                if (entry['session']==session_key and entry['event_ms']<=event_ms
                    and (scope is None or entry.get('kind')==scope or entry.get('claimed'))):
                    state['closed'][key]=-event_ms
            state['requests'] = {key: entry for key, entry in state['requests'].items()
                                 if entry['session'] != session_key or entry['event_ms'] > event_ms
                                 or (scope is not None and entry['kind']!=scope)}
            for name in ('tools','forms'):
                if scope is None:
                    state[name]={k:e for k,e in state[name].items() if e['session']!=session_key or e['event_ms']>event_ms}
        _save(path,state)


def register_tool(path,provider,session,use_id,fingerprint,owner,event_ms):
    if not _owner(owner):return
    path=Path(path); session_key=_identity(provider+'/'+session)
    key=_identity(session_key+'/tool:'+use_id)
    with _lock(path):
        state=_load(path)
        if state['closed'].get(key,0)<0:return
        state['tools'][key]={'provider':provider,'session':session_key,'owner':owner,
            'event_ms':event_ms,'fingerprint':fingerprint,'claimed':state['tools'].get(key,{}).get('claimed',False)}
        _save(path,state)


def claim_permission(path,provider,session,fingerprint,owner,event_ms):
    path=Path(path); session_key=_identity(provider+'/'+session)
    with _lock(path):
        state=_load(path)
        candidates=[(key,entry) for key,entry in state['tools'].items() if entry['session']==session_key
            and entry['fingerprint']==fingerprint and entry['owner']==owner and not entry['claimed']
            and state['closed'].get(key,0)>=0 and entry['event_ms']>=max(
                state['sessions'].get(session_key,0),state['sessions'].get(_identity(session_key+'/approval'),0))]
        if not candidates:return  # An anonymous callback alone cannot identify a live invocation.
        key,entry=min(candidates,key=lambda item:item[1]['event_ms'])
        entry['claimed']=True
        if key not in state['requests']:
            state['requests'][key]={k:entry[k] for k in ('provider','session','owner','event_ms')}
            state['requests'][key]['kind']='approval'
        _save(path,state)


def anonymous_form(path,provider,session,group,action,owner,event_ms):
    if not _owner(owner):return
    path=Path(path); session_key=_identity(provider+'/'+session); key=_identity(session_key+'/'+group)
    with _lock(path):
        state=_load(path); prior=state['forms'].get(key,{})
        balance=prior.get('balance',0) if prior.get('owner')==owner else 0
        state['forms'][key]={'provider':provider,'session':session_key,'owner':owner,'event_ms':event_ms,
            'balance':max(-64,min(64,balance+(1 if action=='raise' else -1)))}
        _save(path,state)


def refresh_sources(path,sessions,owner,now_ms):
    path=Path(path); keys={_identity('codex/'+s) for s in sessions}
    with _lock(path):
        state=_load(path);changed=False
        for entry in state['requests'].values():
            if (entry.get('source')=='codex_record' and entry['session'] in keys and entry['owner']==owner
                and now_ms-entry.get('seen_ms',0)>=1000):
                entry['seen_ms']=now_ms;changed=True
        if changed:_save(path,state)


def has_pending(path,provider):
    return any(e['provider']==provider for e in _load(Path(path))['requests'].values())


def pending_questions(path,provider,session,call):
    state=_load(Path(path));session_key=_identity(provider+'/'+session)
    return [i for i in range(12) if _identity(session_key+f'/question:{call}:{i}') in state['requests']]


def read_attention(path, now, *, alive=owner_alive):
    result = {'codex': None, 'claude': None}
    state = _load(Path(path))
    for provider in result:
        entries = [entry for entry in state['requests'].values() if entry['provider'] == provider
                   and entry['event_ms'] <= (now+60)*1000 and alive(entry['owner'])
                   and (entry.get('source')!='codex_record' or now*1000-entry.get('seen_ms',0)<=10000)]
        forms=[entry for entry in state['forms'].values() if entry['provider']==provider
               and entry['balance']>0 and entry['event_ms']<=(now+60)*1000 and alive(entry['owner'])]
        if entries or forms:
            result[provider] = {'kind': 'question' if forms or any(e['kind'] == 'question' for e in entries) else 'approval',
                                'count': min(64,len(entries)+sum(e['balance'] for e in forms)), 'observed_at': int(now)}
    return result
