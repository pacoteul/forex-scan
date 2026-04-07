CONFIG = {

    # ─── TELEGRAM ────────────────────────────────────────────────────────────
    "telegram_token":   "8583648954:AAGjk4k_nMUm3a4V9qofTBtx6apcjJsPKb0",
    "telegram_chat_id": "1194802438",

    # ─── TWELVE DATA ─────────────────────────────────────────────────────────
    "twelvedata_key": "ccbed7d4da2d4745bc380e2a63d9be6d",

    # ─── PAIRES ──────────────────────────────────────────────────────────────
    "pairs": [
        "EUR/USD", "EUR/JPY", "EUR/CAD", "EUR/GBP", "EUR/CHF",
        "USD/JPY", "USD/CAD", "USD/CHF",
        "CAD/JPY", "CAD/CHF",
        "CHF/JPY",
        "AUD/USD", "AUD/JPY", "AUD/CAD", "AUD/CHF",
        "GBP/USD", "GBP/JPY", "GBP/CHF",
    ],

    # ─── TIMEFRAMES ──────────────────────────────────────────────────────────
    # D1 + H4 + H1 + M15
    # 18 paires x 4 TF = 72 req/scan
    # 800 req/jour gratuit = 11 scans/jour max
    "timeframes": ["D1", "H4", "H1", "M15"],

    # ─── SCORE MINIMUM ───────────────────────────────────────────────────────
    # 75 = haute qualite (recommande)
    "min_confluence_score": 75,

    # ─── TIMING ──────────────────────────────────────────────────────────────
    # 3600s = 1h entre chaque scan
    # Sessions actives: 9h-17h heure Paris
    # Hors session = scan tourne mais aucun signal envoye
    "scan_interval": 3600,

    "send_summary": False,
}
