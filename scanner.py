"""
FOREX SCANNER — Strategie Day Trading Complete
D1 + H4 + H1 + M15 | London + NY | News Filter
Support/Resistance | Fibonacci | Niveaux Psychologiques
Structure HH/HL/LH/LL | Score 75+ | R/R 1:1/1:2/1:3
"""
import pandas as pd
import numpy as np
import requests
import time
import logging
import json
import threading
from datetime import datetime, timezone, timedelta
from config import CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scanner.log", encoding="utf-8")
    ]
)
log = logging.getLogger("SCANNER")

TF_MAP = {"M15":"15min","H1":"1h","H4":"4h","D1":"1day"}

# ─── TWELVE DATA ──────────────────────────────────────────────────────────────
_req=0; _t=time.time()

def td(endpoint, params):
    global _req, _t
    key = CONFIG.get("twelvedata_key","")
    now = time.time()
    if now-_t >= 60: _req=0; _t=now
    if _req >= 7:
        wait = 62-(now-_t)
        log.info(f"  Rate limit — attente {wait:.0f}s...")
        time.sleep(max(wait,1))
        _req=0; _t=time.time()
    try:
        params["apikey"] = key
        r = requests.get(f"https://api.twelvedata.com/{endpoint}", params=params, timeout=20)
        _req += 1
        data = r.json()
        if isinstance(data,dict) and data.get("status")=="error":
            log.debug(f"TD: {data.get('message')}")
            return None
        return data
    except Exception as e:
        log.debug(f"TD: {e}")
        return None

