import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from app.agent import Agent

agent = Agent()
print('Agent created')
try:
    events = list(agent.step('hello'))
    print(f'Number of events: {len(events)}')
    for e in events:
        print(f'  {e.type}: {e.tool if hasattr(e, "tool") else ""} {getattr(e, "target", "")}')
        if e.type == "agent_message":
            print(f'    Message: {e.message}')
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
