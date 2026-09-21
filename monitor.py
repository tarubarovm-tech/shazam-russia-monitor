import csv, io, json, os, re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

TOKEN=os.environ["BOT_TOKEN"].strip()
CHAT_ID=os.environ["CHAT_ID"].strip()
STATE=Path("chart_state.json")
TZ=ZoneInfo("Europe/Moscow")
H={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
   "Accept-Language":"ru-RU,ru;q=0.9,en;q=0.8"}
SHAZAM="https://www.shazam.com/services/charts/csv/top-200/russia/"
APPLE="https://music.apple.com/ru/playlist/shazam-charts-russia/pl.b96cdf2da806490ea383b8a0cb45790d"

def send(text):
    while text:
        cut=min(3900,len(text))
        if cut<len(text):
            p=text.rfind("\n",0,cut)
            if p>2000: cut=p
        r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id":CHAT_ID,"text":text[:cut],"disable_web_page_preview":"true"},timeout=30)
        r.raise_for_status()
        text=text[cut:].lstrip()

def get(url):
    r=requests.get(url,headers=H,timeout=35); r.raise_for_status(); return r

def shazam():
    text=get(SHAZAM).content.decode("utf-8-sig",errors="replace")
    lines=text.splitlines()
    i=next((i for i,x in enumerate(lines) if x.lstrip("\ufeff").strip().lower().startswith("rank,artist,title")),None)
    if i is None: raise RuntimeError("не найден заголовок Rank,Artist,Title")
    out=[]
    for row in csv.DictReader(io.StringIO("\n".join(lines[i:]))):
        low={str(k).strip().lower():(v or "").strip() for k,v in row.items() if k}
        if low.get("title"): out.append({"title":low["title"],"artist":low.get("artist","")})
    if len(out)<150: raise RuntimeError(f"получено только {len(out)} треков")
    return out[:200]

def walk(x,out):
    if isinstance(x,dict):
        if x.get("@type") in ("MusicRecording","Song"):
            n=x.get("name"); a=x.get("byArtist") or x.get("author")
            if isinstance(a,dict): a=a.get("name","")
            elif isinstance(a,list): a=", ".join((z.get("name","") if isinstance(z,dict) else str(z)) for z in a)
            if n: out.append({"title":str(n).strip(),"artist":str(a or "").strip()})
        for v in x.values(): walk(v,out)
    elif isinstance(x,list):
        for v in x: walk(v,out)

def apple():
    html=get(APPLE).text; out=[]
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',html,re.I|re.S):
        try: walk(json.loads(raw),out)
        except Exception: pass
    seen=set(); uniq=[]
    for x in out:
        k=(x["title"],x["artist"])
        if k not in seen: seen.add(k); uniq.append(x)
    if len(uniq)<100: raise RuntimeError(f"получено только {len(uniq)} треков")
    return uniq[:200]

def key(x): return x["title"]+(f" — {x['artist']}" if x["artist"] else "")

def report(name,old,new,now):
    op={key(x):i+1 for i,x in enumerate(old)}; np={key(x):i+1 for i,x in enumerate(new)}
    add=sorted((p,s) for s,p in np.items() if s not in op)
    gone=sorted((p,s) for s,p in op.items() if s not in np)
    move=sorted(((abs(op[s]-p),p,op[s],s) for s,p in np.items() if s in op and op[s]!=p),reverse=True)
    z=[f"🔄 {name}",f"Обнаружено: {now}",f"Новых: {len(add)} | Ушло: {len(gone)} | Сменили позицию: {len(move)}"]
    if add: z+=["","🆕 НОВЫЕ:"]+[f"#{p} {s}" for p,s in add[:30]]
    if gone: z+=["","❌ УШЛИ:"]+[f"было #{p} {s}" for p,s in gone[:20]]
    if move: z+=["","📈 ИЗМЕНЕНИЯ ПОЗИЦИЙ:"]+[f"{'↑' if p<q else '↓'} #{p} {s} (было #{q})" for _,p,q,s in move[:25]]
    return "\n".join(z)

def main():
    try: state=json.loads(STATE.read_text("utf-8"))
    except: state={}
    now=datetime.now(TZ).strftime("%d.%m.%Y %H:%M МСК")
    sources={"Shazam Top 200 Russia":shazam,"Apple Music — Shazam Charts Russia":apple}
    changed=False
    for name,fn in sources.items():
        try:
            cur=fn(); old=state.get(name)
            if old and old!=cur: send(report(name,old,cur,now))
            elif not old: send(f"📌 {name}: GitHub-монитор сохранил исходное состояние — {len(cur)}/200, {now}.")
            if old!=cur: state[name]=cur; changed=True
        except Exception as e:
            send(f"⚠️ {name}: ошибка проверки, {now}\n{e}")
    state["_last_check"]=now
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),"utf-8")

if __name__=="__main__": main()
