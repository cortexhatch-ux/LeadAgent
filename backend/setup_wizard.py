#!/usr/bin/env python3
"""LeadAgent first-time setup wizard.

Falls back to a terminal flow when tkinter isn't installed."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

# Make `backend.agents_catalog` importable whether the wizard is invoked
# directly (`python backend/setup_wizard.py`) or as a module.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.normpath(os.path.join(_HERE, ".."))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from backend.agents_catalog import AGENTS, AGENT_ORDER, is_authenticated, is_installed

DATA_DIR = os.path.join(_BASE, "leadagent-data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
ONBOARD_FLAG = os.path.join(DATA_DIR, ".onboarded")
NPM_PREFIX = "$HOME/.leadagent"

try:
    import tkinter as tk

    _HAS_TK = True
except ImportError:
    _HAS_TK = False


# ── Premium Theme ─────────────────────────────────────────────────────────────
BG = "#FFF1EE"  # Creamy White
SIDEBAR = "#33475B"  # Obsidian/Charcoal
CARD = "#FFFFFF"  # Pure White surfaces
CODE = "#2D3E50"  # Dark Blue for code blocks
ACCENT = "#FF7A59"  # Primary Orange
PURPLE = "#6A78D1"  # Deep Purple
GREEN = "#00BDA5"  # Vibrant Teal
YELLOW = "#F5C26B"  # Warm Marigold
RED = "#F2545B"  # Accent Red
TEXT = "#33475B"  # Obsidian for text
MUTED = "#6B7C93"  # Calmer muted text
WHITE = "#FFFFFF"
MONO = "Menlo" if sys.platform == "darwin" else "Consolas"
SANS = "SF Pro Text" if sys.platform == "darwin" else "Segoe UI"

# High-visibility font sizes
FONT_SIZE_BASE = 14
FONT_SIZE_L = 16
FONT_SIZE_XL = 24
FONT_SIZE_HUGE = 36


def _install_cmd(spec) -> str:
    return f"npm install -g --prefix {NPM_PREFIX} {spec.npm_pkg}"


def _write_onboard_state(selected, projects_dir=None):
    os.makedirs(DATA_DIR, exist_ok=True)
    agents_list = [k for k in AGENT_ORDER if k in selected]

    # Preserve existing projects_dir if not provided
    final_projects_dir = projects_dir
    if not final_projects_dir and os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as fh:
                final_projects_dir = json.load(fh).get("projects_dir")
        except:
            pass

    if not final_projects_dir:
        # Default fallback
        final_projects_dir = os.path.expanduser("~")

    with open(CONFIG_FILE, "w") as fh:
        json.dump(
            {"agents": agents_list, "projects_dir": final_projects_dir}, fh, indent=2
        )
    with open(ONBOARD_FLAG, "w") as fh:
        fh.write("ok\n")


# ── TUI fallback (no tkinter) ────────────────────────────────────────────────


def _tui_wizard():
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
    {GRAY}The Universal Orchestrator  •  Multi-Agent Intelligence{RESET}
    """

    print(CLEAR + BANNER)
    print(f"{BOLD}Step 1: Environment Scan{RESET}")
    print(f"{GRAY}────────────────────────────────────────────────────────────{RESET}")

    for key in AGENT_ORDER:
        spec = AGENTS[key]
        installed = is_installed(key)
        authed = is_authenticated(key)

        # Docker awareness for TUI
        container_map = {"claude": "leadagent-claude", "gemini": "leadagent-gemini"}
        container = container_map.get(key)
        is_docker = False
        if container and shutil.which("docker"):
            try:
                res = subprocess.run(
                    ["docker", "inspect", "-f", "{{.State.Running}}", container],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res.stdout.strip() == "true":
                    is_docker = True
            except:
                pass

        if installed and authed is not False:
            status = f"{GREEN}● available{RESET}"
            detail = f"{GRAY}(subscription detected){RESET}"
        elif installed:
            status = f"{YELLOW}○ signed-out{RESET}"
            login_cmd = spec.login_cmd
            if is_docker and login_cmd:
                login_cmd = f"docker exec -it {container} {login_cmd}"
            detail = f"{GRAY}run: {RESET}{BOLD}{login_cmd}{RESET}"
        else:
            status = f"{RED}○ missing{RESET}"
            detail = (
                f"{GRAY}npm install -g {spec.npm_pkg}{RESET}"
                if spec.npm_pkg
                else f"{GRAY}({spec.note}){RESET}"
            )

        print(f"  {status}  {BOLD}{spec.display:<18}{RESET} {detail}")

    print(f"\n{BOLD}Step 2: Configuration{RESET}")
    print(f"{GRAY}────────────────────────────────────────────────────────────{RESET}")

    try:
        raw = input(
            f"{CYAN}Enable which agents?{RESET} {GRAY}(comma-separated, blank for all):{RESET} "
        ).strip()
    except EOFError:
        raw = ""

    if raw:
        chosen = {k.strip().lower() for k in raw.split(",") if k.strip()}
    else:
        chosen = {k for k in AGENT_ORDER if is_installed(k)}
    if not chosen:
        chosen = {"claude"}

    print()
    default_projects = os.path.expanduser("~")
    try:
        p_dir = input(
            f"{CYAN}Projects workspace directory{RESET} {GRAY}[{default_projects}]:{RESET} "
        ).strip()
    except EOFError:
        p_dir = ""

    if not p_dir:
        p_dir = default_projects
    else:
        p_dir = os.path.expanduser(p_dir)

    _write_onboard_state(chosen, projects_dir=p_dir)

    print(f"\n{GREEN}{BOLD}✨ Setup Complete!{RESET}")
    print(f"  {BOLD}• Agents:{RESET}    {GRAY}{', '.join(sorted(chosen))}{RESET}")
    print(f"  {BOLD}• Workspace:{RESET} {GRAY}{p_dir}{RESET}")
    print(f"\n{BOLD}Next Steps:{RESET}")
    print(f"  1. Run {BOLD}./start_backend.sh{RESET} to launch the daemon.")
    print(f'  2. Type {BOLD}leadagent "Hello"{RESET} to start your first session.\n')


# ── GUI Wizard ───────────────────────────────────────────────────────────────


class SetupWizard:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("LeadAgent Setup")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.geometry("900x680")
        self._center()

        # Pre-fill selection from any previous run + installed CLIs
        self.selected: set = self._initial_selection()
        self.steps: list = []
        self.step_idx: int = 0
        self._agent_cards: dict = {}  # widget refs for live verify

        self._build_chrome()
        self._go_welcome()
        self.root.mainloop()

    def _initial_selection(self) -> set:
        if os.path.exists(CONFIG_FILE):
            try:
                prev = json.load(open(CONFIG_FILE)).get("agents") or []
                if prev:
                    return set(prev)
            except Exception:
                pass
        installed = {k for k in AGENT_ORDER if is_installed(k)}
        return installed or {"claude", "gemini"}

    def _get_docker_cmd(self, agent_key: str, cmd: str) -> str:
        """Wrap cmd in docker exec -it if the agent container is running."""
        container_map = {
            "claude": "leadagent-claude",
            "gemini": "leadagent-gemini",
            "codex": "leadagent-codex",
            "grok": "leadagent-grok",
        }
        container = container_map.get(agent_key)
        if not container or not shutil.which("docker"):
            return cmd

        try:
            res = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.stdout.strip() == "true":
                return f"docker exec -it {container} {cmd}"
        except:
            pass
        return cmd

    def _center(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"900x680+{(sw - 900) // 2}+{(sh - 680) // 2}")

    # ── Chrome ────────────────────────────────────────────────────────────────

    def _build_chrome(self):
        self.sidebar = tk.Frame(self.root, bg=SIDEBAR, width=182)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        tk.Label(
            self.sidebar,
            text="🧠  LeadAgent",
            bg=SIDEBAR,
            fg=WHITE,
            font=(SANS, 12, "bold"),
        ).pack(pady=(26, 2))
        tk.Label(
            self.sidebar, text="Setup Wizard", bg=SIDEBAR, fg=MUTED, font=(SANS, 9)
        ).pack()
        tk.Frame(self.sidebar, bg=MUTED, height=1).pack(fill="x", padx=20, pady=18)
        self.step_list = tk.Frame(self.sidebar, bg=SIDEBAR)
        self.step_list.pack(fill="x")

        right = tk.Frame(self.root, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        # Pack bottom bar FIRST so it stays visible
        bar = tk.Frame(right, bg=SIDEBAR, height=56)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)

        self.area = tk.Frame(right, bg=BG)
        self.area.pack(fill="both", expand=True)
        self.btn_next = tk.Button(
            bar,
            text="Continue →",
            command=self._next,
            bg=WHITE,
            fg=SIDEBAR,
            activebackground=SIDEBAR,
            activeforeground=WHITE,
            relief="flat",
            bd=0,
            padx=28,
            pady=10,
            font=(SANS, FONT_SIZE_L, "bold"),
            cursor="hand2",
        )
        self.btn_next.bind(
            "<Enter>", lambda e: self.btn_next.config(bg=SIDEBAR, fg=WHITE)
        )
        self.btn_next.bind(
            "<Leave>", lambda e: self.btn_next.config(bg=WHITE, fg=SIDEBAR)
        )
        self.btn_next.pack(side="right", padx=20, pady=8)
        self.btn_back = tk.Button(
            bar,
            text="← Back",
            command=self._back,
            bg=CARD,
            fg=TEXT,
            activebackground=SIDEBAR,
            activeforeground=WHITE,
            relief="flat",
            bd=0,
            padx=22,
            pady=10,
            font=(SANS, FONT_SIZE_BASE, "bold"),
            cursor="hand2",
            state="disabled",
        )
        self.btn_back.bind(
            "<Enter>",
            lambda e: (
                self.btn_back.config(bg=SIDEBAR, fg=WHITE)
                if self.btn_back["state"] != "disabled"
                else None
            ),
        )
        self.btn_back.bind(
            "<Leave>",
            lambda e: (
                self.btn_back.config(bg=CARD, fg=TEXT)
                if self.btn_back["state"] != "disabled"
                else None
            ),
        )
        self.btn_back.pack(side="right", padx=0, pady=8)

        self.status_label = tk.Label(
            bar, text="", bg=SIDEBAR, fg=MUTED, font=(SANS, FONT_SIZE_BASE)
        )
        self.status_label.pack(side="left", padx=18)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _go_welcome(self):
        self.steps = [
            ("Welcome", self._welcome),
            ("Your Plans", self._select),
            ("Workspace", self._workspace),
            ("All Set!", self._done),
        ]
        self.step_idx = 0
        self._render()

    def _next(self):
        if self.step_idx < len(self.steps) - 1:
            self.step_idx += 1
            self._render()

    def _back(self):
        if self.step_idx > 0:
            self.step_idx -= 1
            self._render()

    def _render(self):
        self.status_label.config(text="")
        self.btn_back.config(state="normal" if self.step_idx > 0 else "disabled")
        self.btn_next.config(
            command=self._next, text="Continue →", bg=ACCENT, fg=WHITE, state="normal"
        )
        self._redraw_sidebar()
        self._clear()
        _, fn = self.steps[self.step_idx]
        fn()

    def _redraw_sidebar(self):
        for w in self.step_list.winfo_children():
            w.destroy()
        for i, (label, _) in enumerate(self.steps):
            done = i < self.step_idx
            current = i == self.step_idx
            dot = "✓" if done else ("●" if current else "○")
            fc = GREEN if done else (WHITE if current else MUTED)
            fw = "bold" if current else "normal"
            row = tk.Frame(self.step_list, bg=SIDEBAR)
            row.pack(fill="x", pady=5)
            accent_bar = ACCENT if current else SIDEBAR
            tk.Frame(row, bg=accent_bar, width=4).pack(side="left", fill="y", pady=2)
            tk.Label(
                row, text=dot, bg=SIDEBAR, fg=fc, font=(SANS, FONT_SIZE_L, fw), width=3
            ).pack(side="left", padx=(10, 6))
            tk.Label(
                row,
                text=label,
                bg=SIDEBAR,
                fg=fc,
                font=(SANS, FONT_SIZE_BASE, fw),
                anchor="w",
            ).pack(side="left")

    def _clear(self):
        for w in self.area.winfo_children():
            w.destroy()
        self._agent_cards.clear()

    def _content(self):
        f = tk.Frame(self.area, bg=BG)
        f.pack(fill="both", expand=True, padx=34, pady=16)
        return f

    # ── Step: Welcome ─────────────────────────────────────────────────────────

    def _welcome(self):
        f = self._content()
        tk.Label(f, text="🧠", bg=BG, font=(SANS, 64)).pack(pady=(20, 10))
        tk.Label(
            f,
            text="Welcome to LeadAgent",
            bg=BG,
            fg=SIDEBAR,
            font=(SANS, FONT_SIZE_XL, "bold"),
        ).pack()
        tk.Label(
            f,
            text="Your personal AI routing layer.\n"
            "Route every prompt to the best available model — automatically.",
            bg=BG,
            fg=TEXT,
            font=(SANS, FONT_SIZE_L),
            justify="center",
        ).pack(pady=(16, 40))

        pills = tk.Frame(f, bg=BG)
        pills.pack()
        for icon, label in [
            ("🔀", "Smart routing"),
            ("💾", "Graph memory"),
            ("⚡", "Zero API cost"),
        ]:
            p = tk.Frame(pills, bg=CARD, padx=22, pady=18)
            p.pack(side="left", padx=12)
            tk.Label(
                p,
                text=f"{icon}  {label}",
                bg=CARD,
                fg=TEXT,
                font=(SANS, FONT_SIZE_BASE, "bold"),
            ).pack()

        self.btn_next.config(text="Get Started →", font=(SANS, FONT_SIZE_L, "bold"))

    # ── Step: Select subscriptions ────────────────────────────────────────────

    def _select(self):
        f = self._content()
        tk.Label(
            f,
            text="AI Subscriptions",
            bg=BG,
            fg=SIDEBAR,
            font=(SANS, FONT_SIZE_XL, "bold"),
        ).pack(anchor="w")
        tk.Label(
            f,
            text="LeadAgent routes only to models you enable below.",
            bg=BG,
            fg=TEXT,
            font=(SANS, FONT_SIZE_L),
        ).pack(anchor="w", pady=(4, 16))

        self._chk: dict[str, tk.BooleanVar] = {}
        for key in AGENT_ORDER:
            spec = AGENTS[key]
            var = tk.BooleanVar(value=key in self.selected)
            self._chk[key] = var
            card = tk.Frame(f, bg=CARD, padx=20, pady=10)
            card.pack(fill="x", pady=4)

            tk.Label(card, text="●", bg=CARD, fg=spec.color, font=(SANS, 24)).pack(
                side="left", padx=(0, 16)
            )

            info = tk.Frame(card, bg=CARD)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(
                info,
                text=spec.display,
                bg=CARD,
                fg=SIDEBAR,
                font=(SANS, FONT_SIZE_L, "bold"),
                anchor="w",
            ).pack(anchor="w")

            install_status = " •  installed" if is_installed(key) else ""
            GREEN if is_installed(key) else MUTED
            vendor_line = tk.Frame(info, bg=CARD)
            vendor_line.pack(anchor="w")
            sub = tk.Label(
                vendor_line,
                text=spec.vendor,
                bg=CARD,
                fg=MUTED,
                font=(SANS, FONT_SIZE_BASE),
                anchor="w",
            )
            sub.pack(side="left")
            if install_status:
                tk.Label(
                    vendor_line,
                    text=install_status,
                    bg=CARD,
                    fg=GREEN,
                    font=(SANS, FONT_SIZE_BASE, "bold"),
                ).pack(side="left")

            if not spec.npm_pkg:
                tk.Label(
                    info,
                    text=spec.note or "Coming Soon",
                    bg=CARD,
                    fg=YELLOW,
                    font=(SANS, FONT_SIZE_BASE, "bold"),
                    wraplength=580,
                    justify="left",
                ).pack(anchor="w", pady=(4, 0))
            tk.Checkbutton(
                card,
                variable=var,
                bg=CARD,
                activebackground=CARD,
                selectcolor=BG,
                cursor="hand2",
                font=(SANS, FONT_SIZE_L),
            ).pack(side="right")

        def _continue():
            sel = {k for k, v in self._chk.items() if v.get()}
            if not sel:
                self.status_label.config(text="Pick at least one subscription.", fg=RED)
                return
            self.selected = sel
            installable = [
                k for k in AGENT_ORDER if k in self.selected and AGENTS[k].npm_pkg
            ]
            self.steps = (
                [("Welcome", self._welcome), ("Your Plans", self._select)]
                + [
                    (AGENTS[k].display, lambda key=k: self._agent(key))
                    for k in installable
                ]
                + [("Workspace", self._workspace), ("All Set!", self._done)]
            )
            self.step_idx += 1
            self._render()

        self.btn_next.config(command=_continue)

    # ── Step: Workspace setup ─────────────────────────────────────────────────

    def _workspace(self):
        f = self._content()
        tk.Label(
            f,
            text="📂 Workspace Directory",
            bg=BG,
            fg=SIDEBAR,
            font=(SANS, FONT_SIZE_XL, "bold"),
        ).pack(anchor="w")
        tk.Label(
            f,
            text="LeadAgent needs to know where your projects live for Auto-Discovery.",
            bg=BG,
            fg=TEXT,
            font=(SANS, FONT_SIZE_L),
        ).pack(anchor="w", pady=(6, 24))

        card = tk.Frame(f, bg=CARD, padx=22, pady=24)
        card.pack(fill="x")

        tk.Label(
            card,
            text="Absolute path to projects folder:",
            bg=CARD,
            fg=TEXT,
            font=(SANS, FONT_SIZE_L, "bold"),
        ).pack(anchor="w")

        # Load current or default
        current_p = os.path.expanduser("~")
        if os.path.exists(CONFIG_FILE):
            try:
                prev = json.load(open(CONFIG_FILE)).get("projects_dir")
                if prev:
                    current_p = prev
            except:
                pass

        self.p_entry = tk.Entry(
            card,
            bg=CODE,
            fg=WHITE,
            insertbackground=WHITE,
            font=(MONO, FONT_SIZE_XL, "bold"),
            relief="flat",
            bd=12,
        )
        self.p_entry.pack(fill="x", pady=(16, 10))
        self.p_entry.insert(0, current_p)

        tk.Label(
            card,
            text="Example: /Users/username/Projects",
            bg=CARD,
            fg=MUTED,
            font=(SANS, FONT_SIZE_BASE),
        ).pack(anchor="w")

        def _save_and_next():
            p_path = self.p_entry.get().strip()
            if not p_path or not p_path.startswith("/"):
                self.status_label.config(
                    text="Please provide an absolute path.", fg=RED
                )
                return
            _write_onboard_state(self.selected, projects_dir=p_path)
            self._next()

        self.btn_next.config(command=_save_and_next)

    # ── Step: Per-agent setup ─────────────────────────────────────────────────

    def _agent(self, key: str):
        spec = AGENTS[key]
        f = self._content()

        h = tk.Frame(f, bg=BG)
        h.pack(fill="x", anchor="w")
        tk.Label(h, text="●", bg=BG, fg=spec.color, font=(SANS, 20)).pack(
            side="left", padx=(0, 12)
        )
        hh = tk.Frame(h, bg=BG)
        hh.pack(side="left")
        tk.Label(
            hh,
            text=spec.display,
            bg=BG,
            fg=SIDEBAR,
            font=(SANS, FONT_SIZE_L, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            hh,
            text=f"Connect your {spec.vendor} subscription",
            bg=BG,
            fg=TEXT,
            font=(SANS, FONT_SIZE_BASE),
            anchor="w",
        ).pack(anchor="w")

        tk.Frame(f, bg=CARD, height=1).pack(fill="x", pady=18)

        # Live state
        installed = is_installed(key)
        authed = is_authenticated(key) if installed else None

        card_state = {"installed": installed, "authed": authed}
        self._agent_cards[key] = card_state

        # ── Card 1: Install CLI
        card_state["install_card"] = self._cmd_card(
            f,
            step="Step 1 — Install the CLI",
            done=installed,
            done_label="installed",
            cmd=None if installed else _install_cmd(spec),
            note=None
            if installed
            else 'Add to PATH: export PATH="$HOME/.leadagent/bin:$PATH"',
            verify=lambda: self._verify(key, "install"),
        )

        # ── Card 2: Log in
        if spec.login_cmd:
            done_login = authed is True
            login_cmd = self._get_docker_cmd(key, spec.login_cmd)
            card_state["login_card"] = self._cmd_card(
                f,
                step="Step 2 — Log in to your subscription",
                done=done_login,
                done_label="signed in" if done_login else None,
                cmd=login_cmd,
                note="OAuth flow — come back here and click Verify.",
                verify=lambda: self._verify(key, "login"),
            )

        tk.Label(
            f,
            text="Run the commands in a terminal, then click Verify.",
            bg=BG,
            fg=TEXT,
            font=(SANS, FONT_SIZE_BASE),
        ).pack(anchor="w", pady=(16, 0))

    def _cmd_card(self, parent, step, done, done_label, cmd, note=None, verify=None):
        card = tk.Frame(
            parent,
            bg=CARD,
            padx=22,
            pady=12,
            highlightbackground="#E0E2EE",
            highlightthickness=1,
        )
        card.pack(fill="x", pady=4)

        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x")
        dot = tk.Label(
            top,
            text="✓" if done else "○",
            bg=CARD,
            fg=GREEN if done else MUTED,
            font=(SANS, FONT_SIZE_XL, "bold"),
            width=2,
        )
        dot.pack(side="left")
        tk.Label(
            top, text=step, bg=CARD, fg=SIDEBAR, font=(SANS, FONT_SIZE_L, "bold")
        ).pack(side="left")
        status_lbl = tk.Label(
            top,
            text=done_label if done else "",
            bg=CARD,
            fg=GREEN if done else MUTED,
            font=(SANS, FONT_SIZE_BASE, "bold"),
        )
        status_lbl.pack(side="right")

        if cmd:
            cb = tk.Frame(card, bg=CODE, padx=16, pady=14)
            cb.pack(fill="x", pady=(12, 0))
            tk.Label(
                cb,
                text=cmd,
                bg=CODE,
                fg=WHITE,
                font=(MONO, FONT_SIZE_L, "bold"),
                anchor="w",
                justify="left",
            ).pack(anchor="w")
            if note:
                tk.Label(
                    card, text=note, bg=CARD, fg=TEXT, font=(SANS, FONT_SIZE_BASE)
                ).pack(anchor="w", pady=(8, 0))
            btns = tk.Frame(card, bg=CARD)
            btns.pack(anchor="w", pady=(16, 0))

            # Action buttons - EXTREME CONTRAST
            self._btn(btns, "Copy", lambda c=cmd: self._copy(c)).pack(
                side="left", padx=(0, 12)
            )
            self._btn(btns, "Open Terminal", lambda c=cmd: self._terminal(c)).pack(
                side="left", padx=(0, 12)
            )
            if verify:
                self._btn(btns, "Verify", verify).pack(side="left")

        return {"dot": dot, "status": status_lbl}

    def _verify(self, key: str, kind: str):
        installed = is_installed(key)
        if kind == "install":
            self._set_card(
                self._agent_cards[key].get("install_card"),
                ok=installed,
                ok_label="installed",
                bad_label="not detected on PATH",
            )
            if installed:
                # Re-render so step 2 picks up the new install state
                pass
        else:
            authed = is_authenticated(key)
            self._set_card(
                self._agent_cards[key].get("login_card"),
                ok=(authed is True),
                ok_label="signed in",
                bad_label="not signed in",
            )

    @staticmethod
    def _set_card(card_refs, ok: bool, ok_label: str, bad_label: str):
        if not card_refs:
            return
        card_refs["dot"].config(text="✓" if ok else "○", fg=GREEN if ok else RED)
        card_refs["status"].config(
            text=ok_label if ok else bad_label, fg=GREEN if ok else RED
        )

    # ── Step: All Set ─────────────────────────────────────────────────────────

    def _done(self):
        _write_onboard_state(self.selected)

        f = self._content()
        tk.Label(f, text="✓", bg=BG, fg=GREEN, font=(SANS, 36)).pack(pady=(6, 2))
        tk.Label(
            f, text="You're all set!", bg=BG, fg=SIDEBAR, font=(SANS, 16, "bold")
        ).pack()
        tk.Label(
            f,
            text="LeadAgent is configured and ready to route your prompts.",
            bg=BG,
            fg=TEXT,
            font=(SANS, 10),
        ).pack(pady=(4, 14))

        # Dashboard with retry — backend may take ~25s to come up first time.
        self._dashboard_frame = tk.Frame(f, bg=BG)
        self._dashboard_frame.pack(fill="x")
        self._dashboard_attempts = 0
        self._render_dashboard(starting=True)
        self._poll_dashboard()

        self.btn_back.config(state="disabled")
        self.btn_next.config(
            text="Close",
            command=self.root.destroy,
            bg=GREEN,
            fg=WHITE,
            activebackground="#4cae60",
        )

    def _poll_dashboard(self):
        health, ok = self._fetch_health()
        if ok or self._dashboard_attempts >= 25:
            self._render_dashboard(health=health, backend_ok=ok)
            return
        self._dashboard_attempts += 1
        self._render_dashboard(starting=True, attempts=self._dashboard_attempts)
        self.root.after(1000, self._poll_dashboard)

    def _fetch_health(self):
        try:
            with urllib.request.urlopen("http://localhost:8000/health", timeout=1) as r:
                return json.loads(r.read()), True
        except Exception:
            return {}, False

    def _render_dashboard(
        self, health=None, backend_ok=False, starting=False, attempts=0
    ):
        for w in self._dashboard_frame.winfo_children():
            w.destroy()
        panel = tk.Frame(self._dashboard_frame, bg=CARD)
        panel.pack(fill="x")

        header = tk.Frame(panel, bg=ACCENT, padx=14, pady=6)
        header.pack(fill="x")
        tk.Label(
            header,
            text="LeadAgent  Agent Dashboard",
            bg=ACCENT,
            fg=WHITE,
            font=(SANS, 10, "bold"),
        ).pack(side="left")
        if starting:
            status_dot, status_col = f"◌  starting backend ({attempts}s)", YELLOW
        elif backend_ok:
            status_dot, status_col = "●  online", GREEN
        else:
            status_dot, status_col = "○  backend offline", YELLOW
        tk.Label(
            header, text=status_dot, bg=ACCENT, fg=status_col, font=(MONO, 9)
        ).pack(side="right")

        health = health or {}
        agents_health = health.get("components", {}).get("agents", {})
        quotas = health.get("quotas", {})

        for key in AGENT_ORDER:
            spec = AGENTS[key]
            ag = agents_health.get(key, {})
            q = quotas.get(key, {})

            installed = ag.get("installed", is_installed(key))
            is_enabled = ag.get("enabled", key in self.selected)
            signed_in = ag.get("signed_in")
            exhausted = backend_ok and ag.get("exhausted", False)

            if not installed:
                dot, status_text, status_color = "○", "not installed", MUTED
            elif not is_enabled:
                dot, status_text, status_color = "○", "not enabled", MUTED
            elif signed_in is False:
                dot, status_text, status_color = "○", "not signed in", YELLOW
            elif exhausted:
                reset_in = ag.get("reset_in")
                if reset_in:
                    h, rem = divmod(reset_in, 3600)
                    status_text = f"exhausted — resets in {h}h {rem // 60:02d}m"
                else:
                    status_text = "exhausted"
                dot, status_color = "○", YELLOW
            else:
                dot, status_text, status_color = "●", "available", GREEN

            usage = ""
            if installed and is_enabled and signed_in is not False and not exhausted:
                wpct = q.get("real_weekly_pct")
                dpct = q.get("real_daily_pct") or q.get("session_pct")
                if wpct is not None:
                    usage += f"   weekly {wpct:.0f}%"
                if dpct is not None:
                    usage += f"   session {dpct:.0f}%"

            row = tk.Frame(panel, bg=CARD, padx=14, pady=7)
            row.pack(fill="x")
            tk.Label(
                row,
                text=dot,
                bg=CARD,
                fg=status_color,
                font=(MONO, 13, "bold"),
                width=2,
            ).pack(side="left")
            tk.Label(
                row,
                text=key.upper(),
                bg=CARD,
                fg=spec.color,
                font=(MONO, 10, "bold"),
                width=7,
            ).pack(side="left")
            tk.Label(
                row, text=status_text, bg=CARD, fg=status_color, font=(SANS, 10)
            ).pack(side="left")
            if usage:
                tk.Label(row, text=usage, bg=CARD, fg=MUTED, font=(MONO, 9)).pack(
                    side="left", padx=(6, 0)
                )
            tk.Frame(panel, bg=SIDEBAR, height=1).pack(fill="x", padx=14)

        if not backend_ok and not starting:
            hint = tk.Frame(panel, bg=CARD, padx=14, pady=8)
            hint.pack(fill="x")
            tk.Label(
                hint,
                text="backend offline — start with: ./start_backend.sh",
                bg=CARD,
                fg=MUTED,
                font=(SANS, 9),
            ).pack(anchor="w")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _btn(self, parent, text, cmd, bg=WHITE, fg=SIDEBAR):
        """Unified styled button - EXTREME CONTRAST (Obsidian on White)."""
        btn = tk.Button(
            parent,
            text=text,
            command=cmd,
            bg=bg,
            fg=fg,
            activebackground=SIDEBAR,
            activeforeground=WHITE,
            relief="flat",
            bd=0,
            padx=24,
            pady=12,
            font=(SANS, FONT_SIZE_L, "bold"),
            cursor="hand2",
        )

        # Hover effect: Perfect Inversion
        btn.bind("<Enter>", lambda e: btn.config(bg=fg, fg=bg))
        btn.bind("<Leave>", lambda e: btn.config(bg=bg, fg=fg))
        return btn

    def _copy(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_label.config(text="Copied to clipboard.", fg=GREEN)

    def _terminal(self, cmd: str):
        """Open a new terminal window and run `cmd` — quoting-safe via tempfile."""
        try:
            script = tempfile.NamedTemporaryFile(
                "w", prefix="leadagent_", suffix=".sh", delete=False
            )
            script.write("#!/usr/bin/env bash\n")
            script.write(cmd + "\n")
            script.write('echo; echo "[press enter to close]"; read\n')
            script.close()
            os.chmod(script.name, 0o755)

            if sys.platform == "darwin":
                subprocess.Popen(
                    [
                        "osascript",
                        "-e",
                        f'tell application "Terminal" to do script "{script.name}"',
                        "-e",
                        'tell application "Terminal" to activate',
                    ]
                )
                return
            for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
                if shutil.which(term):
                    subprocess.Popen([term, "-e", script.name])
                    return
            self.status_label.config(
                text="No terminal emulator found — copy & run the command manually.",
                fg=YELLOW,
            )
        except Exception as e:
            self.status_label.config(text=f"Failed to open terminal: {e}", fg=RED)


def main():
    if not _HAS_TK:
        print("⚠️  tkinter not available — using terminal setup instead.")
        _tui_wizard()
        return
    try:
        SetupWizard()
    except tk.TclError as e:
        print(f"GUI unavailable ({e}); falling back to terminal setup.")
        _tui_wizard()


if __name__ == "__main__":
    main()
