# ADR: VektorCode Computer Use & Browser Automation Architecture

## Context

VektorCode is currently a code-centric AI agent with four tool capabilities:
- Filesystem (read/write/list)
- Shell (command execution)
- Code search (grep-like regex search)
- Configuration (get_config)

The product goal is to evolve VektorCode into a UI-aware agent capable of:
1. Launching and observing web applications
2. Interacting with browser UI (forms, buttons, navigation)
3. Observing screenshots for visual verification
4. (Future) Interacting with desktop/GUI applications

Users will issue commands like:
- "Check whether the login page works"
- "Verify the dashboard renders correctly"
- "Test the registration flow"

## Problem

The current tool set cannot:
- Launch a local application and wait for readiness
- Open a browser and navigate to URLs
- Inspect DOM or accessibility trees
- Take screenshots
- Verify UI state after actions
- Interact with forms (click, type, select)

Adding browser/computer capabilities requires:
- Separate tool interfaces (browser ≠ desktop)
- A permission model (higher risk than file reading)
- Capability detection for models (not all support vision)
- Secure handling of screenshots and secrets
- Bounded tool-use loops

## Goals

- Browser automation as first-class tool capability
- Separate browser and computer-use tool adapters
- Permission-protected tools with explicit policy
- Graceful degradation for non-vision models
- Integration with existing agent loop, CLI, and LiteLLM
- No rewrite of existing functionality

## Non-Goals (out of scope)

- Full desktop computer automation (Phase 4+)
- Remote/hotel cloud-based browser automation
- Visual regression diff engine (Phase 6+)
- MCP integration (evaluated but deferred)
- Enterprise central policy server (deferred)

## Options Considered

### Option A: Playwright-native (SELECTED)

**Browser:** Use Playwright for all browser operations.
**Computer:** Use Playwright's desktop interaction + msscreenshot capture.

| Criterion        | Playwright |
|------------------|------------|
| Browser support  | Chromium, Firefox, WebKit |
| Screenshots      | ✓ Built-in |
| DOM/AX tree      | ✓ Full access |
| Reliability      | ✓ Excellent |
| Headless/Headed  | ✓ Both |
| Licensing        | MIT |
| CI support       | ✓ Excellent |
| Enterprise       | ✓ Good |
| Size             | ~50MB (downloads browsers) |
| Python           | ✓ Official |

### Option B: Selenium

| Criterion        | Selenium |
|------------------|----------|
| Browser support  | Chrome, Firefox, Safari |
| Screenshots      | ✓ Built-in |
| DOM/AX tree      | Partial |
| Reliability      | Moderate |
| Licensing        | Apache 2.0 |
| CI support       | ✓ Good |
| Python           | ✓ Official |

### Option C: Direct CDP (Chrome DevTools Protocol)

| Criterion        | CDP |
|------------------|-----|
| Browser support  | Chromium only |
| Screenshots      | ✓ Via CDP |
| DOM/AX tree      | ✓ Full |
| Reliability      | Good (unstable protocol) |
| Complexity       | High (raw protocol) |

### Option D: MCP Browser Tools

| Criterion        | MCP Browser |
|------------------|-------------|
| Browser support  | Via MCP server |
| Integration      | Requires MCP transport layer |
| Complexity       | High (extra dependency) |
| Vendor lock-in   | Moderate |

### Option E: browser-use style framework

| Criterion        | browser-use |
|------------------|-------------|
| Abstraction      | High-level agent wrapper |
| Control          | Moderate (framework dictates loop) |
| Customization    | Low (hard to integrate) |

## Decision

**Select Playwright as the primary browser automation library.**

Rationale:
1. Best Python support and developer experience
2. Built-in screenshot, DOM, accessibility tree, network interception
3. Reliable, well-maintained, MIT license
4. Headless and headed modes
5. Extensible for desktop computer use later (Playwright has desktop extensions)
6. No protocol-level complexity (unlike CDP)

**Separate Browser and Computer as distinct tools.**

Rationale:
- Browser targets web applications with structured APIs (DOM, AX tree)
- Computer targets arbitrary desktop apps with coordinate-based interaction
- Different security profiles and permission requirements
- Different capability detection (browser needs Playwright, computer needs OS-level API)

**Implement foundation in phases.**

Phase 1: Tool interface, permission model, structured ToolResult, capability detection
Phase 2: Browser tool (Playwright)
Phase 3: Agent loop integration
Phase 4: Computer tool
Phase 5: Security policies
Phase 6: CLI UX

## Security Analysis

### Threats
- **Prompt injection:** Webpage content must never override system policy
- **Secret exposure:** Screenshots and logs must not persist passwords/tokens
- **Unrestricted access:** Tools require explicit permissions
- **Destructive actions:** Must require user confirmation
- **Unbounded loops:** Agent must have max iterations, timeouts
- **Network access:** Browser domains restricted by policy

### Mitigations
- Permission model with explicit grants
- Domain allowlist (localhost by default)
- Screenshot ephemeral storage
- Trusted/untrusted content separation
- Structured tool results (not raw text)
- Bounded loops (configurable max steps)

## Consequences

### Positive
- Browser automation enables UI testing workflows
- Separate tools keep concerns clean
- Permission model is extensible to desktop tools
- Graceful degradation for non-vision models
- No breaking changes to existing tools

### Negative
- Playwright downloads browsers (~50MB on first use)
- Vision model requirement adds complexity
- More configuration surface area
- Tests must mock browser/computer tools

## Migration Strategy

1. Add new modules under `app/tools/ui_computer.py` and `app/tools/browser.py`
2. Create `app/permissions.py` for policy engine
3. Create `app/tool_registry.py` for tool registration
4. Add capability detection to agent loop
5. Add `/browser`, `/computer`, `/permissions` CLI commands
6. Add mock tools for testing
7. Incrementally add browser/computer to TOOL_MAP

## Future Considerations

- Visual regression testing (diff screenshots)
- Enterprise policy server
- MCP tool integration
- Cloud-based browser automation
- Multi-model routing (vision vs non-vision)
- Screenshot caching and compression
- Accessibility-first interaction (click by accessible name, not coordinates)
