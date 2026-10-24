import getpass
import os
import sys
import warnings
from pathlib import Path

from dotenv import dotenv_values, set_key


BACKEND = Path(__file__).resolve().parents[1] / "backend"
SETTINGS = ("GEMINI_API_KEY", "GEMINI_MODEL")


def configure_env(env_path: Path, template_path: Path, *, interactive: bool | None = None) -> None:
    if not env_path.exists():
        template = template_path.read_text(encoding="utf-8")
        descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(template)
        print("Created backend/.env from the blank template.")

    values = dotenv_values(env_path, encoding="utf-8-sig", interpolate=False)
    missing = [name for name in SETTINGS if not (os.environ.get(name, values.get(name)) or "").strip()]
    if not missing:
        print("Backend credentials are already configured.")
        return

    interactive = sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
    if not interactive:
        print("Generation is not configured. Set " + ", ".join(missing) + " in backend/.env or rerun setup in an interactive terminal.")
        return

    print("Enter your own Free Tier verified Gemini key and exact model ID. No API request or account change is made.")
    print("Press Enter to skip a setting; the app can start without generation.")
    for name in missing:
        if name in os.environ:
            print(name + " is empty in the process environment and overrides backend/.env. Unset it or set it before restarting.")
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = (getpass.getpass("GEMINI_API_KEY (hidden): ") if name == "GEMINI_API_KEY" else input("GEMINI_MODEL: ")).strip()
        except (EOFError, getpass.GetPassWarning):
            print("Secure interactive input is unavailable. Edit backend/.env locally.")
            break
        if not value:
            continue
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            print("Skipped " + name + ": the value must be a single line without control characters.")
            continue
        set_key(env_path, name, value, quote_mode="always", encoding="utf-8-sig")
        print("Saved " + name + " in backend/.env.")


if __name__ == "__main__":
    try:
        configure_env(BACKEND / ".env", BACKEND / ".env.example")
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        sys.exit(130)
