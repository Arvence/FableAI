from pathlib import Path

import pytest
from dotenv import dotenv_values


@pytest.mark.parametrize("relative", [".env.example", "api/.env.example"])
def test_environment_templates_never_contain_credentials(relative: str) -> None:
    path = Path(__file__).resolve().parents[2] / relative
    for name, value in dotenv_values(path, encoding="utf-8-sig", interpolate=False).items():
        if value and any(word in name.upper() for word in ("KEY", "PASSWORD", "TOKEN", "SECRET")):
            pytest.fail("Credential fields must be blank in " + relative)
