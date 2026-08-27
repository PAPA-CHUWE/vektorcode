# VektorCode

AI coding agent that connects to an OpenAI-compatible LLM endpoint to inspect, modify, execute, test, and debug software projects.

## Install

**pipx** is the recommended path. VektorCode runs alongside whatever project
you point it at, so isolating its dependencies avoids conflicts with the
environment it is working in.

```bash
pipx install vektorcode
```

Without pipx:

```bash
pip install --user vektorcode
```

Before it is on PyPI, install straight from the repository:

```bash
pipx install git+https://github.com/PAPA-CHUWE/vektorcode
```

Requires **Python 3.10+**. `git` must be on PATH for the git tool; every
other tool works without it.

## Configure

VektorCode reads `LITELLM_BASE_URL`, `LITELLM_API_KEY` and `LITELLM_MODEL`
from the environment or from a `.env` file. It looks in
`~/.config/vektorcode/.env` first, then the workspace.

```bash
mkdir -p ~/.config/vektorcode
cat > ~/.config/vektorcode/.env <<'ENV'
LITELLM_BASE_URL=http://your-endpoint:30080
LITELLM_API_KEY=your-key
LITELLM_MODEL=nemotron-3-super
ENV
```

Then run `vektorcode` in any project directory, or `vektorcode "a prompt"`
for a single turn.

## Local and Hugging Face models

VektorCode talks to any OpenAI-compatible endpoint, so a locally-served HF
model needs no code changes — only a different base URL.

```bash
ollama serve                       # or any OpenAI-compatible server
ollama pull qwen2.5:7b
```

Then inside VektorCode:

```
/models                 # lists local servers it can reach, and how to switch
/endpoint local         # jumps to VEKTOR_LOCAL_ENDPOINT or a discovered server
/model qwen2.5:7b
```

Set `VEKTOR_LOCAL_ENDPOINT=http://127.0.0.1:11434/v1` in your `.env` so
`/endpoint local` works every session without retyping the URL.

### Tool-calling reliability is the thing that matters

Far more than parameter count or benchmark scores. VektorCode drives every
tool through textual `<tool_call>` blocks, and a model that cannot sustain
that loop is unusable as an agent regardless of how well it writes prose.

Measured on this project, four representative tasks each:

| Model | Serving | Clean runs | Avg / task | Notes |
|---|---|---|---|---|
| `nemotron-3-super` | vLLM (remote) | 3/4 | 10.1s | current default |
| `gemma4:latest` | Ollama (local) | 3/4 | 57.3s | no tool-calling fine-tune; loops on multi-step tasks |
| `gpt-oss-120b` | vLLM (remote) | — | — | emits a tool call ~1/8; needs a tool-call parser enabled server-side |

A "clean run" used at least one tool and hit no loop-detection, iteration
cap or agent error.

What this says: parses single tool calls fine, but sustaining a multi-step
loop is a different capability. gemma4 issued correct `list_files`,
`read_file` and `search_code` calls with correct arguments, then repeated
`list_files` until loop detection stopped it. Local costs 5.7x the latency
for the same success rate, so it is worth it for offline or privacy
reasons, not for quality.

Prefer models with an explicit tool-calling or function-calling fine-tune —
Qwen2.5-Instruct and Hermes-3 are the usual recommendations. Base and plain
instruct models will parse a call and then fail to keep the loop going.

## Project Structure

```
vektor-code-agent/
├── app/
│   ├── __init__.py
│   ├── main.py            # CLI entry point
│   ├── agent.py           # Agent loop with tool orchestration
│   ├── llm.py             # OpenAI-compatible LLM client
│   ├── colors.py          # ANSI color constants
│   ├── commands/          # CLI slash commands
│   │   ├── __init__.py
│   │   ├── config.py      # /auth, /model, /endpoint, /reload, /help
│   │   ├── env_utils.py   # .env read/write helpers
│   │   └── models.py      # /models - list available models
│   └── tools/             # AI-accessible tools
│       ├── __init__.py
│       ├── filesystem.py  # File read/write/list operations
│       ├── terminal.py    # Shell command execution
│       └── config.py      # get_config tool
├── workspace/             # Projects VektorCode operates on
├── tests/                 # Test suite
├── logs/                  # Log directory
├── .env                   # Environment configuration
├── .gitignore
├── requirements.txt
└── README.md
```

## Installation

```bash
cd vektor-code-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment Configuration

Edit `vektor-code-agent/.env` with your LLM endpoint details:

```env
LITELLM_API_KEY=your-api-key
LITELLM_BASE_URL=http://your-endpoint:30080
LITELLM_MODEL=gemma
```

Never commit real API keys to the repository.

## Usage

```bash
cd vektor-code-agent
python -m app.main
```

Then interact naturally:

```
You: List the files in the workspace
VektorCode: [lists files and reports back]

You: Read the main.py file
VektorCode: [reads and summarises the file]

You: Add a health endpoint to this Flask app
VektorCode: [inspects project, makes changes, runs tests, reports]
```

Type `exit` or `quit` to leave.

## Available Tools

| Tool | Description |
|------|-------------|
| `list_files` | List files and directories inside the workspace |
| `read_file` | Read the text contents of a file |
| `write_file` | Write content to a file (creates parent dirs) |
| `run_command` | Execute a shell command in the workspace |
| `get_config` | Show current configuration (model, endpoint, API key) |

## Security

- All file operations are restricted to the `workspace/` directory.
- Path traversal (e.g. `../../`) is blocked.
- Commands execute inside the workspace and time out after 60 seconds.
- No hard-coded credentials.

## Roadmap

- [ ] Code search (grep/ripgrep integration)
- [ ] Git operations
- [ ] Task planning
- [ ] Autonomous fix/test loops
- [ ] Conversation memory
- [ ] Web UI
# vektorcode
