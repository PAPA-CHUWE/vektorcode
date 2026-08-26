# VektorCode

AI coding agent that connects to an OpenAI-compatible LLM endpoint to inspect, modify, execute, test, and debug software projects.

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
LITELLM_BASE_URL=http://litellm.cpt-gpu002.internal:30080
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
