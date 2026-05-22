"""Terminal-fallback onboarding (used when tkinter isn't available)."""
import os
import shutil
import subprocess
import sys

from backend.agents_catalog import AGENTS, AGENT_ORDER, is_authenticated, is_installed


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


class OnboardingManager:
    def check_and_fix_environment(self, interactive: bool | None = None):
        if interactive is None:
            interactive = _interactive()
        
        # ANSI escape codes for "Wow" factor
        CLEAR = "\033[H\033[J"
        BOLD = "\033[1m"
        CYAN = "\033[36m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        RED = "\033[31m"
        GRAY = "\033[90m"
        RESET = "\033[0m"

        BANNER = f"""
{CYAN}{BOLD}    __                   __   ___                             __ 
   / /   ___  ____ _____/ /  /   |  ____ ____  ____  / /_
  / /   / _ \\/ __ `/ __  /  / /| | / __ `/ _ \\/ __ \\/ __/
 / /___/  __/ /_/ / /_/ /  / ___ |/ /_/ /  __/ / / / /_  
/_____/\\___/\\__,_/\\__,_/  /_/  |_|\\__, /\\___/_/ /_/\\__/  
                                 /____/                  {RESET}
    {GRAY}The Universal Orchestrator  •  Environmental Integrity{RESET}
    """

        print(CLEAR + BANNER)
        print(f"{BOLD}Step 1: Core Toolchain{RESET}")
        print(f"{GRAY}────────────────────────────────────────────────────────────{RESET}")

        # agentmemory (optional semantic memory layer)
        if shutil.which("agentmemory"):
            print(f"  {GREEN}● available{RESET}  {BOLD}agentmemory{RESET} {GRAY}(semantic storage){RESET}")
        else:
            print(f"  {YELLOW}○ missing{RESET}    {BOLD}agentmemory{RESET} {GRAY}(context will be limited){RESET}")
            if interactive and self._ask(f"   {CYAN}Install via npm? (y/n):{RESET} "):
                self._npm_install("@agentmemory/agentmemory")

        print(f"\n{BOLD}Step 2: Intelligent Agents{RESET}")
        print(f"{GRAY}────────────────────────────────────────────────────────────{RESET}")

        for key in AGENT_ORDER:
            self._check_agent(key, interactive=interactive)

        print(f"\n{GREEN}{BOLD}✨ Environment synchronization complete.{RESET}")
        print(f"  {GRAY}Type {RESET}{BOLD}leadagent health{RESET}{GRAY} to verify live status at any time.{RESET}\n")

    def _check_agent(self, key: str, interactive: bool):
        BOLD = "\033[1m"
        CYAN = "\033[36m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        RED = "\033[31m"
        GRAY = "\033[90m"
        RESET = "\033[0m"

        spec = AGENTS[key]
        installed = is_installed(key)
        
        if not installed:
            status = f"{RED}○ missing{RESET}"
            detail = f"{GRAY}npm install -g {spec.npm_pkg}{RESET}" if spec.npm_pkg else f"{GRAY}({spec.note}){RESET}"
            print(f"  {status}  {BOLD}{spec.display:<18}{RESET} {detail}")
            
            if spec.npm_pkg and interactive and self._ask(f"   {CYAN}Install {spec.npm_pkg}? (y/n):{RESET} "):
                self._npm_install(spec.npm_pkg)
            return

        authed = is_authenticated(key)
        if authed is True:
            status = f"{GREEN}● available{RESET}"
            detail = f"{GRAY}(active subscription){RESET}"
            print(f"  {status}  {BOLD}{spec.display:<18}{RESET} {detail}")
            return
        
        if authed is False:
            status = f"{YELLOW}○ signed-out{RESET}"
            detail = f"{GRAY}subscription required{RESET}"
            print(f"  {status}  {BOLD}{spec.display:<18}{RESET} {detail}")
        else:
            status = f"{GREEN}● installed{RESET}"
            detail = f"{GRAY}(auth state unknown){RESET}"
            print(f"  {status}  {BOLD}{spec.display:<18}{RESET} {detail}")

        if spec.login_cmd and interactive and self._ask(f"   {CYAN}Sign in to {spec.display} now? (y/n):{RESET} "):
            # Docker awareness: if container is running, run login there
            container_map = {"claude": "leadagent-claude", "gemini": "leadagent-gemini"}
            container = container_map.get(key)
            
            is_docker = False
            if shutil.which("docker") and container:
                try:
                    res = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container], 
                                         capture_output=True, text=True, timeout=2)
                    if res.stdout.strip() == "true":
                        is_docker = True
                except: pass

            if is_docker:
                print(f"   {GRAY}(Redirecting login to container: {container}){RESET}")
                subprocess.run(["docker", "exec", "-it", container] + spec.login_cmd.split())
            else:
                subprocess.run(spec.login_cmd.split())

    @staticmethod
    def _ask(prompt: str) -> bool:
        try:
            return input(prompt).strip().lower().startswith("y")
        except EOFError:
            return False

    @staticmethod
    def _npm_install(package: str):
        prefix = os.path.expanduser("~/.leadagent")
        try:
            subprocess.run(
                ["npm", "install", "-g", "--prefix", prefix, package],
                check=True,
            )
            print(f"   ✅ {package} installed.")
        except Exception as e:
            print(f"   ⚠️  Install failed: {e}. Run manually:")
            print(f"      npm install -g --prefix {prefix} {package}")


onboarding = OnboardingManager()
