"""DB connection configuration helper.

Reads PostgreSQL connection information from environment variables and passes it to
the connector repository. Sensitive values are not recorded in code or documentation;
they are assumed to be injected from the local environment.
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
