import argparse
import json
import sys
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.config import load_settings
from app.credential_contracts import CredentialCreate
from app.persistence.credential_repository import CredentialRepository
from app.persistence.database import Database
from app.persistence.errors import RepositoryError


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage FableAI service credentials using local database access.")
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue-admin", help="Issue an administrator credential and display its secret once.")
    issue.add_argument("--name", required=True)
    for name in ("revoke", "rotate"):
        command = commands.add_parser(name)
        command.add_argument("credential_id")
    args = parser.parse_args(arguments)
    database = None
    try:
        settings = load_settings()
        database = Database(settings.database_url.get_secret_value() if settings.database_url else settings.database_path)
        database.initialize()
        repository = CredentialRepository(database)
        if args.command == "issue-admin":
            result = repository.issue_admin(CredentialCreate(name=args.name))
        elif args.command == "rotate":
            result = repository.rotate(UUID(args.credential_id))
        else:
            repository.revoke(UUID(args.credential_id))
            print(json.dumps({"credential_id": str(args.credential_id), "revoked": True}))
            return 0
        print(result.model_dump_json())
        return 0
    except RepositoryError as error:
        print(error.error.message, file=sys.stderr)
        return 1
    except (SQLAlchemyError, ValidationError, RuntimeError, ValueError):
        print("Credential management failed. Check the input, backend database configuration, and migrations.", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
