"""Isolate every rehearsal builder except the one under test."""
import ast
import importlib
from unittest.mock import patch


def isolate_builders(stack, rehearsal, target):
    tree = ast.parse(open(rehearsal.__file__, encoding='utf-8').read())
    names = {alias.name for node in ast.walk(tree)
             if isinstance(node, ast.ImportFrom) and node.module == 'console'
             for alias in node.names if alias.name.startswith('v3_')}
    names.update(node.value for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)
                 and node.value.startswith('v3_') and node.value.isidentifier())
    for name in sorted(names - {target}):
        try:
            module = importlib.import_module('console.' + name)
        except ImportError:
            continue  # The rehearsal itself skips absent optional modules.
        stack.enter_context(patch.object(module, 'build', return_value=[]))
