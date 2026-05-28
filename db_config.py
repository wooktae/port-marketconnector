"""DB connection 설정 생성 helper.

PostgreSQL 접속정보를 환경변수에서 읽어 connector repository에 전달한다.
민감정보 값은 코드나 문서에 기록하지 않고 로컬 환경에서 주입하는 것을 전제로 한다.
"""

import os
from typing import Dict


def get_db_config() -> Dict[str, object]:
    password = os.getenv("INTEREST_DB_PASSWORD")
    if not password:
        raise RuntimeError("INTEREST_DB_PASSWORD environment variable is required")

    return {
        "host": os.getenv("INTEREST_DB_HOST", "localhost"),
        "port": int(os.getenv("INTEREST_DB_PORT", "5433")),
        "dbname": os.getenv("INTEREST_DB_NAME", "portfolio"),
        "user": os.getenv("INTEREST_DB_USER", "postgres"),
        "password": password,
        "options": "-c search_path=connector,execution,legacy,reference,public",
    }
