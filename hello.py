"""Phase 0 smoke test: ring DEMO_PHONE, say hello, hang up.

Run: uv run python hello.py
Needs: GUAVA_API_KEY, GUAVA_AGENT_NUMBER, DEMO_PHONE in .env
"""

import os

import guava
from dotenv import load_dotenv

load_dotenv()

agent = guava.Agent(
    name="Riley",
    organization="Northgate Financial Services",
    purpose="Connectivity test",
)


@agent.on_call_start
def start(call: guava.Call, event):
    call.hangup("Say 'Hello, this is Riley from Northgate, just testing the line.' Then end the call.")


if __name__ == "__main__":
    guava.logging_utils.configure_logging()
    agent.call_phone(
        from_number=os.environ["GUAVA_AGENT_NUMBER"],
        to_number=os.environ["DEMO_PHONE"],
    )
