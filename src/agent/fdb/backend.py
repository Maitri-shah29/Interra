"""FDB tool adapter: public API declarations only, never scenario/answer files."""
import ast
import importlib.util
import json
from pathlib import Path
import random
import sys

from ..models import ToolSpec


def load_manifest(template_path, effects):
    tree = ast.parse(Path(template_path).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AssistantFnc")
    tools = []
    for node in cls.body:
        if not isinstance(node, ast.AsyncFunctionDef) or not node.decorator_list:
            continue
        description = next((ast.literal_eval(k.value) for d in node.decorator_list
            if isinstance(d, ast.Call) for k in d.keywords if k.arg == "description"), None)
        if description is None:
            continue
        if node.name not in effects:
            raise ValueError(f"Missing reviewed tool effect metadata: {node.name}")
        properties, required = {}, []
        args = node.args.args[1:]
        defaults = [None] * (len(args) - len(node.args.defaults)) + node.args.defaults
        types = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}
        for arg, default in zip(args, defaults):
            if not isinstance(arg.annotation, ast.Name) or arg.annotation.id not in types:
                raise ValueError(f"Unsupported tool annotation for {node.name}.{arg.arg}")
            schema = {"type": types[arg.annotation.id]}
            if default is None:
                required.append(arg.arg)
            else:
                value = ast.literal_eval(default)
                schema["default"] = value
                if value is None:
                    schema["type"] = [schema["type"], "null"]
            properties[arg.arg] = schema
        tools.append(ToolSpec(name=node.name, description=description + "\n" + (ast.get_docstring(node) or ""),
            effect_type=effects[node.name], argument_schema={"type": "object", "properties": properties,
                "required": required, "additionalProperties": False}))
    if set(effects) != {t.name for t in tools}:
        raise ValueError("Tool effect metadata differs from the pinned upstream declarations")
    return tools


class FDBBackend:
    def __init__(self, root, effects_path, clock, seed=0):
        root = Path(root).resolve()
        self.clock, self.rng = clock, random.Random(seed)
        self.counts = {}
        effects = json.loads(Path(effects_path).read_text(encoding="utf-8"))
        self.specs = load_manifest(root / "cascaded_agent.py", effects)
        # These modules only define public mock backends; neither reads test items.
        sys.path.insert(0, str(root))
        try:
            spec = importlib.util.spec_from_file_location("interra_fdb_mock_apis", root / "mock_apis.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(root))
        self.registry = module.MockAPIRegistry(latency_profile="instant")
        if set(self.registry.FUNCTIONS) != {t.name for t in self.specs}:
            raise ValueError("Public mock functions differ from declared tools")

    async def execute(self, name, arguments):
        profile = self.registry.injector._get_profile(name)
        index = self.counts.get(name, 0)
        self.counts[name] = index + 1
        base = profile.fixed_ms if profile.fixed_ms is not None else (
            self.rng.randint(profile.min_ms, profile.max_ms) if profile.jitter else
            (profile.min_ms + profile.max_ms) // 2)
        delay = int(base * profile.progressive_factor ** index)
        if delay:
            await self.clock.sleep(delay / 1000)
        # Pinned upstream mock functions perform only immediate in-memory operations.
        # Equivalent to registry.call, with its time.sleep replaced by cancellable sleep.
        result = self.registry.FUNCTIONS[name](**arguments)
        return result

    async def aclose(self):
        pass
