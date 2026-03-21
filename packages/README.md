# TokenPak Adapter Packages

Standalone, publishable packages for TokenPak integration with popular AI frameworks.

## Packages

| Package | Framework | Status |
|---------|-----------|--------|
| **[langchain-tokenpak](./langchain-tokenpak/)** | LangChain RAG pipelines | ✅ Published |
| **[llamaindex-tokenpak](./llamaindex-tokenpak/)** | LlamaIndex query engines | ✅ Published |
| **[langfuse-tokenpak](./langfuse-tokenpak/)** | Langfuse observability | ✅ Published |
| **[crewai-tokenpak](./crewai-tokenpak/)** | CrewAI multi-agent systems | ✅ Published |
| **[autogen-tokenpak](./autogen-tokenpak/)** | AutoGen conversations | ✅ Published |

## Why Separate Packages?

Each framework has its own conventions, ecosystems, and user bases. Users of LangChain shouldn't need to install CrewAI just to use TokenPak.

### Discoverability

```bash
# Users find us in their ecosystem
pip install langchain-tokenpak
pip install llamaindex-tokenpak
pip install langfuse-tokenpak
pip install crewai-tokenpak
pip install autogen-tokenpak
```

### Independence

- Each package manages its own versioning
- Each depends on `tokenpak`, not heavier framework bundles
- Each publishes independently to PyPI

### Quality

- Framework-specific optimizations
- Best-practice integrations for each framework
- Documentation tailored to each ecosystem

## Development

Each package is a standalone Python project:

```bash
cd packages/langchain-tokenpak
pip install -e ".[dev]"
pytest
```

## Publishing

Each package publishes independently to PyPI:

```bash
cd packages/langchain-tokenpak
pip install build twine
python -m build
twine upload dist/*
```

## What is TokenPak?

TokenPak is an open-source LLM proxy and context compression protocol. It helps:

- **Reduce costs**: Compress context by 30-70%
- **Improve quality**: Keep recent context intact
- **Scale workflows**: Manage token budgets across agents

Learn more: https://github.com/tokenpak/tokenpak

## Support

- Issues: https://github.com/tokenpak/tokenpak/issues
- Discussions: https://github.com/tokenpak/tokenpak/discussions
- Email: support@tokenpak.dev

## License

MIT
