"""
FOREX SCANNER — Edition Unifiée Multi-Timeframe
Un seul signal par paire avec analyse globale D1+H4+H1
"""
import pandas as pd
import numpy as np
import requests
import time
import logging
import json
import threading
from datetime import datetime, timezone
from typing import Optional
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

TF_MAP = {"H1":"1h","H4":"4h","D1":"1day"}

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
        log.debug(f"TD echoue: {e}")
        return None

def fetch(pair, tf):
    data = td("time_series", {"symbol":pair,"interval":TF_MAP[tf],"outputsize":100,"format":"JSON"})
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
        return df if len(df)>=30 else None
    except Exception as e:
        log.debug(f"fetch parse {pair} {tf}: {e}")
        return None

# ─── INDICATEURS ──────────────────────────────────────────────────────────────
def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def sma(s,n): return s.rolling(n).mean()

def rsi_calc(s, n=14):
    d=s.diff()
    g=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean()
    l=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    return 100-100/(1+g/l.replace(0,np.nan))

def atr_calc(df, n=14):
    tr=pd.concat([df["high"]-df["low"],
                  (df["high"]-df["close"].shift()).abs(),
                  (df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean()

def macd_calc(s):
    ml=ema(s,12)-ema(s,26); sl=ema(ml,9)
    return ml,sl,ml-sl

def stoch_calc(df,k=14,d=3):
    lo=df["low"].rolling(k).min(); hi=df["high"].rolling(k).max()
    kl=100*(df["close"]-lo)/(hi-lo+1e-10)
    return kl, kl.rolling(d).mean()

def pip_value(pair, price):
    """Calcule la valeur d'un pip selon la paire."""
    jpy_pairs = ["JPY"]
    if any(j in pair for j in jpy_pairs):
        return 100   # JPY pairs: 1 pip = 0.01
    return 10000     # Autres: 1 pip = 0.0001

# ─── DETECTION SETUPS SUR UN TF ───────────────────────────────────────────────
def detect_tf(df):
    """Detecte tous les setups sur un dataframe. Retourne liste."""
    results = []
    if df is None or len(df) < 30: return results

    c   = df.iloc[-1]
    p   = df.iloc[-2] if len(df)>1 else None
    rng = c["high"]-c["low"]
    rsi_v = rsi_calc(df["close"]).iloc[-1]
    e20 = ema(df["close"],20).iloc[-1]
    e50 = ema(df["close"],50).iloc[-1]
    _,_,hist = macd_calc(df["close"])
    k_s,d_s = stoch_calc(df)

    # PIN BAR
    if rng > 1e-7:
        body  = abs(c["close"]-c["open"])
        upper = c["high"]-max(c["open"],c["close"])
        lower = min(c["open"],c["close"])-c["low"]
        if lower/rng > 0.60 and body/rng < 0.35:
            results.append({"setup":"Pin Bar","dir":"BUY","strength":round(lower/rng*100)})
        if upper/rng > 0.60 and body/rng < 0.35:
            results.append({"setup":"Pin Bar","dir":"SELL","strength":round(upper/rng*100)})

    # ENGULFING
    if p is not None:
        cb=abs(c["close"]-c["open"]); pb=abs(p["close"]-p["open"])
        if pb>1e-7:
            r=round(cb/pb,1)
            if p["close"]<p["open"] and c["close"]>c["open"] and c["open"]<=p["close"] and c["close"]>=p["open"] and cb>pb:
                results.append({"setup":"Engulfing","dir":"BUY","strength":min(95,int(r*45))})
            if p["close"]>p["open"] and c["close"]<c["open"] and c["open"]>=p["close"] and c["close"]<=p["open"] and cb>pb:
                results.append({"setup":"Engulfing","dir":"SELL","strength":min(95,int(r*45))})

    # BREAKOUT
    if len(df)>=22:
        prev=df.iloc[-22:-1]
        res=prev["high"].max(); sup=prev["low"].min()
        if c["close"]>res and c["close"]>c["open"]:
            results.append({"setup":"Breakout","dir":"BUY","strength":75})
        if c["close"]<sup and c["close"]<c["open"]:
            results.append({"setup":"Breakout","dir":"SELL","strength":75})

    # MACD CROSS
    if len(hist)>2 and not hist.isna().iloc[-2]:
        if hist.iloc[-2]<0 and hist.iloc[-1]>0:
            results.append({"setup":"MACD Cross","dir":"BUY","strength":70})
        if hist.iloc[-2]>0 and hist.iloc[-1]<0:
            results.append({"setup":"MACD Cross","dir":"SELL","strength":70})

    # RSI EXTREME
    if rsi_v < 30:
        results.append({"setup":"RSI Extreme","dir":"BUY","strength":72})
    if rsi_v > 70:
        results.append({"setup":"RSI Extreme","dir":"SELL","strength":72})

    # STOCH CROSS
    if not k_s.isna().iloc[-2]:
        if k_s.iloc[-2]<d_s.iloc[-2] and k_s.iloc[-1]>d_s.iloc[-1] and k_s.iloc[-1]<35:
            results.append({"setup":"Stoch Cross","dir":"BUY","strength":68})
        if k_s.iloc[-2]>d_s.iloc[-2] and k_s.iloc[-1]<d_s.iloc[-1] and k_s.iloc[-1]>65:
            results.append({"setup":"Stoch Cross","dir":"SELL","strength":68})

    # EMA TREND
    if e20>e50 and p is not None and p["close"]<p["open"] and c["close"]>c["open"]:
        results.append({"setup":"EMA Trend","dir":"BUY","strength":65})
    if e20<e50 and p is not None and p["close"]>p["open"] and c["close"]<c["open"]:
        results.append({"setup":"EMA Trend","dir":"SELL","strength":65})

    return results

# ─── ANALYSE UNIFIEE PAR PAIRE ────────────────────────────────────────────────
def analyze_pair(pair):
    """
    Analyse une paire sur D1+H4+H1 et retourne UN SEUL signal unifié.
    """
    timeframes = CONFIG["timeframes"]
    min_score  = CONFIG["min_confluence_score"]

    # ── Récupérer les données de tous les TF ──
    tf_data = {}
    for tf in timeframes:
        df = fetch(pair, tf)
        if df is not None:
            tf_data[tf] = df

    if not tf_data: return None

    # ── Analyser chaque TF ──
    TF_WEIGHT = {"D1":5, "H4":3, "H1":2}
    buy_score  = 0
    sell_score = 0
    tf_summary = []
    all_setups_buy  = []
    all_setups_sell = []

    for tf in ["D1","H4","H1"]:
        if tf not in tf_data: continue
        df = tf_data[tf]
        detections = detect_tf(df)

        tf_buys  = [d["setup"] for d in detections if d["dir"]=="BUY"]
        tf_sells = [d["setup"] for d in detections if d["dir"]=="SELL"]
        w = TF_WEIGHT.get(tf,1)

        for d in detections:
            if d["dir"]=="BUY":
                buy_score  += d["strength"] * w
                all_setups_buy.append((tf, d["setup"], d["strength"]))
            else:
                sell_score += d["strength"] * w
                all_setups_sell.append((tf, d["setup"], d["strength"]))

        # Résumé pour ce TF
        if tf_buys or tf_sells:
            buy_str  = ", ".join(tf_buys)  if tf_buys  else "—"
            sell_str = ", ".join(tf_sells) if tf_sells else "—"
            if tf_buys and tf_sells:
                tf_summary.append(f"⚡ {tf}: BUY({buy_str}) / SELL({sell_str})")
            elif tf_buys:
                tf_summary.append(f"▲ {tf}: {buy_str}")
            else:
                tf_summary.append(f"▼ {tf}: {sell_str}")
        else:
            tf_summary.append(f"➖ {tf}: aucun setup")

        # Bonus tendance EMA
        e20 = ema(df["close"],20).iloc[-1]
        e50 = ema(df["close"],50).iloc[-1]
        if e20>e50: buy_score  += 10*w
        else:       sell_score += 10*w

    # ── Direction gagnante ──
    if buy_score == 0 and sell_score == 0: return None
    direction = "BUY" if buy_score >= sell_score else "SELL"
    winning_setups = all_setups_buy if direction=="BUY" else all_setups_sell

    if not winning_setups: return None

    # ── Setup principal = TF le plus élevé qui a un signal ──
    main_tf    = None
    main_setup = None
    main_detail = ""
    for tf in ["D1","H4","H1"]:
        matches = [(s,st) for (s,st,_) in winning_setups if s==tf]
        if matches:
            main_tf    = tf
            main_setup = matches[0][1]
            break

    if not main_tf: return None

    # ── Score final normalisé ──
    max_possible = sum(100 * TF_WEIGHT.get(tf,1) for tf in tf_data) + sum(10*TF_WEIGHT.get(tf,1) for tf in tf_data)
    raw_score = max(buy_score, sell_score)
    total_score = min(100, round(raw_score / max(max_possible,1) * 100))

    if total_score < min_score: return None

    # ── Indicateurs du TF principal ──
    df_main = tf_data[main_tf]
    price   = df_main["close"].iloc[-1]
    atr_v   = atr_calc(df_main).iloc[-1]
    rsi_v   = rsi_calc(df_main["close"]).iloc[-1]
    e20     = ema(df_main["close"],20).iloc[-1]
    e50     = ema(df_main["close"],50).iloc[-1]
    dec     = 3 if price>10 else 5

    # ── Niveaux SL/TP avec correction JPY ──
    pip_mult = pip_value(pair, price)

    if direction=="BUY":
        sl  = round(price - atr_v*1.5, dec)
        tp1 = round(price + atr_v*1.0, dec)
        tp2 = round(price + atr_v*2.0, dec)
        tp3 = round(price + atr_v*3.0, dec)
    else:
        sl  = round(price + atr_v*1.5, dec)
        tp1 = round(price - atr_v*1.0, dec)
        tp2 = round(price - atr_v*2.0, dec)
        tp3 = round(price - atr_v*3.0, dec)

    sl_dist = abs(price-sl)
    sl_pips = round(sl_dist * pip_mult, 1)
    rr1 = round(abs(tp1-price)/max(sl_dist,1e-7),2)
    rr2 = round(abs(tp2-price)/max(sl_dist,1e-7),2)
    rr3 = round(abs(tp3-price)/max(sl_dist,1e-7),2)

    # ── Confluences ──
    confluences = []
    if e20>e50 and direction=="BUY":  confluences.append("EMA20>50 tendance haussiere")
    if e20<e50 and direction=="SELL": confluences.append("EMA20<50 tendance baissiere")
    _,_,hist = macd_calc(df_main["close"])
    if not hist.isna().iloc[-1]:
        if direction=="BUY"  and hist.iloc[-1]>0: confluences.append("MACD positif")
        if direction=="SELL" and hist.iloc[-1]<0: confluences.append("MACD negatif")
    if direction=="BUY"  and rsi_v<45: confluences.append(f"RSI bas {rsi_v:.0f}")
    if direction=="SELL" and rsi_v>55: confluences.append(f"RSI haut {rsi_v:.0f}")

    # Nombre de TF alignés
    aligned_tfs = [tf for (tf,_,_) in winning_setups]
    n_aligned = len(set(aligned_tfs))

    return {
        "pair":        pair,
        "direction":   direction,
        "main_tf":     main_tf,
        "main_setup":  main_setup,
        "score":       total_score,
        "n_aligned":   n_aligned,
        "tf_summary":  "\n".join(tf_summary),
        "confluences": confluences,
        "entry":       round(price, dec),
        "sl":          sl,
        "tp1":         tp1,
        "tp2":         tp2,
        "tp3":         tp3,
        "rr1":         rr1,
        "rr2":         rr2,
        "rr3":         rr3,
        "sl_pips":     sl_pips,
        "rsi":         round(rsi_v,1),
        "atr":         round(atr_v, dec),
        "trend":       "HAUSSIER" if e20>e50 else "BAISSIER",
        "session":     get_session(),
        "time":        datetime.now().strftime("%H:%M:%S"),
        "timestamp":   datetime.now().isoformat(),
    }

# ─── SESSION ──────────────────────────────────────────────────────────────────
def get_session():
    h=datetime.now(timezone.utc).hour
    if 9<=h<12:  return "London"
    if 12<=h<14: return "London/NY Overlap"
    if 14<=h<17: return "New York"
    if 7<=h<9:   return "Pre-London"
    if 17<=h<20: return "NY Late"
    return "Tokyo/Calme"

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
def send_telegram(sig):
    token   = CONFIG.get("telegram_token","")
    chat_id = CONFIG.get("telegram_chat_id","")
    if not token or token=="TON_TOKEN_ICI": return

    score  = sig["score"]
    stars  = "⭐"*(1 if score<65 else 2 if score<80 else 3)
    emoji  = "🟢" if sig["direction"]=="BUY" else "🔴"
    trend_e= "📈" if sig["trend"]=="HAUSSIER" else "📉"
    facts  = "\n".join(f"✓ {f}" for f in sig["confluences"]) or "—"
    aligned= f"{sig['n_aligned']}/{len(CONFIG['timeframes'])} TF alignés"

    msg = f"""╔══ {stars} {sig['direction']} · {sig['pair']} {stars} ══╗

{emoji} *{sig['pair']}* · {sig['session']}
📐 *Setup principal:* {sig['main_setup']} sur `{sig['main_tf']}`
{trend_e} *Tendance:* {sig['trend']} · {aligned}

📊 *ANALYSE MULTI-TIMEFRAME*
{sig['tf_summary']}

💹 *NIVEAUX*
┌ 🎯 Entree:      `{sig['entry']}`
├ 🛑 Stop Loss:   `{sig['sl']}` ({sig['sl_pips']} pips)
├ ✅ TP1 (R:{sig['rr1']}): `{sig['tp1']}`
├ ✅ TP2 (R:{sig['rr2']}): `{sig['tp2']}`
└ ✅ TP3 (R:{sig['rr3']}): `{sig['tp3']}`

📈 *INDICATEURS*
• RSI: `{sig['rsi']}` · ATR: `{sig['atr']}`

🎯 *Score global: {score}/100*

🔍 *Confluences:*
{facts}

⏰ {sig['time']}
╚══════════════════════════╝"""

    try:
        r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id":chat_id,"text":msg,"parse_mode":"Markdown",
                  "disable_web_page_preview":True},timeout=10)
        if r.status_code==200:
            log.info(f"  Telegram OK → {sig['pair']} {sig['direction']} score:{score}")
        else:
            log.warning(f"  Telegram {r.status_code}: {r.text[:80]}")
    except Exception as e:
        log.warning(f"  Telegram echoue: {e}")

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
    sess=get_session()
    print(f"\n{'='*55}")
    log.info(f"SCAN #{n} — {datetime.now().strftime('%d/%m %H:%M:%S')} — {sess}")
    print(f"{'='*55}")

    t0=time.time(); new=0

    for i,pair in enumerate(CONFIG["pairs"]):
        log.info(f"  [{i+1}/{len(CONFIG['pairs'])}] {pair}...")
        sig = analyze_pair(pair)

        if sig is None:
            log.info(f"    → Aucun signal")
            continue

        key=f"{pair}|{sig['direction']}|{datetime.now().strftime('%Y%m%d%H')}"
        if key in seen:
            log.info(f"    → Signal deja envoye")
            continue

        seen.add(key); new+=1
        score=sig["score"]
        bar="█"*(score//10)+"░"*(10-score//10)
        col="\033[92m" if sig["direction"]=="BUY" else "\033[91m"
        print(f"""
  ╔══════════════════════════════════════════╗
  ║ {col}{sig['direction']:4}\033[0m | {pair:8} | {sig['main_tf']:3} | {sig['main_setup']}
  ║ Score [{bar}] {score}/100
  ║ TFs: {sig['tf_summary'].split(chr(10))[0]}
  ║ Entree:{sig['entry']} SL:{sig['sl']} ({sig['sl_pips']}pips)
  ╚══════════════════════════════════════════╝""")

        send_telegram(sig)
        with open("signals_log.jsonl","a",encoding="utf-8") as f:
            f.write(json.dumps(sig,ensure_ascii=False)+"\n")

    elapsed=time.time()-t0
    log.info(f"Scan #{n} termine en {elapsed:.0f}s · {new} nouveau(x) signal(s)")
    return seen

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def run():
    print("\033[36m  FOREX SCANNER — Edition Unifiee Multi-Timeframe\033[0m")
    print("  ENTREE = scanner maintenant | q + ENTREE = quitter\n")
    log.info(f"Demarre — {len(CONFIG['pairs'])} paires · {', '.join(CONFIG['timeframes'])} · Score min: {CONFIG['min_confluence_score']}")

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
            log.info(f"Prochain scan dans {mins}min — ENTREE pour scanner maintenant")
        else:
            time.sleep(1)

if __name__=="__main__":
    run()
