"""Regression: main.py no debe referenciar nombres sin importar/definir.

Al extraer load_config/save_config a `persistencia` se coló un import
borrado (`ListManagerDialog`) y el arranque reventaba con NameError antes
de abrir la ventana. Este test lo detecta sin levantar Qt.
"""
import ast
import builtins
import pathlib


def test_sin_nombres_no_definidos():
    root = pathlib.Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "main.py").read_text(encoding="utf-8"))

    bound = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            bound |= {a.asname or a.name for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            bound.add(node.id)
        elif isinstance(node, (ast.arg,)):
            bound.add(node.arg)

    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
            and isinstance(n.ctx, ast.Load)}
    missing = used - bound
    assert not missing, f"usados sin importar/definir en main.py: {sorted(missing)}"


def test_main_importa_el_dialogo():
    root = pathlib.Path(__file__).resolve().parents[1]
    src = (root / "main.py").read_text(encoding="utf-8")
    assert "from src.infrastructure.ui.list_manager_dialog import ListManagerDialog" in src
