import sys
import os
import json
from typing import Generator

sys.path.insert(0, os.path.abspath("."))
from backend.agents import CLIAgent

def test_codex():
    agent = CLIAgent("codex")
    prompt = "let codex find the problem in LeadAgent"
    print(f"Running codex via CLIAgent.execute_stream('{prompt}')")
    
    # We want to see EXACTLY what is yielded, including whitespace
    for chunk in agent.execute_stream(prompt, cwd=".", session_id="test", mode="plan"):
        print(f"YIELDED: {repr(chunk)}")

if __name__ == "__main__":
    test_codex()
