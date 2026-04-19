# TokenPak Plugin Guide

TokenPak supports a plugin system that lets you inject custom compression logic into the proxy pipeline. Plugins run after built-in compression and can further transform, filter, or annotate text.

---

## How It Works

1. You create a class that extends `CompressorPlugin`
2. Register it via env var or config file
3. TokenPak discovers and runs it automatically on every request

Plugins are ordered by **priority** (highest runs first). After all plugins run, the final `text` value is used as the compressed output.

---

## Creating a Plugin

### 1. Subclass `CompressorPlugin`

```python
from tokenpak.plugins.base import CompressorPlugin

class MyPlugin(CompressorPlugin):
    name = "my-plugin"           # Unique name — required

    def compress(self, text: str, context: dict) -> dict:
        # Transform text however you like
        cleaned = text.replace("  ", " ")
        return {
            "text": cleaned,
            "metadata": {"plugin": self.name, "chars_removed": len(text) - len(cleaned)}
        }

    def priority(self) -> int:
        return 75  # Default is 50. Higher = runs earlier.
```

### Return Format

`compress()` must return a dict with:

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `text` | `str` | ✅ | The (possibly modified) output text |
| `metadata` | `dict` | ✅ | Arbitrary info about what was done |

---

## Registering a Plugin

### Option A: Environment Variable

Set `TOKENPAK_PLUGINS` to a comma-separated list of dotted import paths:

```bash
export TOKENPAK_PLUGINS="mypackage.plugins.MyPlugin,mypackage.plugins.AnotherPlugin"
```

### Option B: Config File

Create `tokenpak.config.json` in the working directory:

```json
{
  "plugins": [
    "mypackage.plugins.MyPlugin"
  ]
}
```

Both sources are loaded on startup via `PluginRegistry.discover()`. Duplicates (same `name`) raise a `ValueError`.

---

## Priority & Ordering

Plugins run in **descending priority order** (highest first, default 50):

```python
class EarlyPlugin(CompressorPlugin):
    name = "early"
    def priority(self): return 100   # runs first

class LatePlugin(CompressorPlugin):
    name = "late"
    def priority(self): return 10    # runs last
```

Output from earlier plugins is passed as input to later plugins, forming a pipeline.

---

## Example: Whitespace Normalizer

```python
import re
from tokenpak.plugins.base import CompressorPlugin

class WhitespacePlugin(CompressorPlugin):
    name = "whitespace-normalizer"

    def compress(self, text: str, context: dict) -> dict:
        original_len = len(text)
        normalized = re.sub(r'\s+', ' ', text).strip()
        return {
            "text": normalized,
            "metadata": {
                "plugin": self.name,
                "chars_saved": original_len - len(normalized),
            }
        }
```

Register it:

```bash
export TOKENPAK_PLUGINS="mypackage.whitespace_plugin.WhitespacePlugin"
```

---

## Built-in Example Plugin

TokenPak ships with a no-op passthrough plugin you can use as a template:

```
tokenpak/plugins/examples/passthrough.py
```

```python
from tokenpak.plugins.examples.passthrough import PassthroughPlugin
```

---

## Error Handling

If a plugin raises an exception during `compress()`, the pipeline should catch it and fall back to the unmodified text. Plugins should be defensive:

```python
def compress(self, text: str, context: dict) -> dict:
    try:
        result = my_transform(text)
        return {"text": result, "metadata": {"ok": True}}
    except Exception as exc:
        return {"text": text, "metadata": {"ok": False, "error": str(exc)}}
```

---

## Name Collision

Two plugins with the same `name` cannot coexist. Registering a duplicate raises:

```
ValueError: Plugin name collision: 'my-plugin' is already registered
```

Use unique, descriptive names (e.g. `org.mycompany.whitespace-normalizer`).

---

## API Reference

### `CompressorPlugin` (abstract base)

| Member | Type | Description |
|--------|------|-------------|
| `name` | `str` | Unique identifier. Set as class attribute. |
| `compress(text, context)` | abstract method | Core transform. Returns `{text, metadata}`. |
| `priority()` | method | Returns int (default 50). Higher = earlier. |

### `PluginRegistry`

| Method | Description |
|--------|-------------|
| `register(plugin_cls)` | Instantiate and add a plugin class |
| `discover()` | Load from env var + config file |
| `get_plugins()` | Return plugins sorted by priority (desc) |
