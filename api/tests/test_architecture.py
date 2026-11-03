import ast
from pathlib import Path

import pytest


APP = Path(__file__).resolve().parents[1] / "app"


@pytest.mark.parametrize(("pattern", "forbidden"), [
    ("generation/*.py", ("app.persistence", "app.transport", "app.persistent_conversation", "app.main", "fastapi", "starlette", "sqlalchemy")),
    ("persistence/*.py", ("app.generation", "app.transport", "app.persistent_conversation", "app.main", "fastapi", "starlette")),
    ("*contracts.py", ("app.generation", "app.persistence", "app.transport", "app.main", "fastapi", "starlette", "sqlalchemy")),
    ("config.py", ("app.generation", "app.persistence", "app.transport", "app.main")),
])
def test_layer_dependencies(pattern: str, forbidden: tuple[str, ...]) -> None:
    for path in APP.glob(pattern):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        for module in imports:
            assert not any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden), f"{path.name} imports {module}"
