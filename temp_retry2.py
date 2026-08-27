import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from app.agent import Agent

agent = Agent()
print('Agent created')
try:
    events = list(agent.step('list files in the workspace'))
    print(f'Number of events: {len(events)}')
    for i, e in enumerate(events):
        print(f'  Event {i}: {e.type}')
        if hasattr(e, 'tool'):
            print(f'    Tool: {e.tool}')
        if hasattr(e, 'target'):
            print(f'    Target: {e.target}')
        if e.type == 'tool_result':
            print(f'    Result: {e.data if hasattr(e, "data") else ""}')
        if e.type == 'agent_message':
            print(f'    Message: {e.message[:100]}...' if len(e.message) > 100 else f'    Message: {e.message}')
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
