CONFIG = {

    # ─── TELEGRAM ────────────────────────────────────────────────────────────
    "telegram_token":   "8583648954:AAGjk4k_nMUm3a4V9qofTBtx6apcjJsPKb0",
    "telegram_chat_id": "1194802438",

    # ─── TWELVE DATA ─────────────────────────────────────────────────────────
    # Clé gratuite sur twelvedata.com → Dashboard → API Keys
    "twelvedata_key": "ccbed7d4da2d4745bc380e2a63d9be6d",

    # ─── PAIRES ──────────────────────────────────────────────────────────────
    "pairs": [
        "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
        "AUD/USD", "NZD/USD", "USD/CAD",
        "EUR/GBP", "EUR/JPY", "EUR/CAD", "EUR/AUD", "EUR/NZD", "EUR/CHF",
        "GBP/JPY", "GBP/CAD", "GBP/AUD", "GBP/NZD", "GBP/CHF",
        "CAD/JPY", "AUD/JPY", "NZD/JPY", "CHF/JPY",
        "AUD/NZD", "AUD/CAD", "AUD/CHF",
        "NZD/CAD", "NZD/CHF", "CAD/CHF",
        "XAU/USD",
    ],

    # ─── TIMEFRAMES ──────────────────────────────────────────────────────────
    "timeframes": ["H1", "H4", "D1"],

    # ─── SETUPS ACTIFS ───────────────────────────────────────────────────────
    "active_setups": [
        "Pin Bar", "Engulfing", "Breakout", "RSI Div",
        "SMC/BOS", "MACD Cross", "Ichimoku",
        "Pivot", "Fibonacci", "CCI", "VWAP",
    ],

    # ─── FILTRE QUALITÉ ──────────────────────────────────────────────────────
    "min_confluence_score": 70,

    # ─── TIMING ──────────────────────────────────────────────────────────────
    # 29 paires x 3 TF = ~87 appels/scan
    # Plan gratuit Twelve Data = 800 req/jour → max 9 scans/jour
    "scan_interval": 3600,

    "send_summary": False,
}
