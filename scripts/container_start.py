import os
import subprocess
import sys

from sqlalchemy.engine import URL


def main() -> int:
    environment = dict(os.environ)
    if not environment.get("FABLEAI_DATABASE_URL"):
        environment["FABLEAI_DATABASE_URL"] = URL.create("postgresql+psycopg", username=environment["POSTGRES_USER"], password=environment["POSTGRES_PASSWORD"], host="postgres", port=5432, database=environment["POSTGRES_DB"]).render_as_string(hide_password=False)
    result = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], env=environment)
    if result.returncode:
        return result.returncode
    os.execve(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"], environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
