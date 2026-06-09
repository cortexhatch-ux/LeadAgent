import os
import threading
import logging
import requests
import json
import re
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load tokens from environment
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")
BACKEND_URL = os.environ.get("LEADAGENT_BACKEND_URL", "http://backend:8000")

# Marker prefixes
PREFIX_ROUND = "__DEBATE_ROUND__"
PREFIX_AGENT = "__DEBATE_AGENT__"
MARKER_AGENT_END = "__DEBATE_AGENT_END__"
MARKER_UMPIRE = "__DEBATE_UMPIRE__"
MARKER_UMPIRE_END = "__DEBATE_UMPIRE_END__"
MARKER_SYNTHESIS = "__DEBATE_SYNTHESIS__"
MARKER_DONE = "__DEBATE_DONE__"

if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
    logger.warning("SLACK_BOT_TOKEN or SLACK_APP_TOKEN not set. Slack bot will not start.")
    app = None
else:
    app = App(token=SLACK_BOT_TOKEN)

def execute_debate(topic: str, channel_id: str, user_id: str):
    """Call the backend debate API and post updates to Slack."""
    try:
        # Initial status
        ts = app.client.chat_postMessage(
            channel=channel_id,
            text=f"🧠 *LeadAgent Debate:* {topic}\n_Initializing agents..._"
        )["ts"]

        current_round = 0
        current_agent = ""
        agent_buffer = ""
        umpire_buffer = ""
        synthesis_buffer = ""
        
        in_agent = False
        in_umpire = False
        in_synthesis = False

        # Call backend streaming endpoint
        resp = requests.post(
            f"{BACKEND_URL}/debate",
            json={"prompt": topic, "rounds": 3},
            stream=True,
            timeout=1800 # Debates can take a long time
        )
        resp.raise_for_status()

        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            
            # --- Round Start ---
            if line.startswith(PREFIX_ROUND):
                current_round += 1
                app.client.chat_update(
                    channel=channel_id,
                    ts=ts,
                    text=f"🧠 *LeadAgent Debate:* {topic}\n🚀 *Round {current_round} in progress...*"
                )
            
            # --- Agent Position Start ---
            elif line.startswith(PREFIX_AGENT):
                in_agent = True
                agent_buffer = ""
                # Extract agent name: __DEBATE_AGENT__:claude
                parts = line.split(":")
                current_agent = parts[1] if len(parts) > 1 else "Unknown"
                if in_synthesis:
                    synthesis_buffer += f"\n🏆 *CONSENSUS ({current_agent.upper()})*\n"
            
            # --- Agent Position End ---
            elif line.startswith(MARKER_AGENT_END):
                in_agent = False
                if not in_synthesis and agent_buffer.strip():
                    app.client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=ts,
                        text=f"👤 *{current_agent.upper()}*:\n{agent_buffer.strip()}"
                    )
                agent_buffer = ""
            
            # --- Umpire Start ---
            elif line.startswith(MARKER_UMPIRE):
                in_umpire = True
                umpire_buffer = ""

            # --- Umpire End ---
            elif line.startswith(MARKER_UMPIRE_END):
                in_umpire = False
                if umpire_buffer.strip():
                    app.client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=ts,
                        text=f"⚖️ *UMPIRE*:\n{umpire_buffer.strip()}"
                    )

            # --- Synthesis Start ---
            elif line.startswith(MARKER_SYNTHESIS):
                in_synthesis = True
                synthesis_buffer = ""
                app.client.chat_update(
                    channel=channel_id,
                    ts=ts,
                    text=f"🧠 *LeadAgent Debate:* {topic}\n⚖️ *Synthesizing final consensus...*"
                )

            # --- Done ---
            elif line.startswith(MARKER_DONE):
                logger.info("Debate stream marked as DONE")
                break
            
            # --- Content Accumulation ---
            else:
                if in_synthesis:
                    synthesis_buffer += line + "\n"
                elif in_agent:
                    agent_buffer += line + "\n"
                elif in_umpire:
                    umpire_buffer += line + "\n"

        # Final Summary Post
        app.client.chat_update(
            channel=channel_id,
            ts=ts,
            text=f"🧠 *LeadAgent Debate:* {topic}\n✅ *Debate Complete*"
        )
        
        # Post the full synthesis as the final word
        if synthesis_buffer.strip():
            logger.info("Posting final synthesis")
            app.client.chat_postMessage(
                channel=channel_id,
                thread_ts=ts,
                text=synthesis_buffer.strip()
            )
        else:
            logger.warning("Synthesis buffer was empty at end of debate")

    except Exception as e:
        logger.error(f"Debate execution failed: {e}")
        if app:
            app.client.chat_postMessage(channel=channel_id, text=f"❌ Debate failed: {e}")

if app:
    @app.command("/debate")
    def handle_debate_command(ack, command, respond):
        logger.info(f"Received /debate command from user {command.get('user_id')} in channel {command.get('channel_id')}")
        ack()
        topic = command.get("text")
        if not topic:
            logger.info("No topic provided in /debate command")
            respond("Please provide a topic: `/debate Should we use React or Vue?`")
            return
        
        user_id = command.get("user_id")
        channel_id = command.get("channel_id")
        
        logger.info(f"Starting debate thread for topic: {topic}")
        threading.Thread(
            target=execute_debate, 
            args=(topic, channel_id, user_id),
            daemon=True
        ).start()

    @app.event("app_mention")
    def handle_mentions(event, say):
        logger.info(f"Received app_mention from user {event.get('user')} in channel {event.get('channel')}")
        text = event.get("text", "")
        if "debate" in text.lower():
            topic = text.lower().split("debate")[-1].strip()
            if topic:
                user_id = event.get("user")
                channel_id = event.get("channel")
                logger.info(f"Starting debate thread for topic: {topic}")
                threading.Thread(
                    target=execute_debate, 
                    args=(topic, channel_id, user_id),
                    daemon=True
                ).start()
                say(f"Understood. Starting a debate on: *{topic}*")
            else:
                say("Mention me with 'debate <topic>' to start a multi-agent session.")
        else:
            say("I am LeadAgent. Try `@LeadAgent debate <topic>` or use the `/debate` command.")

if __name__ == "__main__":
    if app and SLACK_APP_TOKEN:
        logger.info("Starting LeadAgent Slack Bot (Socket Mode)...")
        # Quick health check of the backend
        try:
            r = requests.get(f"{BACKEND_URL}/health", timeout=5)
            logger.info(f"Backend health check: {r.status_code} {r.json().get('status')}")
        except Exception as e:
            logger.error(f"Backend unreachable at {BACKEND_URL}: {e}")

        handler = SocketModeHandler(app, SLACK_APP_TOKEN)
        handler.start()
    else:
        logger.error("Slack bot configuration missing (Tokens).")
