"""KIS access token 파일 처리와 발급/갱신 helper.

token 파일을 읽고 쓰며 KIS token endpoint를 호출할 수 있다.
정적 문서화 작업 중에는 실행하거나 token 값을 출력하지 않는다.
"""

import requests
import json
import os
import time

from config import APP_KEY, APP_SECRET, BASE_URL

TOKEN_FILE = "access_token.txt"

print("[token_manager] 모듈 로드됨")

def save_token(token):
    print("[token_manager] 토큰 파일에 저장")
    with open(TOKEN_FILE, "w") as f:
        f.write(token)

def load_token():
    if os.path.exists(TOKEN_FILE):
        print("[token_manager] 기존 토큰 파일 발견")
        with open(TOKEN_FILE, "r") as f:
            return f.read().strip()
    return None

def delete_token_file():
    if os.path.exists(TOKEN_FILE):
        print("[token_manager] 토큰 파일 제거")
        os.remove(TOKEN_FILE)

def issue_new_token():
    print("[token_manager] 새 토큰 발급 시도")
    url = f"{BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    res = requests.post(url, json=body, headers={"Content-Type": "application/json"})
    print("[token_manager] token API status:", res.status_code)

    try:
        data = res.json()
    except Exception as e:
        print("[token_manager] 토큰 발급 응답 JSON 파싱 실패:", e)
        return None

    token = data.get("access_token")
    if token:
        save_token(token)
        return token

    print("[token_manager] 토큰 발급 실패:", data)
    return None

def get_access_token():
    print("[token_manager] get_access_token 호출됨")

    token = load_token()
    if token:
        # 일단 기존 토큰 쓰되 검증/재발급 루틴 수행
        print("[token_manager] 기존 토큰 재사용")
        return token

    print("[token_manager] 저장된 토큰이 없어서 새로 발급")
    return issue_new_token()

def force_issue_new_token():
    print("[token_manager] 만료 또는 오류로 토큰 재발급 시도")
    delete_token_file()
    return issue_new_token()

# —————— API 호출 중 토큰 오류 체크용 헬퍼 ——————

def check_and_refresh_token(response_text):
    """
    API 응답 텍스트에서 토큰 만료/유효성 오류가 있으면
    토큰 파일 삭제 + 토큰 재발급 후 반환.
    아니면 None 반환.
    """
    text = response_text.lower()
    # 만료 메시지나 인증 오류 메시지 포함돼 있으면
    if "token" in text and ("만료" in text or "expired" in text):
        print("[token_manager] 응답에서 토큰 만료/오류 감지됨:", response_text[:100])
        # 파일 삭제
        delete_token_file()
        # 재발급
        return issue_new_token()
    return None

if __name__ == "__main__":
    t = get_access_token()
    print("[token_manager] token issued:", bool(t))
