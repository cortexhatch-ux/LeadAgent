import subprocess
import json
import os
import sys

# Simulation of what CLIAgent does for Grok
# We need to see the RAW JSONL to see if it uses keys other than 'data'
prompt = "summarize the titanic disaster in one sentence"
# Use the actual Docker command if in Docker mode
cmd = ["docker", "exec", "-i", "leadagent-grok", "grok", "--prompt-file", "/app/leadagent-data/test_prompt.txt", "--output-format", "streaming-json", "--permission-mode", "plan"]

# Create the test prompt file in the mounted volume
with open("test_prompt.txt", "w") as f:
    f.write(prompt)

print(f"Running: {' '.join(cmd)}")
process = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=0
)

for line in process.stdout:
    print(f"STDOUT: {line.strip()}")

for line in process.stderr:
    print(f"STDERR: {line.strip()}")

process.wait()
os.remove("test_prompt.txt")