def fetch(pair, tf):
    data = td("time_series",{"symbol":pair,"interval":TF_MAP[tf],"outputsize":200,"format":"JSON"})
    if not data or "values" not in data: return None
    try:
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        for c in ["open","high","low","close"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        else:
            df["volume"] = 0.0
        df = df[["open","high","low","close","volume"]].dropna(subset=["open","high","low","close"])
        return df if len(df)>=50 else None
    except Exception as e:
        log.debug(f"fetch {pair} {tf}: {e}")
        return None

# ─── INDICATEURS ──────────────────────────────────────────────────────────────
def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def sma(s,n): return s.rolling(n).mean()

def rsi_calc(s,n=14):
    d=s.diff()
    g=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean()
    l=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    return 100-100/(1+g/l.replace(0,np.nan))

def atr_calc(df,n=14):
    tr=pd.concat([df["high"]-df["low"],
                  (df["high"]-df["close"].shift()).abs(),
                  (df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean()

def macd_calc(s,fast=12,slow=26,sig=9):
    ml=ema(s,fast)-ema(s,slow); sl=ema(ml,sig)
    return ml,sl,ml-sl

def stoch_calc(df,k=14,d=3):
    lo=df["low"].rolling(k).min(); hi=df["high"].rolling(k).max()
    kl=100*(df["close"]-lo)/(hi-lo+1e-10)
    return kl,kl.rolling(d).mean()

def pip_mult(pair):
    return 100 if "JPY" in pair else 10000

# ─── STRUCTURE HH/HL/LH/LL ───────────────────────────────────────────────────
def market_structure(df, lookback=50):
    if len(df)<lookback: return "NEUTRAL", []
    recent=df.tail(lookback)
    highs=[]; lows=[]
    for i in range(3,len(recent)-3):
        if all(recent["high"].iloc[i]>recent["high"].iloc[i-j] for j in range(1,4)) and \
           all(recent["high"].iloc[i]>recent["high"].iloc[i+j] for j in range(1,4)):
            highs.append(recent["high"].iloc[i])
        if all(recent["low"].iloc[i]<recent["low"].iloc[i-j] for j in range(1,4)) and \
           all(recent["low"].iloc[i]<recent["low"].iloc[i+j] for j in range(1,4)):
            lows.append(recent["low"].iloc[i])
    struct="NEUTRAL"
    if len(highs)>=2 and len(lows)>=2:
        if highs[-1]>highs[-2] and lows[-1]>lows[-2]: struct="BULLISH"
        elif highs[-1]<highs[-2] and lows[-1]<lows[-2]: struct="BEARISH"
    swing=[]+lows[-3:]+highs[-3:]
    return struct, sorted(swing)

# ─── SUPPORT ET RÉSISTANCE ────────────────────────────────────────────────────
def find_sr(df, lookback=100, tol=0.001):
    if len(df)<lookback: return []
    recent=df.tail(lookback); levels=[]
    for i in range(2,len(recent)-2):
        if recent["high"].iloc[i]>=recent["high"].iloc[i-1] and recent["high"].iloc[i]>=recent["high"].iloc[i+1]:
            levels.append(recent["high"].iloc[i])
        if recent["low"].iloc[i]<=recent["low"].iloc[i-1] and recent["low"].iloc[i]<=recent["low"].iloc[i+1]:
            levels.append(recent["low"].iloc[i])
    levels=sorted(levels); merged=[]
    for l in levels:
        if not merged or abs(l-merged[-1])/max(merged[-1],0.0001)>tol:
            merged.append(l)
        else:
            merged[-1]=(merged[-1]+l)/2
    confirmed=[]
    for level in merged:
        touches=sum(1 for l in levels if abs(l-level)/max(level,0.0001)<tol)
        if touches>=2: confirmed.append(round(level,5))
    return confirmed

# ─── NIVEAUX PSYCHOLOGIQUES ───────────────────────────────────────────────────
def psycho_levels(price, pair):
    levels=[]
    if "JPY" in pair:
        base=round(price)
        for i in range(-3,4): levels.append(base+i)
        for i in range(-3,4): levels.append(base+i+0.5)
    else:
        base=round(price,2)
        for i in range(-5,6): levels.append(round(base+i*0.01,4))
    close=[l for l in levels if abs(l-price)/max(price,0.0001)<0.02]
    return sorted(set([round(l,3 if "JPY" in pair else 5) for l in close]))

# ─── FIBONACCI ────────────────────────────────────────────────────────────────
def fibonacci(df, lookback=50):
    if len(df)<lookback: return {}
    r=df.tail(lookback)
    hi=r["high"].max(); lo=r["low"].min(); rng=hi-lo
    return {"0.236":round(hi-rng*0.236,5),"0.382":round(hi-rng*0.382,5),
            "0.500":round(hi-rng*0.500,5),"0.618":round(hi-rng*0.618,5),
            "0.786":round(hi-rng*0.786,5),"high":round(hi,5),"low":round(lo,5)}

# ─── SESSION ──────────────────────────────────────────────────────────────────
def get_session():
    h=datetime.now(timezone.utc).hour
    if 8<=h<12:  return "London", True
    if 12<=h<16: return "London/NY Overlap", True
    if 16<=h<17: return "New York", True
    return "Hors session", False

# ─── NEWS FILTER ──────────────────────────────────────────────────────────────
def fetch_news():
    try:
        key=CONFIG.get("twelvedata_key","")
        now=datetime.now()
        r=requests.get("https://api.twelvedata.com/economic_calendar",
            params={"apikey":key,"start_date":now.strftime("%Y-%m-%d"),
                    "end_date":(now+timedelta(days=1)).strftime("%Y-%m-%d")},timeout=10)
        data=r.json()
        if "result" in data and "list" in data["result"]:
            return data["result"]["list"]
    except: pass
    return []

def news_blocked(pair, events):
    currencies=pair.split("/")
    now_utc=datetime.now(timezone.utc)
    for ev in events:
        if ev.get("country","").upper() not in currencies: continue
        if str(ev.get("importance","")).lower() != "high": continue
        try:
            ev_time=datetime.fromisoformat(ev["date"].replace("Z","+00:00"))
            diff=(ev_time-now_utc).total_seconds()/60
            if -30<=diff<=30:
                return True, f"News {ev.get('country','')} dans {diff:.0f}min"
        except: continue
    return False, ""

# ─── ANALYSE D1 ───────────────────────────────────────────────────────────────
def analyze_d1(df, pair):
    if df is None: return None
    price=df["close"].iloc[-1]
    rsi_v=rsi_calc(df["close"]).iloc[-1]
    e20=ema(df["close"],20).iloc[-1]; e50=ema(df["close"],50).iloc[-1]
    e200=ema(df["close"],200).iloc[-1] if len(df)>=200 else None
    _,_,hist=macd_calc(df["close"])
    struct,swings=market_structure(df)
    sr=find_sr(df); fibs=fibonacci(df); psycho=psycho_levels(price,pair)

    score_bull=0; score_bear=0
    if struct=="BULLISH": score_bull+=30
    elif struct=="BEARISH": score_bear+=30
    if e20>e50: score_bull+=15
    else: score_bear+=15
    if e200:
        if price>e200: score_bull+=15
        else: score_bear+=15
    if rsi_v<50: score_bull+=10
    else: score_bear+=10
    if not hist.isna().iloc[-1]:
        if hist.iloc[-1]>0: score_bull+=10
        else: score_bear+=10

    direction="BUY" if score_bull>=score_bear else "SELL"
    confidence=max(score_bull,score_bear)

    nearest_sr=min(sr,key=lambda x:abs(x-price)) if sr else None
    nearest_psycho=min(psycho,key=lambda x:abs(x-price)) if psycho else None
    fib_vals={k:v for k,v in fibs.items() if k not in ["high","low"]}
    nearest_fib=None; fib_name=None
    if fib_vals:
        fib_name=min(fib_vals,key=lambda k:abs(fib_vals[k]-price))
        nearest_fib=fib_vals[fib_name]

    dec=3 if "JPY" in pair else 5
    return {"direction":direction,"confidence":confidence,"structure":struct,
            "rsi":round(rsi_v,1),"e20":round(e20,dec),"e50":round(e50,dec),
            "e200":round(e200,dec) if e200 else None,"sr":sr[:5],
            "nearest_sr":nearest_sr,"nearest_psycho":nearest_psycho,
            "psycho":psycho[:6],"fibs":fibs,"nearest_fib":nearest_fib,
            "fib_name":fib_name,"price":round(price,dec)}

# ─── ANALYSE H4 ───────────────────────────────────────────────────────────────
def analyze_h4(df, direction):
    if df is None: return None
    price=df["close"].iloc[-1]
    rsi_v=rsi_calc(df["close"]).iloc[-1]
    e20=ema(df["close"],20).iloc[-1]; e50=ema(df["close"],50).iloc[-1]
    _,_,hist=macd_calc(df["close"])
    fibs=fibonacci(df); struct,_=market_structure(df,lookback=60)
    vol_avg=df["volume"].rolling(20).mean().iloc[-1]
    score=0; confirms=[]

    if struct==("BULLISH" if direction=="BUY" else "BEARISH"):
        score+=25; confirms.append(f"Structure H4 {struct}")
    if direction=="BUY" and e20>e50: score+=20; confirms.append("EMA20>50 H4")
    if direction=="SELL" and e20<e50: score+=20; confirms.append("EMA20<50 H4")
    if min(e20,e50)<=price<=max(e20,e50): score+=15; confirms.append("Zone de valeur EMA H4")
    if not hist.isna().iloc[-1]:
        if direction=="BUY" and hist.iloc[-1]>0: score+=15; confirms.append("MACD H4 positif")
        if direction=="SELL" and hist.iloc[-1]<0: score+=15; confirms.append("MACD H4 negatif")
    fib_key={"0.382","0.500","0.618"}
    for k in fib_key:
        if k in fibs and abs(price-fibs[k])/max(price,0.001)<0.002:
            score+=20; confirms.append(f"Fib {k} H4 ({fibs[k]})"); break
    if direction=="BUY" and rsi_v<50: score+=10; confirms.append(f"RSI H4 {rsi_v:.0f}")
    if direction=="SELL" and rsi_v>50: score+=10; confirms.append(f"RSI H4 {rsi_v:.0f}")
    if not np.isnan(vol_avg) and vol_avg>0 and df["volume"].iloc[-1]>vol_avg*1.2:
        score+=10; confirms.append("Volume H4 eleve")

    return {"score":min(100,score),"confirms":confirms,"structure":struct,
            "rsi":round(rsi_v,1),"aligned":score>=40}

# ─── ANALYSE H1 ───────────────────────────────────────────────────────────────
def analyze_h1(df, direction, pair):
    if df is None: return None
    c=df.iloc[-1]; p=df.iloc[-2]
    price=c["close"]; rng=c["high"]-c["low"]
    rsi_v=rsi_calc(df["close"]).iloc[-1]
    atr_v=atr_calc(df).iloc[-1]
    k_s,d_s=stoch_calc(df); _,_,hist=macd_calc(df["close"])
    e20=ema(df["close"],20).iloc[-1]; sr=find_sr(df,lookback=80)
    setups=[]

    # Pin Bar
    if rng>1e-7:
        body=abs(c["close"]-c["open"])
        upper=c["high"]-max(c["open"],c["close"])
        lower=min(c["open"],c["close"])-c["low"]
        if lower/rng>0.60 and body/rng<0.35 and direction=="BUY":
            setups.append({"name":"Pin Bar Haussier","strength":80,"detail":f"Meche basse {round(lower/rng*100)}%"})
        if upper/rng>0.60 and body/rng<0.35 and direction=="SELL":
            setups.append({"name":"Pin Bar Baissier","strength":80,"detail":f"Meche haute {round(upper/rng*100)}%"})

    # Engulfing
    cb=abs(c["close"]-c["open"]); pb=abs(p["close"]-p["open"])
    if pb>1e-7:
        r=round(cb/pb,1)
        if p["close"]<p["open"] and c["close"]>c["open"] and c["open"]<=p["close"] and c["close"]>=p["open"] and cb>pb and direction=="BUY":
            setups.append({"name":"Engulfing Haussier","strength":85,"detail":f"Corps x{r}"})
        if p["close"]>p["open"] and c["close"]<c["open"] and c["open"]>=p["close"] and c["close"]<=p["open"] and cb>pb and direction=="SELL":
            setups.append({"name":"Engulfing Baissier","strength":85,"detail":f"Corps x{r}"})

    # Inside Bar
    if c["high"]<p["high"] and c["low"]>p["low"]:
        setups.append({"name":"Inside Bar","strength":70,"detail":"Compression"})

    # MACD Cross
    if not hist.isna().iloc[-2]:
        if hist.iloc[-2]<0 and hist.iloc[-1]>0 and direction=="BUY":
            setups.append({"name":"MACD Cross Haussier","strength":75,"detail":"Croisement H1"})
        if hist.iloc[-2]>0 and hist.iloc[-1]<0 and direction=="SELL":
            setups.append({"name":"MACD Cross Baissier","strength":75,"detail":"Croisement H1"})

    # Stoch Cross
    if not k_s.isna().iloc[-2]:
        if k_s.iloc[-2]<d_s.iloc[-2] and k_s.iloc[-1]>d_s.iloc[-1] and k_s.iloc[-1]<35 and direction=="BUY":
            setups.append({"name":"Stoch Cross Haussier","strength":72,"detail":f"K={k_s.iloc[-1]:.1f}"})
        if k_s.iloc[-2]>d_s.iloc[-2] and k_s.iloc[-1]<d_s.iloc[-1] and k_s.iloc[-1]>65 and direction=="SELL":
            setups.append({"name":"Stoch Cross Baissier","strength":72,"detail":f"K={k_s.iloc[-1]:.1f}"})

    # RSI Divergence
    rs=rsi_calc(df["close"]).values; cl=df["close"].values
    peaks=[i for i in range(2,len(cl)-2) if cl[i]>cl[i-1] and cl[i]>cl[i+1]]
    troughs=[i for i in range(2,len(cl)-2) if cl[i]<cl[i-1] and cl[i]<cl[i+1]]
    if len(peaks)>=2 and direction=="SELL":
        i1,i2=peaks[-2],peaks[-1]
        if cl[i2]>cl[i1] and rs[i2]<rs[i1]:
            setups.append({"name":"Divergence RSI Baissiere","strength":80,"detail":"Prix monte RSI baisse"})
    if len(troughs)>=2 and direction=="BUY":
        i1,i2=troughs[-2],troughs[-1]
        if cl[i2]<cl[i1] and rs[i2]>rs[i1]:
            setups.append({"name":"Divergence RSI Haussiere","strength":80,"detail":"Prix baisse RSI monte"})

    # Breakout S/R
    if sr:
        res_levels=[l for l in sr if l<price*1.005]
        sup_levels=[l for l in sr if l>price*0.995]
        if res_levels:
            res=max(res_levels)
            if c["close"]>res and c["close"]>c["open"] and direction=="BUY":
                setups.append({"name":"Breakout Resistance","strength":78,"detail":f"Cassure {res:.5f}"})
        if sup_levels:
            sup=min(sup_levels)
            if c["close"]<sup and c["close"]<c["open"] and direction=="SELL":
                setups.append({"name":"Breakout Support","strength":78,"detail":f"Cassure {sup:.5f}"})

    if not setups: return None
    best=max(setups,key=lambda x:x["strength"])
    score=best["strength"]; confirms=[best["detail"]]
    if direction=="BUY" and rsi_v<50: score+=10; confirms.append(f"RSI H1 {rsi_v:.0f}")
    if direction=="SELL" and rsi_v>50: score+=10; confirms.append(f"RSI H1 {rsi_v:.0f}")

    return {"setup":best["name"],"detail":best["detail"],"score":min(100,score),
            "confirms":confirms,"rsi":round(rsi_v,1),"atr":round(atr_v,5),
            "sr":sr[:4],"all_setups":[s["name"] for s in setups]}

# ─── ANALYSE M15 ──────────────────────────────────────────────────────────────
def analyze_m15(df, direction):
    if df is None: return {"aligned":False,"score":0,"confirms":[],"rsi":50}
    c=df.iloc[-1]
    rsi_v=rsi_calc(df["close"]).iloc[-1]
    k_s,d_s=stoch_calc(df)
    e20=ema(df["close"],20).iloc[-1]; e50=ema(df["close"],50).iloc[-1]
    score=0; confirms=[]

    if direction=="BUY" and e20>e50: score+=25; confirms.append("EMA M15 haussiere")
    if direction=="SELL" and e20<e50: score+=25; confirms.append("EMA M15 baissiere")
    if direction=="BUY" and rsi_v<55: score+=20; confirms.append(f"RSI M15 {rsi_v:.0f}")
    if direction=="SELL" and rsi_v>45: score+=20; confirms.append(f"RSI M15 {rsi_v:.0f}")
    if not k_s.isna().iloc[-1]:
        if direction=="BUY" and k_s.iloc[-1]<60: score+=20; confirms.append(f"Stoch M15 {k_s.iloc[-1]:.0f}")
        if direction=="SELL" and k_s.iloc[-1]>40: score+=20; confirms.append(f"Stoch M15 {k_s.iloc[-1]:.0f}")
    if direction=="BUY" and c["close"]>c["open"]: score+=15; confirms.append("Bougie M15 haussiere")
    if direction=="SELL" and c["close"]<c["open"]: score+=15; confirms.append("Bougie M15 baissiere")

    return {"aligned":score>=50,"score":min(100,score),"confirms":confirms,"rsi":round(rsi_v,1)}

# ─── NIVEAUX SL/TP ────────────────────────────────────────────────────────────
def calc_levels(direction, price, atr_v, d1, pair):
    dec=3 if "JPY" in pair else 5
    pm=pip_mult(pair)

    if direction=="BUY":
        sl=round(price-atr_v*1.5,dec)
        sl_dist=price-sl
        tp1=round(price+sl_dist*1.0,dec)
        tp2=round(price+sl_dist*2.0,dec)
        tp3=round(price+sl_dist*3.0,dec)
        # Ajuster TP1 sur prochain S/R ou psycho
        targets=[]
        if d1.get("sr"): targets+=[l for l in d1["sr"] if l>price+atr_v*0.3]
        if d1.get("psycho"): targets+=[l for l in d1["psycho"] if l>price+atr_v*0.3]
        if targets:
            near=min(targets)
            if near<tp2: tp1=round(near,dec)
    else:
        sl=round(price+atr_v*1.5,dec)
        sl_dist=sl-price
        tp1=round(price-sl_dist*1.0,dec)
        tp2=round(price-sl_dist*2.0,dec)
        tp3=round(price-sl_dist*3.0,dec)
        targets=[]
        if d1.get("sr"): targets+=[l for l in d1["sr"] if l<price-atr_v*0.3]
        if d1.get("psycho"): targets+=[l for l in d1["psycho"] if l<price-atr_v*0.3]
        if targets:
            near=max(targets)
            if near>tp2: tp1=round(near,dec)

    rr1=round(abs(tp1-price)/max(sl_dist,1e-7),2)
    rr2=round(abs(tp2-price)/max(sl_dist,1e-7),2)
    rr3=round(abs(tp3-price)/max(sl_dist,1e-7),2)
    sl_pips=round(sl_dist*pm,1)
    return {"sl":sl,"tp1":tp1,"tp2":tp2,"tp3":tp3,"rr1":rr1,"rr2":rr2,"rr3":rr3,"sl_pips":sl_pips}

# ─── ANALYSE COMPLETE PAR PAIRE ───────────────────────────────────────────────
def analyze_pair(pair, events):
    min_score=CONFIG["min_confluence_score"]

    # Session check
    session,active=get_session()
    if not active: return None

    # Fetch
    df_d1=fetch(pair,"D1"); df_h4=fetch(pair,"H4")
    df_h1=fetch(pair,"H1"); df_m15=fetch(pair,"M15")
    if df_d1 is None: return None

    # D1
    d1=analyze_d1(df_d1,pair)
    if not d1: return None
    direction=d1["direction"]
    log.info(f"    D1:{direction} struct:{d1['structure']} conf:{d1['confidence']}")

    # H4
    h4=analyze_h4(df_h4,direction) if df_h4 is not None else None
    h4_ok=h4 and h4["aligned"]

    # H1
    h1=analyze_h1(df_h1,direction,pair) if df_h1 is not None else None
    if not h1:
        log.info(f"    H1: aucun setup — skip")
        return None

    # M15
    m15=analyze_m15(df_m15,direction) if df_m15 is not None else {"aligned":False,"score":0,"confirms":[],"rsi":50}

    # Compter TF alignés
    tf_count=1  # D1
    if h4_ok: tf_count+=1
    tf_count+=1  # H1 avec setup
    if m15["aligned"]: tf_count+=1

    if tf_count<3:
        log.info(f"    {tf_count}/4 TF — insuffisant")
        return None

    # News
    blocked,reason=news_blocked(pair,events)
    if blocked:
        log.info(f"    News: {reason}")
        return None

    # Score global
    d1_s=d1["confidence"]
    h4_s=h4["score"] if h4 else 0
    h1_s=h1["score"]
    m15_s=m15["score"]
    total=min(100,round(d1_s*0.35+h4_s*0.30+h1_s*0.25+m15_s*0.10))

    if total<min_score:
        log.info(f"    Score {total}<{min_score} — filtre")
        return None

    # Niveaux
    price=df_h1["close"].iloc[-1]
    atr_v=atr_calc(df_h1).iloc[-1]
    levels=calc_levels(direction,price,atr_v,d1,pair)
    dec=3 if "JPY" in pair else 5

    # Résumé MTF
    tf_lines=[]
    tf_lines.append(f"✅ D1: {d1['structure']} | RSI {d1['rsi']}")
    tf_lines.append(f"{'✅' if h4_ok else '⚠️'} H4: {'Confirme' if h4_ok else 'Faible'}" + (f" | {h4['confirms'][0]}" if h4 and h4['confirms'] else ""))
    tf_lines.append(f"✅ H1: {h1['setup']} — {h1['detail']}")
    tf_lines.append(f"{'✅' if m15['aligned'] else '⚠️'} M15: {'Aligne' if m15['aligned'] else 'Attendre'} | RSI {m15['rsi']}")

    # Niveaux clés
    key_levels=[]
    if d1.get("nearest_psycho"):
        dist=round(abs(price-d1["nearest_psycho"])*pip_mult(pair),1)
        key_levels.append(f"Psycho: {d1['nearest_psycho']} ({dist} pips)")
    if d1.get("nearest_sr"):
        dist=round(abs(price-d1["nearest_sr"])*pip_mult(pair),1)
        key_levels.append(f"S/R majeur: {d1['nearest_sr']} ({dist} pips)")
    if d1.get("nearest_fib"):
        key_levels.append(f"Fib {d1['fib_name']}: {d1['nearest_fib']}")

    # Confluences
    confluences=[]
    confluences.append(f"Structure D1: {d1['structure']}")
    if d1["e200"]:
        ab="au-dessus" if price>d1["e200"] else "en-dessous"
        confluences.append(f"Prix {ab} EMA200 D1")
    if h4 and h4["confirms"]: confluences+=h4["confirms"][:2]
    if h1 and h1["confirms"]: confluences+=h1["confirms"][:2]

    stars="⭐"*(1 if total<75 else 2 if total<85 else 3)
    priority="🔥 PRIORITAIRE" if tf_count==4 else "📡 Signal"

    return {
        "pair":pair,"direction":direction,"session":session,
        "tf_count":tf_count,"stars":stars,"priority":priority,
        "score":total,"d1_score":d1_s,"h4_score":h4_s,"h1_score":h1_s,"m15_score":m15_s,
        "setup":h1["setup"],"setup_detail":h1["detail"],
        "tf_summary":"\n".join(tf_lines),"confluences":confluences[:5],
        "key_levels":key_levels[:3],
        "entry":round(price,dec),"sl":levels["sl"],
        "tp1":levels["tp1"],"tp2":levels["tp2"],"tp3":levels["tp3"],
        "rr1":levels["rr1"],"rr2":levels["rr2"],"rr3":levels["rr3"],
        "sl_pips":levels["sl_pips"],
        "rsi_d1":d1["rsi"],"rsi_h1":h1["rsi"],"structure":d1["structure"],
        "time":datetime.now().strftime("%H:%M:%S"),
        "timestamp":datetime.now().isoformat(),
    }

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
def send_telegram(sig):
    token=CONFIG.get("telegram_token",""); chat_id=CONFIG.get("telegram_chat_id","")
    if not token or token=="TON_TOKEN_ICI": return
    emoji="🟢" if sig["direction"]=="BUY" else "🔴"
    facts="\n".join(f"✓ {f}" for f in sig["confluences"]) or "—"
    kl="\n".join(f"📍 {l}" for l in sig["key_levels"]) or "—"

    msg=f"""╔══ {sig['stars']} {sig['direction']} · {sig['pair']} {sig['stars']} ══╗

{emoji} *{sig['pair']}* · {sig['session']}
{sig['priority']} · {sig['tf_count']}/4 TF alignes

📐 *Setup H1:* {sig['setup']}
_{sig['setup_detail']}_

📊 *ANALYSE MULTI-TIMEFRAME*
{sig['tf_summary']}

📍 *NIVEAUX CLES*
{kl}

💹 *TRADING LEVELS*
┌ 🎯 Entree:    `{sig['entry']}`
├ 🛑 Stop Loss: `{sig['sl']}` ({sig['sl_pips']} pips)
├ ✅ TP1 (R:{sig['rr1']}): `{sig['tp1']}`
├ ✅ TP2 (R:{sig['rr2']}): `{sig['tp2']}`
└ ✅ TP3 (R:{sig['rr3']}): `{sig['tp3']}`

📈 *INDICATEURS*
• RSI D1: `{sig['rsi_d1']}` · RSI H1: `{sig['rsi_h1']}`
• Structure: `{sig['structure']}`

🎯 *Score: {sig['score']}/100*
D1:{sig['d1_score']} · H4:{sig['h4_score']} · H1:{sig['h1_score']} · M15:{sig['m15_score']}

🔍 *Confluences:*
{facts}

⏰ {sig['time']}
╚══════════════════════════╝"""

    try:
        r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id":chat_id,"text":msg,"parse_mode":"Markdown",
                  "disable_web_page_preview":True},timeout=10)
        if r.status_code==200:
            log.info(f"  ✅ Telegram → {sig['pair']} {sig['direction']} {sig['score']}/100")
        else:
            log.warning(f"  Telegram {r.status_code}: {r.text[:80]}")
    except Exception as e:
        log.warning(f"  Telegram: {e}")

# ─── KEYBOARD ─────────────────────────────────────────────────────────────────
_force=threading.Event(); _quit=threading.Event()

def kb():
    while not _quit.is_set():
        try:
            line=input()
            if line.strip().lower()=="q": _quit.set(); _force.set()
            else: log.info("Scan force!"); _force.set()
        except: break

# ─── SCAN ─────────────────────────────────────────────────────────────────────
def do_scan(seen, n):
    session,active=get_session()
    print(f"\n{'='*60}")
    log.info(f"SCAN #{n} — {datetime.now().strftime('%d/%m %H:%M:%S')} — {session}")
    print(f"{'='*60}")

    if not active:
        log.info(f"  Hors session — signaux actifs 9h-17h heure Paris")
        return seen

    log.info("  Recuperation news...")
    events=fetch_news()
    log.info(f"  → {len(events)} evenements")

    t0=time.time(); new=0

    for i,pair in enumerate(CONFIG["pairs"]):
        log.info(f"  [{i+1}/{len(CONFIG['pairs'])}] {pair}...")
        sig=analyze_pair(pair,events)
        if sig is None: continue

        key=f"{pair}|{sig['direction']}|{datetime.now().strftime('%Y%m%d%H')}"
        if key in seen:
            log.info(f"    Deja envoye"); continue

        seen.add(key); new+=1
        score=sig["score"]
        bar="█"*(score//10)+"░"*(10-score//10)
        col="\033[92m" if sig["direction"]=="BUY" else "\033[91m"
        print(f"""
  ╔══════════════════════════════════════════════════╗
  ║ {col}{sig['direction']:4}\033[0m | {pair:8} | {sig['tf_count']}/4 TF | {sig['setup']}
  ║ Score [{bar}] {score}/100
  ║ Entree:{sig['entry']} SL:{sig['sl']} ({sig['sl_pips']}pips)
  ║ TP1:{sig['tp1']} TP2:{sig['tp2']} TP3:{sig['tp3']}
  ╚══════════════════════════════════════════════════╝""")

        send_telegram(sig)
        with open("signals_log.jsonl","a",encoding="utf-8") as f:
            f.write(json.dumps(sig,ensure_ascii=False)+"\n")

    log.info(f"Scan #{n} termine en {time.time()-t0:.0f}s · {new} signal(s)")
    return seen

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def run():
    print("\033[36m")
    print("  ╔═══════════════════════════════════════════════╗")
    print("  ║   FOREX SCANNER — STRATEGIE DAY TRADING     ║")
    print("  ║   D1+H4+H1+M15 | London+NY | News Filter    ║")
    print("  ║   S/R | Fibonacci | Psycho | HH/HL/LH/LL   ║")
    print("  ╚═══════════════════════════════════════════════╝")
    print("\033[0m")
    print("  ENTREE = scanner maintenant | q + ENTREE = quitter\n")
    log.info(f"Demarre — {len(CONFIG['pairs'])} paires | Score min: {CONFIG['min_confluence_score']}")
    log.info("Sessions actives: London 9h-13h | NY Overlap 13h-17h (heure Paris)")

    threading.Thread(target=kb,daemon=True).start()
    seen=set(); n=0; next_scan=time.time()

    while not _quit.is_set():
        now=time.time()
        if now>=next_scan or _force.is_set():
            _force.clear()
            if _quit.is_set(): break
            n+=1
            seen=do_scan(seen,n)
            if len(seen)>1000: seen=set(list(seen)[-500:])
            next_scan=time.time()+CONFIG["scan_interval"]
            mins=CONFIG["scan_interval"]//60
            _,active=get_session()
            if active:
                log.info(f"Prochain scan dans {mins}min")
            else:
                log.info(f"Hors session — prochain scan dans {mins}min (actif 9h-17h Paris)")
        else:
            time.sleep(1)

if __name__=="__main__":
    run()
