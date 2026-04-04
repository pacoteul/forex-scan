"""
╔══════════════════════════════════════════════════════════════════════╗
║     ULTIMATE FOREX SCANNER — WALL STREET PROFESSIONAL EDITION      ║
║                                                                      ║
║  Données: Twelve Data (réelles)                                     ║
║  Setups: 12 détecteurs                                              ║
║  Filtres: Session · Volume · Tendance HTF · Volatilité · News       ║
║  Niveaux: Fibonacci · Pivot Points · Support/Résistance dynamique   ║
║  Gestion risque: SL/TP/RR · Taille position · ATR adaptatif        ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import pandas as pd
import numpy as np
import requests
import time
import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Optional
from config import CONFIG

# ─── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)s │ %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scanner.log", encoding="utf-8")
    ]
)
log = logging.getLogger("SCANNER")

# ─── PAIRES ───────────────────────────────────────────────────────────────────
ALL_PAIRS = [
    "EUR/USD","GBP/USD","USD/JPY","USD/CHF","AUD/USD","NZD/USD","USD/CAD",
    "EUR/GBP","EUR/JPY","EUR/CAD","EUR/AUD","EUR/NZD","EUR/CHF",
    "GBP/JPY","GBP/CAD","GBP/AUD","GBP/NZD","GBP/CHF",
    "CAD/JPY","AUD/JPY","NZD/JPY","CHF/JPY",
    "AUD/NZD","AUD/CAD","AUD/CHF",
    "NZD/CAD","NZD/CHF","CAD/CHF",
    "XAU/USD",
]

TF_MAP = {"M5":"5min","M15":"15min","M30":"30min","H1":"1h","H4":"4h","D1":"1day"}

# ─── TWELVE DATA ──────────────────────────────────────────────────────────────
_request_count = 0
_minute_start  = time.time()

def td_get(endpoint: str, params: dict) -> Optional[dict]:
    global _request_count, _minute_start
    key = CONFIG.get("twelvedata_key","")
    if not key or key == "TA_CLE_ICI":
        log.error("❌ Clé Twelve Data manquante dans config.py !")
        return None

    # Rate limiting : max 8 req/minute plan gratuit
    now = time.time()
    if now - _minute_start >= 60:
        _request_count = 0
        _minute_start  = now
    if _request_count >= 7:
        wait = 60 - (now - _minute_start) + 1
        log.info(f"  ⏳ Rate limit — attente {wait:.0f}s...")
        time.sleep(wait)
        _request_count = 0
        _minute_start  = time.time()

    try:
        params["apikey"] = key
        r = requests.get(f"https://api.twelvedata.com/{endpoint}",
                         params=params, timeout=20)
        _request_count += 1
        data = r.json()
        if isinstance(data,dict) and data.get("status") == "error":
            log.debug(f"  TD erreur: {data.get('message')}")
            return None
        return data
    except Exception as e:
        log.debug(f"  TD req échouée: {e}")
        return None

def fetch_ohlcv(pair: str, tf: str) -> Optional[pd.DataFrame]:
    symbol = pair.replace("/","")
    data = td_get("time_series", {
        "symbol": symbol, "interval": TF_MAP[tf],
        "outputsize": 200, "format": "JSON",
    })
    if not data or "values" not in data: return None
    try:
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        for c in ["open","high","low","close"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume",0), errors="coerce").fillna(0)
        df = df[["open","high","low","close","volume"]].dropna(subset=["open","high","low","close"])
        return df if len(df) >= 50 else None
    except: return None

# ─── INDICATEURS PRO ──────────────────────────────────────────────────────────
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()

def rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + g/l.replace(0,np.nan))

def atr(df, n=14):
    tr = pd.concat([
        df["high"]-df["low"],
        (df["high"]-df["close"].shift()).abs(),
        (df["low"]-df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def macd(s, fast=12, slow=26, sig=9):
    ml = ema(s,fast) - ema(s,slow)
    sl = ema(ml,sig)
    return ml, sl, ml-sl

def bollinger(s, n=20, k=2):
    m = sma(s,n); d = s.rolling(n).std()
    return m+k*d, m, m-k*d

def stochastic(df, k=14, d=3):
    lo = df["low"].rolling(k).min()
    hi = df["high"].rolling(k).max()
    kl = 100*(df["close"]-lo)/(hi-lo+1e-10)
    return kl, kl.rolling(d).mean()

def adx(df, n=14):
    """Average Directional Index — force de la tendance."""
    up   = df["high"].diff()
    down = -df["low"].diff()
    pdm  = up.where((up>down) & (up>0), 0)
    ndm  = down.where((down>up) & (down>0), 0)
    atr_ = atr(df, n)
    pdi  = 100 * pdm.ewm(alpha=1/n,adjust=False).mean() / atr_.replace(0,np.nan)
    ndi  = 100 * ndm.ewm(alpha=1/n,adjust=False).mean() / atr_.replace(0,np.nan)
    dx   = 100 * (pdi-ndi).abs() / (pdi+ndi).replace(0,np.nan)
    return dx.ewm(alpha=1/n,adjust=False).mean(), pdi, ndi

def cci(df, n=20):
    """Commodity Channel Index."""
    tp = (df["high"]+df["low"]+df["close"])/3
    ma = tp.rolling(n).mean()
    md = tp.rolling(n).apply(lambda x: np.mean(np.abs(x-x.mean())))
    return (tp-ma)/(0.015*md.replace(0,np.nan))

def williams_r(df, n=14):
    hi = df["high"].rolling(n).max()
    lo = df["low"].rolling(n).min()
    return -100*(hi-df["close"])/(hi-lo+1e-10)

def vwap(df):
    """Volume Weighted Average Price."""
    tp = (df["high"]+df["low"]+df["close"])/3
    cv = (tp * df["volume"]).cumsum()
    v  = df["volume"].cumsum()
    return cv / v.replace(0,np.nan)

def ichimoku(df):
    """Ichimoku Cloud complet."""
    h9  = df["high"].rolling(9).max();  l9  = df["low"].rolling(9).min()
    h26 = df["high"].rolling(26).max(); l26 = df["low"].rolling(26).min()
    h52 = df["high"].rolling(52).max(); l52 = df["low"].rolling(52).min()
    tenkan  = (h9+l9)/2
    kijun   = (h26+l26)/2
    senkouA = (tenkan+kijun)/2
    senkouB = (h52+l52)/2
    chikou  = df["close"].shift(-26)
    return tenkan, kijun, senkouA, senkouB, chikou

def pivot_points(df):
    """Pivot Points classiques (basés sur la bougie précédente)."""
    h = df["high"].iloc[-2]; l = df["low"].iloc[-2]; c = df["close"].iloc[-2]
    pp = (h+l+c)/3
    return {
        "pp":  pp,
        "r1":  2*pp - l,
        "r2":  pp + (h-l),
        "r3":  h + 2*(pp-l),
        "s1":  2*pp - h,
        "s2":  pp - (h-l),
        "s3":  l - 2*(h-pp),
    }

def fibonacci_levels(df, lookback=50):
    """Niveaux de Fibonacci sur le dernier swing."""
    recent = df.tail(lookback)
    high = recent["high"].max(); low = recent["low"].min()
    rng  = high - low
    return {
        "high": high, "low": low,
        "0.236": high - rng*0.236,
        "0.382": high - rng*0.382,
        "0.500": high - rng*0.500,
        "0.618": high - rng*0.618,
        "0.786": high - rng*0.786,
    }

def market_structure(df, left=5, right=5):
    """Détecte Higher Highs/Lower Lows pour la structure de marché."""
    highs = []; lows = []
    for i in range(left, len(df)-right):
        if all(df["high"].iloc[i] > df["high"].iloc[i-j] for j in range(1,left+1)) and \
           all(df["high"].iloc[i] > df["high"].iloc[i+j] for j in range(1,right+1)):
            highs.append(df["high"].iloc[i])
        if all(df["low"].iloc[i] < df["low"].iloc[i-j] for j in range(1,left+1)) and \
           all(df["low"].iloc[i] < df["low"].iloc[i+j] for j in range(1,right+1)):
            lows.append(df["low"].iloc[i])
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1]>highs[-2] and lows[-1]>lows[-2]: return "BULLISH"
        if highs[-1]<highs[-2] and lows[-1]<lows[-2]: return "BEARISH"
    return "NEUTRAL"

def volatility_regime(df):
    """Classe la volatilité: LOW / NORMAL / HIGH."""
    a = atr(df, 14).iloc[-1]
    hist = atr(df, 14).tail(50)
    pct = (a - hist.min()) / (hist.max() - hist.min() + 1e-10)
    if pct < 0.3: return "LOW", pct
    if pct < 0.7: return "NORMAL", pct
    return "HIGH", pct

# ─── DÉTECTEURS DE SETUP ──────────────────────────────────────────────────────
def detect_pin_bar(df):
    c = df.iloc[-1]; rng = c["high"]-c["low"]
    if rng < 1e-7: return None
    body = abs(c["close"]-c["open"])
    upper = c["high"]-max(c["open"],c["close"])
    lower = min(c["open"],c["close"])-c["low"]
    if lower/rng > 0.62 and body/rng < 0.32:
        return {"setup":"📍 Pin Bar","dir":"BUY","strength":round(lower/rng*100),
                "details":f"Rejet haussier {round(lower/rng*100)}% mèche"}
    if upper/rng > 0.62 and body/rng < 0.32:
        return {"setup":"📍 Pin Bar","dir":"SELL","strength":round(upper/rng*100),
                "details":f"Rejet baissier {round(upper/rng*100)}% mèche"}
    return None

def detect_engulfing(df):
    if len(df) < 2: return None
    c,p = df.iloc[-1],df.iloc[-2]
    cb=abs(c["close"]-c["open"]); pb=abs(p["close"]-p["open"])
    if pb<1e-7: return None
    r=round(cb/pb,1)
    if p["close"]<p["open"] and c["close"]>c["open"] and c["open"]<=p["close"] and c["close"]>=p["open"] and cb>pb:
        return {"setup":"🕯 Engulfing","dir":"BUY","strength":min(99,int(r*45)),
                "details":f"Englobante haussière ×{r}"}
    if p["close"]>p["open"] and c["close"]<c["open"] and c["open"]>=p["close"] and c["close"]<=p["open"] and cb>pb:
        return {"setup":"🕯 Engulfing","dir":"SELL","strength":min(99,int(r*45)),
                "details":f"Englobante baissière ×{r}"}
    return None

def detect_breakout(df, lb=20):
    if len(df)<lb+2: return None
    c=df.iloc[-1]; prev=df.iloc[-(lb+1):-1]
    res=prev["high"].max(); sup=prev["low"].min(); rng=res-sup
    if rng<1e-7: return None
    if c["close"]>res and c["close"]>c["open"]:
        return {"setup":"💥 Breakout","dir":"BUY","strength":min(99,60+int((c["close"]-res)/rng*200)),
                "details":f"Cassure résistance {res:.5f}"}
    if c["close"]<sup and c["close"]<c["open"]:
        return {"setup":"💥 Breakout","dir":"SELL","strength":min(99,60+int((sup-c["close"])/rng*200)),
                "details":f"Cassure support {sup:.5f}"}
    return None

def detect_rsi_div(df):
    if len(df)<30: return None
    rs=rsi(df["close"],14).values; cl=df["close"].values
    peaks=[i for i in range(2,len(cl)-2) if cl[i]>cl[i-1] and cl[i]>cl[i+1]]
    if len(peaks)>=2:
        i1,i2=peaks[-2],peaks[-1]
        if cl[i2]>cl[i1] and rs[i2]<rs[i1] and rs[i2]>55:
            return {"setup":"📊 RSI Div","dir":"SELL","strength":min(99,50+int(abs(rs[i1]-rs[i2])*2)),
                    "details":f"Divergence baissière RSI Δ{abs(rs[i1]-rs[i2]):.1f}"}
    troughs=[i for i in range(2,len(cl)-2) if cl[i]<cl[i-1] and cl[i]<cl[i+1]]
    if len(troughs)>=2:
        i1,i2=troughs[-2],troughs[-1]
        if cl[i2]<cl[i1] and rs[i2]>rs[i1] and rs[i2]<45:
            return {"setup":"📊 RSI Div","dir":"BUY","strength":min(99,50+int(abs(rs[i2]-rs[i1])*2)),
                    "details":f"Divergence haussière RSI Δ{abs(rs[i2]-rs[i1]):.1f}"}
    return None

def detect_smc(df, lb=15):
    if len(df)<lb+2: return None
    c=df.iloc[-1]; prev=df.iloc[-(lb+1):-1]
    sh=prev["high"].max(); sl_=prev["low"].min()
    if c["close"]>sh and c["close"]>c["open"]:
        ob=next((prev.iloc[i] for i in range(len(prev)-1,-1,-1) if prev.iloc[i]["close"]<prev.iloc[i]["open"]),None)
        s=f"OB {ob['low']:.5f}–{ob['high']:.5f}" if ob is not None else "BOS confirmé"
        return {"setup":"🏦 SMC/BOS","dir":"BUY","strength":78,"details":f"BOS haussier · {s}"}
    if c["close"]<sl_ and c["close"]<c["open"]:
        ob=next((prev.iloc[i] for i in range(len(prev)-1,-1,-1) if prev.iloc[i]["close"]>prev.iloc[i]["open"]),None)
        s=f"OB {ob['low']:.5f}–{ob['high']:.5f}" if ob is not None else "BOS confirmé"
        return {"setup":"🏦 SMC/BOS","dir":"SELL","strength":78,"details":f"BOS baissier · {s}"}
    return None

def detect_macd_cross(df):
    if len(df)<35: return None
    _,_,hist=macd(df["close"])
    if hist.isna().iloc[-2]: return None
    if hist.iloc[-2]<0 and hist.iloc[-1]>0:
        return {"setup":"📈 MACD Cross","dir":"BUY","strength":72,"details":"Croisement haussier MACD/Signal"}
    if hist.iloc[-2]>0 and hist.iloc[-1]<0:
        return {"setup":"📈 MACD Cross","dir":"SELL","strength":72,"details":"Croisement baissier MACD/Signal"}
    return None

def detect_ichimoku(df):
    """Signal Ichimoku : prix crosses au-dessus/dessous du nuage."""
    if len(df)<60: return None
    tenkan,kijun,sA,sB,_ = ichimoku(df)
    c = df["close"].iloc[-1]; p = df["close"].iloc[-2]
    cloud_top = max(sA.iloc[-1], sB.iloc[-1])
    cloud_bot = min(sA.iloc[-1], sB.iloc[-1])
    tk_cross_bull = tenkan.iloc[-1] > kijun.iloc[-1] and tenkan.iloc[-2] <= kijun.iloc[-2]
    tk_cross_bear = tenkan.iloc[-1] < kijun.iloc[-1] and tenkan.iloc[-2] >= kijun.iloc[-2]
    if c > cloud_top and p <= cloud_top and tenkan.iloc[-1] > kijun.iloc[-1]:
        return {"setup":"☁ Ichimoku","dir":"BUY","strength":80,
                "details":f"Prix sort du nuage haussier | TK cross: {tk_cross_bull}"}
    if c < cloud_bot and p >= cloud_bot and tenkan.iloc[-1] < kijun.iloc[-1]:
        return {"setup":"☁ Ichimoku","dir":"SELL","strength":80,
                "details":f"Prix sort du nuage baissier | TK cross: {tk_cross_bear}"}
    return None

def detect_pivot_bounce(df):
    """Rebond sur Pivot Points classiques."""
    if len(df)<3: return None
    pp = pivot_points(df)
    c  = df.iloc[-1]; p = df.iloc[-2]
    atr_v = atr(df).iloc[-1]
    levels = [("PP",pp["pp"]),("S1",pp["s1"]),("S2",pp["s2"]),
              ("R1",pp["r1"]),("R2",pp["r2"])]
    for name, level in levels:
        if abs(p["low"]-level) < atr_v*0.3 and c["close"]>c["open"] and c["close"]>level:
            return {"setup":"🔢 Pivot","dir":"BUY","strength":75,
                    "details":f"Rebond haussier sur {name} ({level:.5f})"}
        if abs(p["high"]-level) < atr_v*0.3 and c["close"]<c["open"] and c["close"]<level:
            return {"setup":"🔢 Pivot","dir":"SELL","strength":75,
                    "details":f"Rejet baissier sur {name} ({level:.5f})"}
    return None

def detect_fib_bounce(df):
    """Rebond sur niveau de Fibonacci clé (38.2%, 50%, 61.8%)."""
    if len(df)<52: return None
    fibs = fibonacci_levels(df)
    c = df.iloc[-1]; atr_v = atr(df).iloc[-1]
    key_levels = [("Fib 61.8%", fibs["0.618"]),
                  ("Fib 50.0%", fibs["0.500"]),
                  ("Fib 38.2%", fibs["0.382"])]
    for name, level in key_levels:
        if abs(c["low"]-level) < atr_v*0.4 and c["close"]>c["open"]:
            return {"setup":"🌀 Fibonacci","dir":"BUY","strength":76,
                    "details":f"Rebond haussier sur {name} ({level:.5f})"}
        if abs(c["high"]-level) < atr_v*0.4 and c["close"]<c["open"]:
            return {"setup":"🌀 Fibonacci","dir":"SELL","strength":76,
                    "details":f"Rejet baissier sur {name} ({level:.5f})"}
    return None

def detect_cci_extreme(df):
    """CCI en zone extrême avec retournement."""
    if len(df)<25: return None
    cc = cci(df)
    if cc.iloc[-2] < -150 and cc.iloc[-1] > -150:
        return {"setup":"📉 CCI","dir":"BUY","strength":70,
                "details":f"CCI retour de zone survente ({cc.iloc[-1]:.0f})"}
    if cc.iloc[-2] > 150 and cc.iloc[-1] < 150:
        return {"setup":"📉 CCI","dir":"SELL","strength":70,
                "details":f"CCI retour de zone surachat ({cc.iloc[-1]:.0f})"}
    return None

def detect_vwap_bounce(df):
    """Prix rebondit sur le VWAP — utilisé par les pros intraday."""
    if len(df)<20 or df["volume"].sum()==0: return None
    vw = vwap(df)
    c  = df.iloc[-1]; p = df.iloc[-2]; atr_v = atr(df).iloc[-1]
    if abs(p["low"]-vw.iloc[-2]) < atr_v*0.3 and c["close"]>c["open"] and c["close"]>vw.iloc[-1]:
        return {"setup":"💎 VWAP","dir":"BUY","strength":73,
                "details":f"Rebond haussier sur VWAP ({vw.iloc[-1]:.5f})"}
    if abs(p["high"]-vw.iloc[-2]) < atr_v*0.3 and c["close"]<c["open"] and c["close"]<vw.iloc[-1]:
        return {"setup":"💎 VWAP","dir":"SELL","strength":73,
                "details":f"Rejet baissier sur VWAP ({vw.iloc[-1]:.5f})"}
    return None

DETECTORS = [
    detect_pin_bar, detect_engulfing, detect_breakout, detect_rsi_div,
    detect_smc, detect_macd_cross, detect_ichimoku, detect_pivot_bounce,
    detect_fib_bounce, detect_cci_extreme, detect_vwap_bounce,
]

# ─── SCORING CONFLUENCE WALL STREET ──────────────────────────────────────────
def full_confluence_score(df: pd.DataFrame, direction: str) -> dict:
    """
    Score 0-100 basé sur 10+ indicateurs professionnels.
    Inspiré des systèmes de prop trading et hedge funds.
    """
    score = 0; factors = []
    if len(df) < 50: return {"score":0,"factors":[]}

    close = df["close"]
    rsi_v  = rsi(close,14).iloc[-1]
    _,_,hist = macd(close)
    upper,mid,lower = bollinger(close)
    e20=ema(close,20); e50=ema(close,50); e200=ema(close,200) if len(df)>=200 else None
    k_stoch,d_stoch = stochastic(df)
    adx_v,pdi,ndi   = adx(df)
    cci_v  = cci(df).iloc[-1]
    wr_v   = williams_r(df).iloc[-1]
    c      = df.iloc[-1]
    vol_avg= df["volume"].rolling(20).mean().iloc[-1]
    struct = market_structure(df)
    vol_regime, vol_pct = volatility_regime(df)

    # ── Structure de marché (10pts) ──
    if direction=="BUY" and struct=="BULLISH":
        score+=10; factors.append("✦ Structure haussière (HH/HL)")
    elif direction=="SELL" and struct=="BEARISH":
        score+=10; factors.append("✦ Structure baissière (LH/LL)")

    # ── ADX — force de tendance (10pts) ──
    if not adx_v.isna().iloc[-1]:
        av = adx_v.iloc[-1]
        if av > 25:
            if direction=="BUY" and pdi.iloc[-1]>ndi.iloc[-1]:
                score+=10; factors.append(f"✦ ADX fort haussier {av:.0f}")
            elif direction=="SELL" and ndi.iloc[-1]>pdi.iloc[-1]:
                score+=10; factors.append(f"✦ ADX fort baissier {av:.0f}")

    # ── RSI (15pts) ──
    if direction=="BUY":
        if rsi_v<30:   score+=15; factors.append(f"✦ RSI survente extrême {rsi_v:.0f}")
        elif rsi_v<45: score+=8;  factors.append(f"✦ RSI survente {rsi_v:.0f}")
    else:
        if rsi_v>70:   score+=15; factors.append(f"✦ RSI surachat extrême {rsi_v:.0f}")
        elif rsi_v>55: score+=8;  factors.append(f"✦ RSI surachat {rsi_v:.0f}")

    # ── MACD (10pts) ──
    if not hist.isna().iloc[-1]:
        if direction=="BUY":
            if hist.iloc[-1]>0 and hist.iloc[-2]<0: score+=10; factors.append("✦ MACD croisement haussier")
            elif hist.iloc[-1]>0: score+=5; factors.append("✦ MACD positif")
        else:
            if hist.iloc[-1]<0 and hist.iloc[-2]>0: score+=10; factors.append("✦ MACD croisement baissier")
            elif hist.iloc[-1]<0: score+=5; factors.append("✦ MACD négatif")

    # ── EMA alignment (10pts) ──
    if direction=="BUY":
        if e200 is not None and e20.iloc[-1]>e50.iloc[-1]>e200.iloc[-1]:
            score+=10; factors.append("✦ EMA 20>50>200 alignées haussier")
        elif e20.iloc[-1]>e50.iloc[-1]:
            score+=5; factors.append("✦ EMA 20>50 haussier")
    else:
        if e200 is not None and e20.iloc[-1]<e50.iloc[-1]<e200.iloc[-1]:
            score+=10; factors.append("✦ EMA 20<50<200 alignées baissier")
        elif e20.iloc[-1]<e50.iloc[-1]:
            score+=5; factors.append("✦ EMA 20<50 baissier")

    # ── Bollinger (8pts) ──
    if direction=="BUY" and c["close"]<=lower.iloc[-1]*1.002:
        score+=8; factors.append("✦ Prix bande basse Bollinger")
    elif direction=="SELL" and c["close"]>=upper.iloc[-1]*0.998:
        score+=8; factors.append("✦ Prix bande haute Bollinger")

    # ── Stochastique (8pts) ──
    if not k_stoch.isna().iloc[-1]:
        if direction=="BUY" and k_stoch.iloc[-1]<25:
            score+=8; factors.append(f"✦ Stoch survente {k_stoch.iloc[-1]:.0f}")
        elif direction=="SELL" and k_stoch.iloc[-1]>75:
            score+=8; factors.append(f"✦ Stoch surachat {k_stoch.iloc[-1]:.0f}")

    # ── CCI (5pts) ──
    if direction=="BUY" and cci_v < -100:
        score+=5; factors.append(f"✦ CCI survente {cci_v:.0f}")
    elif direction=="SELL" and cci_v > 100:
        score+=5; factors.append(f"✦ CCI surachat {cci_v:.0f}")

    # ── Williams %R (5pts) ──
    if direction=="BUY" and wr_v < -80:
        score+=5; factors.append(f"✦ Williams %R survente {wr_v:.0f}")
    elif direction=="SELL" and wr_v > -20:
        score+=5; factors.append(f"✦ Williams %R surachat {wr_v:.0f}")

    # ── Volume (5pts) ──
    if not np.isnan(vol_avg) and vol_avg>0 and c["volume"]>vol_avg*1.5:
        score+=5; factors.append("✦ Volume +50% au-dessus moyenne")

    # ── Volatilité (bonus/malus) ──
    if vol_regime == "HIGH":
        factors.append("⚠ Volatilité HAUTE — augmenter SL")
    elif vol_regime == "LOW":
        factors.append("ℹ Volatilité basse — spread attention")

    return {"score":min(100,score),"factors":factors,
            "adx":round(adx_v.iloc[-1],1) if not adx_v.isna().iloc[-1] else None,
            "cci":round(cci_v,1), "wr":round(wr_v,1),
            "structure":struct, "vol_regime":vol_regime}

# ─── SESSION + QUALITÉ ────────────────────────────────────────────────────────
def get_session() -> tuple:
    h = datetime.now(timezone.utc).hour
    if 9<=h<12:   return "🌍 London", 3
    if 12<=h<14:  return "🌍🌎 London/NY Overlap ⭐⭐", 5   # Meilleure session
    if 14<=h<17:  return "🌎 New York", 3
    if 7<=h<9:    return "🌍 Pre-London", 2
    if 17<=h<20:  return "🌎 NY Late", 2
    if 22<=h or h<7: return "🌏 Tokyo", 2
    return "😴 Calme", 1

def session_bonus(session_name: str, pair: str) -> int:
    """Bonus de score selon la session et la paire."""
    jpy_pairs = ["USD/JPY","EUR/JPY","GBP/JPY","CAD/JPY","AUD/JPY","NZD/JPY","CHF/JPY"]
    if "Tokyo" in session_name and pair in jpy_pairs: return 5
    if "London" in session_name: return 5
    if "Overlap" in session_name: return 8
    if "New York" in session_name: return 5
    return 0

# ─── CALCUL NIVEAUX DE TRADING ────────────────────────────────────────────────
def calc_levels(df, direction, price, atr_v, dec):
    """
    Calcule SL/TP basés sur ATR + structure de marché.
    Utilise aussi les Pivot Points et Fibonacci pour valider les niveaux.
    """
    pp = pivot_points(df)
    fibs = fibonacci_levels(df)

    if direction == "BUY":
        # SL sous le dernier swing low ou 1.5x ATR
        swing_low = df["low"].tail(10).min()
        sl_atr    = price - atr_v * 1.5
        sl        = round(min(sl_atr, swing_low - atr_v*0.3), dec)
        sl_dist   = price - sl

        tp1 = round(price + sl_dist * 1.0, dec)   # RR 1:1
        tp2 = round(price + sl_dist * 1.5, dec)   # RR 1:1.5
        tp3 = round(price + sl_dist * 2.5, dec)   # RR 1:2.5

        # Ajuster TP sur niveaux proches (R1, R2, Fib)
        targets = sorted([pp["r1"], pp["r2"], fibs["0.618"], fibs["0.382"]])
        for t in targets:
            if t > price + atr_v*0.5:
                tp1_adj = round(t, dec)
                if tp1_adj > tp1: tp1 = tp1_adj
                break

    else:  # SELL
        swing_high = df["high"].tail(10).max()
        sl_atr     = price + atr_v * 1.5
        sl         = round(max(sl_atr, swing_high + atr_v*0.3), dec)
        sl_dist    = sl - price

        tp1 = round(price - sl_dist * 1.0, dec)
        tp2 = round(price - sl_dist * 1.5, dec)
        tp3 = round(price - sl_dist * 2.5, dec)

        targets = sorted([pp["s1"], pp["s2"], fibs["0.618"], fibs["0.382"]], reverse=True)
        for t in targets:
            if t < price - atr_v*0.5:
                tp1_adj = round(t, dec)
                if tp1_adj < tp1: tp1 = tp1_adj
                break

    rr1 = round(abs(tp1-price)/max(abs(sl-price),1e-7), 2)
    rr2 = round(abs(tp2-price)/max(abs(sl-price),1e-7), 2)
    rr3 = round(abs(tp3-price)/max(abs(sl-price),1e-7), 2)

    return {"sl":sl,"tp1":tp1,"tp2":tp2,"tp3":tp3,
            "rr1":rr1,"rr2":rr2,"rr3":rr3,"sl_pips":round(abs(price-sl)*10000,1)}

# ─── ANALYSE UNIFIÉE MULTI-TIMEFRAME PAR PAIRE ───────────────────────────────
def analyze_pair(pair: str, timeframes: list) -> list:
    """
    Analyse une paire sur TOUS les timeframes et produit UN SEUL signal unifié.
    Logique Wall Street: cherche l'alignement multi-TF pour maximiser la fiabilité.
    """
    active    = CONFIG["active_setups"]
    min_score = CONFIG["min_confluence_score"]
    session_name, _ = get_session()
    sess_bonus = session_bonus(session_name, pair)

    # ── Étape 1: Collecter les données et signaux de chaque TF ────────────────
    tf_results = {}  # tf → {df, setups[], conf_score, direction_vote, rsi, atr}

    for tf in timeframes:
        df = fetch_ohlcv(pair, tf)
        if df is None:
            continue

        tf_setups = []
        for detector in DETECTORS:
            try:
                result = detector(df)
                if not result: continue
                setup_name = result["setup"].split(" ",1)[-1] if " " in result["setup"] else result["setup"]
                if setup_name not in active: continue
                tf_setups.append(result)
            except:
                continue

        if not tf_setups:
            # Même sans setup, on garde les données pour la confluence
            tf_results[tf] = {
                "df": df, "setups": [],
                "rsi": rsi(df["close"],14).iloc[-1],
                "atr": atr(df).iloc[-1],
                "price": df["close"].iloc[-1],
            }
        else:
            tf_results[tf] = {
                "df": df, "setups": tf_setups,
                "rsi": rsi(df["close"],14).iloc[-1],
                "atr": atr(df).iloc[-1],
                "price": df["close"].iloc[-1],
            }

    if not tf_results:
        return []

    # ── Étape 2: Trouver la direction dominante sur tous les TF ───────────────
    buy_votes  = 0
    sell_votes = 0
    buy_setups_found  = []
    sell_setups_found = []

    # Poids par timeframe (D1 > H4 > H1 > M30 > M15 > M5)
    TF_WEIGHT = {"D1":6, "H4":5, "H1":4, "M30":3, "M15":2, "M5":1}

    for tf, data in tf_results.items():
        weight = TF_WEIGHT.get(tf, 1)
        # Vote basé sur les setups détectés
        for setup in data["setups"]:
            if setup["dir"] == "BUY":
                buy_votes += weight
                buy_setups_found.append((tf, setup))
            else:
                sell_votes += weight
                sell_setups_found.append((tf, setup))
        # Vote basé sur la structure (RSI, EMA) même sans setup
        df = data["df"]
        if len(df) >= 50:
            e20 = ema(df["close"],20).iloc[-1]
            e50 = ema(df["close"],50).iloc[-1]
            r   = data["rsi"]
            if e20 > e50 and r < 60: buy_votes  += weight * 0.3
            if e20 < e50 and r > 40: sell_votes += weight * 0.3

    if buy_votes == 0 and sell_votes == 0:
        return []

    # Direction gagnante
    direction = "BUY" if buy_votes >= sell_votes else "SELL"
    winning_setups = buy_setups_found if direction == "BUY" else sell_setups_found

    if not winning_setups:
        return []

    # ── Étape 3: Calculer le score d'alignement multi-TF ─────────────────────
    total_weight  = sum(TF_WEIGHT.get(tf,1) for tf in tf_results)
    aligned_weight = sum(TF_WEIGHT.get(tf,1) for tf,_ in winning_setups)
    alignment_pct  = round(aligned_weight / max(total_weight, 1) * 100)

    # TFs confirmés
    confirmed_tfs = sorted(set(tf for tf,_ in winning_setups),
                           key=lambda t: list(TF_WEIGHT.keys()).index(t) if t in TF_WEIGHT else 99)

    # Setup principal = celui du TF le plus élevé
    main_tf, main_setup = winning_setups[0]
    for tf, setup in winning_setups:
        if TF_WEIGHT.get(tf,0) > TF_WEIGHT.get(main_tf,0):
            main_tf, main_setup = tf, setup

    # ── Étape 4: Score de confluence sur le TF principal ──────────────────────
    main_df = tf_results[main_tf]["df"]
    conf    = full_confluence_score(main_df, direction)

    # Score final pondéré
    alignment_bonus = min(20, len(confirmed_tfs) * 4)  # +4 pts par TF aligné
    raw_score = (main_setup["strength"]*0.25 + conf["score"]*0.55 + alignment_pct*0.2) + sess_bonus + alignment_bonus
    total_score = min(100, round(raw_score))

    if total_score < min_score:
        return []

    # ── Étape 5: Niveaux sur le TF principal ──────────────────────────────────
    price = tf_results[main_tf]["price"]
    atr_v = tf_results[main_tf]["atr"]
    dec   = 5 if price < 10 else 3
    levels = calc_levels(main_df, direction, price, atr_v, dec)

    rsi_v = tf_results[main_tf]["rsi"]

    # Résumé des setups par TF
    tf_summary = []
    for tf in ["D1","H4","H1","M30","M15","M5"]:
        if tf not in tf_results: continue
        setups_on_tf = [s["setup"] for _,s in winning_setups if _ == tf]
        if setups_on_tf:
            tf_summary.append(f"✅ {tf}: {', '.join(setups_on_tf)}")
        elif tf in tf_results:
            tf_summary.append(f"➖ {tf}: aucun setup")

    signal = {
        "pair":         pair,
        "tf":           main_tf,           # TF principal
        "confirmed_tfs": " · ".join(confirmed_tfs),  # Tous les TF qui confirment
        "tf_summary":   "\n".join(tf_summary),
        "setup":        main_setup["setup"],
        "direction":    direction,
        "strength":     main_setup["strength"],
        "confluence":   conf["score"],
        "alignment":    alignment_pct,     # % des TF alignés
        "total_score":  total_score,
        "details":      main_setup["details"],
        "factors":      conf["factors"],
        "adx":          conf.get("adx"),
        "cci":          conf.get("cci"),
        "wr":           conf.get("wr"),
        "structure":    conf.get("structure","?"),
        "vol_regime":   conf.get("vol_regime","?"),
        "entry":        round(price, dec),
        "sl":           levels["sl"],
        "tp1":          levels["tp1"],
        "tp2":          levels["tp2"],
        "tp3":          levels["tp3"],
        "rr1":          levels["rr1"],
        "rr2":          levels["rr2"],
        "rr3":          levels["rr3"],
        "sl_pips":      levels["sl_pips"],
        "atr":          round(atr_v, dec),
        "rsi":          round(rsi_v, 1),
        "session":      session_name,
        "time":         datetime.now().strftime("%H:%M:%S"),
        "timestamp":    datetime.now().isoformat(),
        "buy_votes":    round(buy_votes, 1),
        "sell_votes":   round(sell_votes, 1),
    }

    return [signal]

# ─── CALENDRIER ÉCONOMIQUE ────────────────────────────────────────────────────
# Mapping devise → paires concernées
CURRENCY_PAIRS = {
    "USD": ["EUR/USD","GBP/USD","USD/JPY","USD/CHF","AUD/USD","NZD/USD","USD/CAD","XAU/USD"],
    "EUR": ["EUR/USD","EUR/GBP","EUR/JPY","EUR/CAD","EUR/AUD","EUR/NZD","EUR/CHF"],
    "GBP": ["GBP/USD","EUR/GBP","GBP/JPY","GBP/CAD","GBP/AUD","GBP/NZD","GBP/CHF"],
    "JPY": ["USD/JPY","EUR/JPY","GBP/JPY","CAD/JPY","AUD/JPY","NZD/JPY","CHF/JPY"],
    "CAD": ["USD/CAD","EUR/CAD","GBP/CAD","CAD/JPY","AUD/CAD","NZD/CAD","CAD/CHF"],
    "AUD": ["AUD/USD","EUR/AUD","GBP/AUD","AUD/JPY","AUD/NZD","AUD/CAD","AUD/CHF"],
    "NZD": ["NZD/USD","EUR/NZD","GBP/NZD","NZD/JPY","AUD/NZD","NZD/CAD","NZD/CHF"],
    "CHF": ["USD/CHF","EUR/CHF","GBP/CHF","CHF/JPY","AUD/CHF","NZD/CHF","CAD/CHF"],
}

def fetch_economic_calendar() -> list:
    """
    Récupère les événements économiques des prochaines 24h
    depuis Forex Factory (scraping public).
    Retourne liste de dicts: {time, currency, impact, event, actual, forecast}
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        # Twelve Data economic calendar endpoint
        key = CONFIG.get("twelvedata_key","")
        if key and key != "TA_CLE_ICI":
            r = requests.get(
                "https://api.twelvedata.com/economic_calendar",
                params={"apikey": key, "start_date": datetime.now().strftime("%Y-%m-%d"),
                        "end_date": (datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")},
                timeout=10
            )
            data = r.json()
            if "result" in data and "list" in data["result"]:
                events = []
                for ev in data["result"]["list"]:
                    events.append({
                        "time":     ev.get("date",""),
                        "currency": ev.get("country","").upper(),
                        "impact":   ev.get("importance","low"),
                        "event":    ev.get("event",""),
                        "actual":   ev.get("actual",""),
                        "forecast": ev.get("estimate",""),
                    })
                return events
    except Exception as e:
        log.debug(f"  Calendrier échoué: {e}")
    return []

def get_news_for_pair(pair: str, events: list) -> dict:
    """
    Filtre les news pertinentes pour une paire donnée.
    Retourne: {alert_level, news_str, block_trade}
    """
    if not events:
        return {"level":"🟢","text":"Aucune news (calendrier indisponible)","block":False}

    currencies = pair.replace("/XAU","").replace("XAU/","USD").split("/")
    currencies = [c for c in currencies if c in CURRENCY_PAIRS]

    now_utc = datetime.now(timezone.utc)
    relevant = []

    for ev in events:
        if ev["currency"] not in currencies: continue
        impact = str(ev.get("impact","")).lower()
        if impact not in ["high","medium"]: continue
        try:
            ev_time = datetime.fromisoformat(ev["time"].replace("Z","+00:00"))
            hours_away = (ev_time - now_utc).total_seconds() / 3600
            if -1 <= hours_away <= 24:  # De -1h à +24h
                relevant.append({**ev, "hours_away": hours_away})
        except:
            continue

    if not relevant:
        return {"level":"🟢","text":"✅ Aucune news à impact fort (24h)","block":False}

    # Trier par proximité
    relevant.sort(key=lambda x: abs(x["hours_away"]))

    lines = []
    block_trade = False
    max_level = "🟢"

    for ev in relevant[:3]:
        h = ev["hours_away"]
        impact = str(ev.get("impact","")).lower()
        if impact == "high":
            if abs(h) <= 4:
                emoji = "🔴"; max_level = "🔴"; block_trade = True
            else:
                emoji = "🟡"; max_level = "🟡" if max_level != "🔴" else "🔴"
        else:
            emoji = "🟡"; max_level = "🟡" if max_level == "🟢" else max_level

        if h < 0:
            timing = f"il y a {abs(h):.1f}h"
        elif h < 1:
            timing = f"dans {int(h*60)}min ⚠️"
        else:
            timing = f"dans {h:.1f}h"

        ev_time_local = datetime.fromisoformat(ev["time"].replace("Z","+00:00"))
        lines.append(f"{emoji} {ev['currency']} — {ev['event']} ({timing})")

    text = "\n".join(lines)
    return {"level": max_level, "text": text, "block": block_trade}

# ─── BOUCLE PRINCIPALE ────────────────────────────────────────────────────────
def print_banner():
    print("""\033[36m
  ██████╗ ██████╗ ██████╗ ███████╗██╗  ██╗
  ██╔════╝██╔═══██╗██╔══██╗██╔════╝╚██╗██╔╝
  █████╗  ██║   ██║██████╔╝█████╗   ╚███╔╝ 
  ██╔══╝  ██║   ██║██╔══██╗██╔══╝   ██╔██╗ 
  ██║     ╚██████╔╝██║  ██║███████╗██╔╝ ██╗
  ╚═╝      ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
   WALL STREET PROFESSIONAL EDITION
\033[0m""")
    print("  💡 Appuie sur ENTRÉE à tout moment pour forcer un scan immédiat")
    print("  💡 Tape 'q' + ENTRÉE pour quitter\n")

import threading
import sys

# Flag partagé pour forcer un scan
_force_scan = threading.Event()
_quit       = threading.Event()

def _keyboard_listener():
    """Thread qui écoute les touches clavier."""
    while not _quit.is_set():
        try:
            line = input()
            if line.strip().lower() == "q":
                log.info("👋 Arrêt demandé — fermeture...")
                _quit.set()
                _force_scan.set()
            else:
                log.info("⚡ Scan forcé par l'utilisateur !")
                _force_scan.set()
        except:
            break

def do_scan(seen: set, scan_count: int, events: list) -> tuple:
    """Exécute un scan complet et retourne (seen, new_count)."""
    session_name, _ = get_session()
    print(f"\n{'═'*65}")
    log.info(f"🔍 SCAN #{scan_count} — {datetime.now().strftime('%d/%m %H:%M:%S')} — {session_name}")
    print(f"{'═'*65}")

    t0 = time.time()
    all_signals = []

    for i, pair in enumerate(CONFIG["pairs"]):
        log.info(f"  [{i+1}/{len(CONFIG['pairs'])}] {pair}...")
        sigs = analyze_pair(pair, CONFIG["timeframes"])

        # Ajouter les news à chaque signal
        news = get_news_for_pair(pair, events)
        for sig in sigs:
            sig["news_level"] = news["level"]
            sig["news_text"]  = news["text"]
            sig["news_block"] = news["block"]

            # Si news rouge à impact fort dans < 4h → baisser le score
            if news["block"]:
                sig["total_score"] = max(0, sig["total_score"] - 20)
                sig["news_warning"] = "⚠️ NEWS IMPACT FORT — Réduire la taille de position !"
            else:
                sig["news_warning"] = ""

        all_signals.extend(sigs)

    all_signals.sort(key=lambda s: s["total_score"], reverse=True)

    new_count = 0
    for sig in all_signals:
        if sig["total_score"] < CONFIG["min_confluence_score"]: continue
        key = f"{sig['pair']}|{sig['setup']}|{sig['direction']}|{sig['tf']}|{datetime.now().strftime('%Y%m%d%H')}"
        if key in seen: continue
        seen.add(key)
        new_count += 1

        score = sig["total_score"]
        bar   = "█"*(score//10) + "░"*(10-score//10)
        col   = "\033[92m" if sig["direction"]=="BUY" else "\033[91m"
        print(f"""
  ╔══════════════════════════════════════════════════════╗
  ║ {col}{sig['direction']:4}\033[0m │ {sig['pair']:8} │ {sig['tf']:3} │ {sig['setup']}
  ║ Score [{bar}] {score}/100
  ║ Entrée:{sig['entry']}  SL:{sig['sl']}  TP1:{sig['tp1']}  TP2:{sig['tp2']}
  ║ RSI:{sig['rsi']}  ADX:{sig.get('adx','?')}  Structure:{sig['structure']}
  ║ News: {sig['news_level']} {sig.get('news_warning','')}
  ║ {sig['session']} · {sig['time']}
  ╚══════════════════════════════════════════════════════╝""")

        send_telegram(sig)

        with open("signals_log.jsonl","a",encoding="utf-8") as f:
            f.write(json.dumps(sig, ensure_ascii=False)+"\n")

    elapsed = time.time()-t0
    log.info(f"✅ Scan #{scan_count} terminé en {elapsed:.0f}s · {new_count} nouveau(x) signal(s)")
    return seen, new_count

def send_telegram_with_news(signal: dict):
    """Version étendue avec news intégrées dans le message Telegram."""
    token   = CONFIG.get("telegram_token","")
    chat_id = CONFIG.get("telegram_chat_id","")
    if not token or token == "TON_TOKEN_ICI": return

    score = signal["total_score"]
    stars = "⭐"*(1 if score<65 else 2 if score<80 else 3)
    emoji = "🟢" if signal["direction"]=="BUY" else "🔴"
    arrow = "▲" if signal["direction"]=="BUY" else "▼"
    facts = "\n".join(f"{f}" for f in signal["factors"][:4]) or "—"
    htf   = f"\n🔭 *HTF:* _{signal.get('htf_note','')}_" if signal.get("htf_note") else ""
    vol   = signal.get("vol_regime","?")
    vol_e = "🔴" if vol=="HIGH" else "🟡" if vol=="LOW" else "🟢"
    news_warn = f"\n⚠️ *{signal.get('news_warning','')}*" if signal.get("news_warning") else ""

    msg = f"""╔══ {stars} {signal['direction']} · {signal['pair']} {stars} ══╗

{emoji} *{signal['pair']}* · {signal['session']}
{signal['setup']} · TF principal: `{signal['tf']}`

📊 *ALIGNEMENT MULTI-TIMEFRAME*
{signal.get('tf_summary','—')}
🎯 Alignement: `{signal.get('alignment','?')}%` des TF confirment

💹 *NIVEAUX*
┌ 🎯 Entrée:      `{signal['entry']}`
├ 🛑 Stop Loss:   `{signal['sl']}` ({signal['sl_pips']} pips)
├ ✅ TP1 (R:{signal['rr1']}): `{signal['tp1']}`
├ ✅ TP2 (R:{signal['rr2']}): `{signal['tp2']}`
└ ✅ TP3 (R:{signal['rr3']}): `{signal['tp3']}`

📊 *INDICATEURS*
• RSI: `{signal['rsi']}` · ADX: `{signal.get('adx','?')}` · CCI: `{signal.get('cci','?')}`
• Structure: `{signal['structure']}` · Volatilité: {vol_e} `{vol}`

📰 *NEWS ÉCONOMIQUES*
{signal.get('news_text','—')}{news_warn}

🎯 *Score global: {score}/100*
• Setup: {signal['strength']}% · Confluence: {signal['confluence']}% · Alignement MTF: {signal.get('alignment','?')}%

🔍 *Confluences:*
{facts}

📋 _{signal['details']}_
⏰ {signal['time']}
╚══════════════════════════╝"""

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id":chat_id,"text":msg,"parse_mode":"Markdown",
                  "disable_web_page_preview":True},
            timeout=10)
        if r.status_code == 200:
            log.info(f"  📤 Telegram ✓ {signal['pair']} {signal['setup']} {signal['direction']} score:{score}")
        else:
            log.warning(f"  Telegram {r.status_code}: {r.text[:80]}")
    except Exception as e:
        log.warning(f"  Telegram échoué: {e}")

# Override send_telegram avec la version news
send_telegram = send_telegram_with_news

def run():
    print_banner()
    log.info("🚀 Scanner Pro démarré — Twelve Data + News Edition")
    log.info(f"📊 {len(CONFIG['pairs'])} paires · {', '.join(CONFIG['timeframes'])} · Score min: {CONFIG['min_confluence_score']}")

    # Démarrer le thread clavier
    kb_thread = threading.Thread(target=_keyboard_listener, daemon=True)
    kb_thread.start()

    seen = set(); scan_count = 0
    next_scan = time.time()  # Scan immédiat au démarrage

    while not _quit.is_set():
        now = time.time()

        # Scan si c'est l'heure OU si l'utilisateur a appuyé sur ENTRÉE
        if now >= next_scan or _force_scan.is_set():
            _force_scan.clear()
            if _quit.is_set(): break

            scan_count += 1

            # Récupérer le calendrier économique une fois par scan
            log.info("📰 Récupération calendrier économique...")
            events = fetch_economic_calendar()
            log.info(f"  → {len(events)} événements trouvés")

            seen, new_count = do_scan(seen, scan_count, events)

            if len(seen) > 1000: seen = set(list(seen)[-500:])

            next_scan = time.time() + CONFIG["scan_interval"]
            remaining = CONFIG["scan_interval"]
            log.info(f"⏳ Prochain scan automatique dans {remaining//60:.0f}min — Appuie sur ENTRÉE pour scanner maintenant")

        else:
            # Afficher le countdown toutes les 5 minutes
            remaining = int(next_scan - time.time())
            if remaining % 300 == 0 and remaining > 0:
                log.info(f"⏳ Prochain scan dans {remaining//60:.0f}min · ENTRÉE = scanner maintenant")
            time.sleep(1)

if __name__ == "__main__":
    run()
