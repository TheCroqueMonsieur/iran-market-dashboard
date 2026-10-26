#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt, html, json, math, re, statistics, sys, time, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'market.json'
UA='Mozilla/5.0 (IranEquityMonitor/1.0)'
TEPIX_CODE='32097828799138957'

def get(url, timeout=9):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return r.read().decode('utf-8','replace')

def get_json(url,timeout=9): return json.loads(get(url,timeout))
def n(x):
    if x is None:return None
    s=re.sub(r'<[^>]+>','',html.unescape(str(x))).replace(',','').strip().replace('−','-')
    try:
        v=float(s);return v if math.isfinite(v) else None
    except:return None

def iso(x):
    s=str(x).strip().replace('-','/')[:10]
    try:return dt.datetime.strptime(s,'%Y/%m/%d').date().isoformat()
    except:return None

def tgju(slug):
    errors=[]
    for host in ('https://api.tgju.org/v1','https://api.accessban.com/v1'):
        url=f'{host}/market/indicator/summary-table-data/{slug}'
        try:
            p=get_json(url,9); rows=p.get('data',[]) if isinstance(p,dict) else []
            out={}
            for r in rows:
                if isinstance(r,list) and len(r)>=7:
                    d=iso(r[6]);v=n(r[3])
                    if d and v and v>0:out[d]=v
            if len(out)>=100:return out
            errors.append(f'{host}: {len(out)} rows')
        except Exception as e: errors.append(f'{host}: {type(e).__name__}')
    raise RuntimeError(f'TGJU {slug} failed: '+', '.join(errors))

def tsetmc():
    urls=(
      f'https://members.tsetmc.com/tsev2/chart/data/IndexFinancial.aspx?i={TEPIX_CODE}&t=ph',
      f'http://old.tsetmc.com/tsev2/chart/data/IndexFinancial.aspx?i={TEPIX_CODE}&t=ph')
    errs=[]
    for url in urls:
        try:
            text=get(url,8);out={}
            for row in text.strip().split(';'):
                c=[z.strip() for z in row.split(',')]
                if len(c)>=7:
                    d=iso(c[0]);v=n(c[-1])
                    if d and v and v>100:out[d]=v
            if len(out)>=500:return out,url
            errs.append(f'{url}: {len(out)} rows')
        except Exception as e:errs.append(f'{url}: {type(e).__name__}')
    raise RuntimeError('TSETMC unavailable: '+' | '.join(errs))

def bonbast():
    p=get_json('https://raw.githubusercontent.com/SamadiPour/rial-exchange-rates-archive/data/gregorian_imp.min.json',20)
    out={}
    for k,v in p.items():
        d=iso(k);u=(v.get('usd') or v.get('USD')) if isinstance(v,dict) else None
        if not d or not isinstance(u,dict):continue
        vals=[z for z in (n(u.get('buy')),n(u.get('sell'))) if z and z>0]
        if vals:out[d]=statistics.mean(vals)*10 # toman -> rial
    return out

def validate(a,b):
    common=sorted(set(a)&set(b))[-60:]
    dif=[abs(a[d]/b[d]-1) for d in common if a[d]>0 and b[d]>0]
    return len(dif)>=10 and statistics.median(dif)<.01,(statistics.median(dif) if dif else None),len(dif)

def validate_publication(records, previous=None):
    if sum(bool(r['fx'].get('tgju_free')) for r in records)<500:
        raise RuntimeError('Fewer than 500 default-FX observations; refusing publication')
    dates=[r['date'] for r in records]
    if dates!=sorted(set(dates)):
        raise RuntimeError('Duplicate or unsorted index dates')
    for r in records:
        if not math.isfinite(r['tepix']) or r['tepix']<=0:
            raise RuntimeError('Invalid index value')
        if dt.date.fromisoformat(r['date'])>dt.datetime.now(dt.timezone.utc).date():
            raise RuntimeError('Future index date')
        for k,v in r['fx'].items():
            age=r.get('fx_age_days',{}).get(k)
            if not math.isfinite(v) or v<=0 or type(age) is not int or not 0<=age<=4:
                raise RuntimeError('Invalid FX value or age')
    if previous:
        old=previous.get('records',[])
        if old and (dates[-1]<old[-1]['date'] or not set(r['date'] for r in old).issubset(dates)):
            raise RuntimeError('Index history regressed; refusing publication')

