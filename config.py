CONFIG = {

    # ─── TELEGRAM ────────────────────────────────────────────────────────────
    "telegram_token":   "8583648954:AAGjk4k_nMUm3a4V9qofTBtx6apcjJsPKb0",
    "telegram_chat_id": "1194802438",

    # ─── TWELVE DATA ─────────────────────────────────────────────────────────
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
    # D1 + H4 + H1 = combinaison pro recommandée
    # 29 paires x 3 TF = 87 requêtes/scan
    # 800 req/jour gratuit → 9 scans/jour → toutes les 2h30
    "timeframes": ["D1", "H4", "H1"],

    # ─── SETUPS ACTIFS ───────────────────────────────────────────────────────
    "active_setups": [
        "Pin Bar", "Engulfing", "Breakout", "RSI Div",
        "SMC/BOS", "MACD Cross", "Ichimoku",
        "Pivot", "Fibonacci", "CCI", "VWAP",
    ],

    # ─── FILTRE QUALITÉ ──────────────────────────────────────────────────────
    "min_confluence_score": 70,

    # ─── TIMING ──────────────────────────────────────────────────────────────
    # 9000s = 2h30 — couvre toutes les sessions importantes
    # Horaires des scans (heure française) :
    # 00h00 - 02h30 - 05h00 - 07h30 - 10h00 - 12h30 - 15h00 - 17h30 - 20h00
    "scan_interval": 9000,

    "send_summary": False,
}
