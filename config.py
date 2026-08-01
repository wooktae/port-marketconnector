import os

APP_KEY = os.environ["KIS_APP_KEY"]
APP_SECRET = os.environ["KIS_APP_SECRET"]
BASE_URL = os.environ.get("KIS_BASE_URL", "https://openapivts.koreainvestment.com:29443")

PAPER_ACNT = os.environ["KIS_PAPER_ACNT"]
ACNT_PRDT_CD = os.environ.get("KIS_ACNT_PRDT_CD", "01")