def main():
    notes=[]
    print('TGJU TEDPIX...',file=sys.stderr); tg=tgju('gc30')
    try:
        print('TSETMC TEDPIX...',file=sys.stderr); off,offurl=tsetmc();ok,med,cnt=validate(off,tg)
        if ok:
            idx=off; idxsrc='Official TSETMC IndexFinancial';notes.append(f'TSETMC validated vs TGJU: median abs diff {med:.4%}, n={cnt}.')
        else:
            idx=tg;idxsrc='TGJU gc30 (TSETMC validation failed)';notes.append(f'TSETMC failed TGJU validation: median diff {med}, n={cnt}.')
    except Exception as e:
        idx=tg;idxsrc='TGJU gc30 (TSETMC network fallback)';notes.append(str(e))
    print('TGJU free USD/IRR...',file=sys.stderr); fx=tgju('price_dollar_rl')
    try:
        print('Bonbast cross-check...',file=sys.stderr);bb=bonbast()
    except Exception as e:bb={};notes.append('Bonbast unavailable: '+str(e))

    series={'tgju_free':fx}
    if len(bb)>=100:series['bonbast_mid']=bb
    sortedfx={k:sorted(v.items()) for k,v in series.items()}; ptr={k:0 for k in series};last={k:None for k in series}
    recs=[]
    for d,iv in sorted(idx.items()):
        dd=dt.date.fromisoformat(d)
        if dd<dt.date(2012,10,9):continue
        rec={'date':d,'tepix':round(iv,4),'fx':{},'fx_age_days':{}}
        for k,items in sortedfx.items():
            p=ptr[k]
            while p<len(items) and items[p][0]<=d:last[k]=items[p];p+=1
            ptr[k]=p
            if last[k]:
                fd,val=last[k];age=(dd-dt.date.fromisoformat(fd)).days
                if 0<=age<=4:rec['fx'][k]=round(val,4);rec['fx_age_days'][k]=age
        recs.append(rec) # Preserve index sessions even when FX is unavailable.
    previous=json.loads(OUT.read_text()) if OUT.exists() else None
    validate_publication(recs,previous)
    diagnostics={k:{'latest_observation':max(v),'latest_aligned_session':next((r['date'] for r in reversed(recs) if k in r['fx']),None)} for k,v in series.items()}
    for k,info in diagnostics.items():
        if info['latest_aligned_session']!=recs[-1]['date']:
            notes.append(f"Coverage warning: {k} ends {info['latest_aligned_session']}; index ends {recs[-1]['date']}.")
    age=(dt.datetime.now(dt.timezone.utc).date()-dt.date.fromisoformat(recs[-1]['date'])).days
    if age>4:notes.append(f'Index freshness warning: latest observation is {age} calendar days old; verify holidays or source interruption.')
    unusual=[r['date'] for r in recs if dt.date.fromisoformat(r['date']).weekday() in (3,4)]
    if unusual:notes.append(f'Calendar warning: {len(unusual)} Thursday/Friday index dates require source-calendar verification; retained without assuming they are trading sessions.')
    p={'schema_version':1,'updated_at':dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
       'methodology':{'index':'TEDPIX / TEPIX close','default_fx':'TGJU free-market USD/IRR daily close, IRR per USD','usd_return_formula':'(TEPIX_t / TEPIX_t-1) * (USDIRR_t-1 / USDIRR_t) - 1','usd_index_formula':'100 * (TEPIX_t / USDIRR_t) / (TEPIX_base / USDIRR_base)','fx_alignment':'Most recent FX observation on or before each TSE session; maximum 4 calendar days; age disclosed'},
       'sources':{'index':idxsrc,'index_validation':'TGJU gc30','tgju_free':'TGJU price_dollar_rl — Iranian free-market USD/IRR','bonbast_mid':'Bonbast archive — midpoint buy/sell, toman ×10 to IRR' if bb else None,'regulated':None},'notes':notes,'diagnostics':diagnostics,'records':recs}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(p,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print(f'Published {len(recs)} sessions through {recs[-1]["date"]}',file=sys.stderr)
if __name__=='__main__':main()
