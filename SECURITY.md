# Security Policy

## Reporting a vulnerability

Please report security issues **privately**. Do not open a public GitHub issue for a
vulnerability.

- Preferred: use GitHub's [private vulnerability reporting](https://github.com/vishbay/cram-ai/security/advisories/new)
  ("Report a vulnerability" under the Security tab).
- Alternative: email the maintainer at **vishbay19@gmail.com** with details and, if possible, a
  reproduction.

You can expect an acknowledgement within a few days. Once a fix is available, we will
coordinate disclosure and credit you if you wish.

## Scope notes

cram-ai is a local-first CLI. The `cram audit` profiler reads agent transcripts that already
exist on your disk and makes no network calls. The context layer (`cram task` / `cram add`) and
some `cram rig` adapters may call an LLM provider using credentials from your environment — cram
does not transmit your code or transcripts anywhere except to the provider you have configured.

Relevant considerations for reports:
- parsing of untrusted transcript/JSONL files,
- handling of API keys / environment variables,
- the MCP server (`cram mcp`) and any tool it exposes.

## Supported versions

Only the latest released version on PyPI receives security fixes.
