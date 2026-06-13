package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime/debug"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/charmbracelet/bubbles/key"
	"github.com/charmbracelet/bubbles/textarea"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/lipgloss"
	"golang.org/x/sys/unix"
	"golang.org/x/term"
)

type ChatRequest struct {
	Prompt         string `json:"prompt"`
	TaskType       string `json:"task_type"`
	PreferredAgent string `json:"preferred_agent,omitempty"`
	SessionID      string `json:"session_id,omitempty"`
	CWD            string `json:"cwd,omitempty"`
	Parallel       bool   `json:"parallel,omitempty"`
}

type DebateRequest struct {
	Prompt string   `json:"prompt"`
	Rounds int      `json:"rounds"`
	Agents []string `json:"agents,omitempty"`
	CWD    string   `json:"cwd,omitempty"`
	Force  bool     `json:"force,omitempty"`
}

type ChatResponse struct {
	Agent         string                `json:"agent"`
	Response      string                `json:"response"`
	UsageEstimate int                   `json:"usage_estimate"`
	Quotas        map[string]QuotaState `json:"quotas"`
	Timing        map[string]string     `json:"timing,omitempty"`
}

type QuotaState struct {
	Exhausted     bool     `json:"exhausted"`
	ResetAt       *float64 `json:"reset_at"`
	LimitType     *string  `json:"limit_type"`
	RealDailyPct  *float64 `json:"real_daily_pct"`
	RealWeeklyPct *float64 `json:"real_weekly_pct"`
}

type AgentHealth struct {
	Installed bool  `json:"installed"`
	Enabled   bool  `json:"enabled"`
	SignedIn  *bool `json:"signed_in"`
	Available bool  `json:"available"`
	Exhausted bool  `json:"exhausted"`
	ResetIn   *int  `json:"reset_in"`
}

type HealthComponents struct {
	Database      map[string]interface{} `json:"database"`
	MemoryService map[string]interface{} `json:"memory_service"`
	Agents        map[string]AgentHealth `json:"agents"`
}

type HealthResponse struct {
	Status    string           `json:"status"`
	UptimeSec float64          `json:"uptime_seconds"`
	Components HealthComponents `json:"components"`
	Quotas    map[string]QuotaState `json:"quotas"`
}

const (
	Reset  = "\033[0m"
	Bold   = "\033[1m"
	Dim    = "\033[2m"
	Red    = "\033[31m"
	Green  = "\033[32m"
	Yellow = "\033[33m"
	Blue   = "\033[34m"
	Purple = "\033[35m"
	Cyan   = "\033[36m"
	Gray   = "\033[37m"
	White  = "\033[97m"
)

// requestCancel holds the cancel func for the currently in-flight HTTP request.
// SIGINT calls it so the request aborts and the REPL returns to the prompt.
var (
	requestCancel func() = func() {}
	requestMu     sync.Mutex
	termMu        sync.Mutex
)

func setRequestCancel(cancel func()) {
	requestMu.Lock()
	requestCancel = cancel
	requestMu.Unlock()
}

func cancelCurrentRequest() {
	requestMu.Lock()
	f := requestCancel
	requestCancel = func() {}
	requestMu.Unlock()
	f()
}

// initSigintHandler catches Ctrl+C and cancels the in-flight request instead
// of killing the process. A second Ctrl+C with no request in flight exits.
func initSigintHandler() {
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, os.Interrupt)
	go func() {
		for range ch {
			cancelCurrentRequest()
			// Print on a fresh line so the prompt doesn't look garbled.
			fmt.Printf("\r%s^C%s\n", Dim+Gray, Reset)
		}
	}()
}

// renderMarkdown renders markdown text using glamour with a dark theme.
// Falls back to raw content if glamour produces nothing visible.
func renderMarkdown(content string) string {
	r, err := glamour.NewTermRenderer(
		glamour.WithStylePath("dark"),
		glamour.WithWordWrap(100),
	)
	if err != nil {
		return content
	}
	rendered, err := r.Render(content)
	if err != nil || strings.TrimSpace(rendered) == "" {
		return content
	}
	return rendered
}

// printSeparator prints a subtle divider between exchanges.
func printSeparator() {
	fmt.Printf("%s%s%s\n", Dim+Gray, strings.Repeat("─", 78), Reset)
}

func printTimingLedger(jsonStr string) {
	var raw map[string]interface{}
	if err := json.Unmarshal([]byte(jsonStr), &raw); err != nil {
		return
	}

	str := func(key string) string {
		if v, ok := raw[key]; ok {
			return fmt.Sprintf("%v", v)
		}
		return ""
	}

	// Determine badge: fan-out summary has "agents" array, single has "agent" string
	badge := ""
	if agentsRaw, ok := raw["agents"]; ok {
		// fan-out summary — list agent names
		if arr, ok := agentsRaw.([]interface{}); ok {
			parts := make([]string, 0, len(arr))
			for _, a := range arr {
				name := fmt.Sprintf("%v", a)
				parts = append(parts, getAgentColor(name)+strings.ToUpper(name)+Reset+Dim+Gray)
			}
			badge = " " + strings.Join(parts, Dim+Gray+" + "+Reset+Dim+Gray)
		}
	} else if ag := str("agent"); ag != "" {
		badge = " via " + getAgentColor(ag) + strings.ToUpper(ag) + Reset + Dim + Gray
	}

	termMu.Lock()
	defer termMu.Unlock()

	fmt.Printf("\n%s%s┌─ Timing Ledger%s %s%s\n", Dim, Gray, badge, strings.Repeat("─", 28), Reset)
	sep := Dim + Gray + "│" + Reset

	order := []string{"memory", "routing", "agent_start", "agent_total", "total"}
	labels := map[string]string{
		"memory":      "memory lookup",
		"routing":     "agent routing",
		"agent_start": "time to first token",
		"agent_total": "agent response",
		"total":       "total",
	}
	for _, key := range order {
		val := str(key)
		if val == "" {
			continue
		}
		label := fmt.Sprintf("%-22s", labels[key])
		if key == "total" {
			fmt.Printf("%s├%s%s\n", Dim+Gray, strings.Repeat("─", 43), Reset)
			fmt.Printf("%s  %s %s%s%s\n", sep, label, Bold+White, val, Reset)
			continue
		}
		fmt.Printf("%s  %s %s%s%s\n", sep, label, Dim+Gray, val, Reset)
	}
	fmt.Printf("%s%s└%s%s\n", Dim, Gray, strings.Repeat("─", 44), Reset)
}

// printAgentHeader prints the agent label with a pill-style badge.
func printAgentHeader(agent string) {
	color := getAgentColor(agent)
	label := strings.ToUpper(agent)
	termMu.Lock()
	defer termMu.Unlock()
	fmt.Printf("\n%s %s●%s %s%s%s\n", Dim+Gray+"───", color, Reset+Dim+Gray+"──────────────────────────────────────────────────────── ", color+Bold, label, Reset)
}

// findProjectRoot returns the LeadAgent project root regardless of cwd.
// Strategy: walk up from the executable looking for backend/setup_wizard.py,
// then fall back to walking up from cwd.
func findProjectRoot() string {
	candidates := []string{}
	if exe, err := os.Executable(); err == nil {
		if real, err := filepath.EvalSymlinks(exe); err == nil {
			candidates = append(candidates, filepath.Dir(real))
		}
	}
	if cwd, err := os.Getwd(); err == nil {
		candidates = append(candidates, cwd)
	}
	for _, start := range candidates {
		dir := start
		for i := 0; i < 6; i++ {
			if _, err := os.Stat(filepath.Join(dir, "backend", "setup_wizard.py")); err == nil {
				return dir
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
			dir = parent
		}
	}
	return ""
}

func projectFile(rel string) string {
	if root := findProjectRoot(); root != "" {
		return filepath.Join(root, rel)
	}
	return rel
}

func isOnboarded() bool {
	_, err := os.Stat(projectFile("leadagent-data/.onboarded"))
	return err == nil
}

func runSetupWizard() {
	root := findProjectRoot()
	if root == "" {
		fmt.Printf("%s⚠️  Could not locate LeadAgent project root — skipping setup wizard.%s\n", Yellow, Reset)
		return
	}
	// Prefer the venv python so the wizard uses the installed dependencies.
	python := ""
	candidates := []string{
		filepath.Join(root, "leadagent", "bin", "python3"),
		filepath.Join(root, "leadagent", "bin", "python"),
	}
	for _, p := range candidates {
		if _, err := os.Stat(p); err == nil {
			python = p
			break
		}
	}
	if python == "" {
		for _, p := range []string{"python3", "python"} {
			if path, err := exec.LookPath(p); err == nil {
				python = path
				break
			}
		}
	}
	if python == "" {
		fmt.Printf("%s⚠️  Python 3 not found — run backend/setup_wizard.py manually to complete setup.%s\n", Yellow, Reset)
		return
	}
	wizard := filepath.Join(root, "backend", "setup_wizard.py")
	if _, err := os.Stat(wizard); err != nil {
		return
	}
	fmt.Printf("%s🧠  Opening LeadAgent setup wizard...%s\n", Cyan, Reset)
	cmd := exec.Command(python, wizard)
	cmd.Dir = root
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	cmd.Run()
}

var version = "0.4.0"

func printVersion() {
	rev, dirty := "", ""
	if info, ok := debug.ReadBuildInfo(); ok {
		for _, s := range info.Settings {
			switch s.Key {
			case "vcs.revision":
				rev = s.Value
			case "vcs.modified":
				if s.Value == "true" {
					dirty = "-dirty"
				}
			}
		}
	}
	if len(rev) > 7 {
		rev = rev[:7]
	}
	if rev != "" {
		fmt.Printf("leadagent %s (%s%s)\n", version, rev, dirty)
	} else {
		fmt.Printf("leadagent %s\n", version)
	}
}

func main() {
	for _, arg := range os.Args[1:] {
		if arg == "--version" || arg == "-v" || arg == "version" {
			printVersion()
			return
		}
	}

	initSigintHandler()
	go initProject()

	skipOnboard := false
	reOnboard := false
	for _, arg := range os.Args {
		switch arg {
		case "--skip-onboard":
			skipOnboard = true
		case "--onboard", "--onboarding":
			reOnboard = true
		}
	}

	isUtilityCmd := false
	for _, arg := range os.Args {
		if arg == "--query" || arg == "--auth" || arg == "health" || arg == "doctor" {
			isUtilityCmd = true
			break
		}
	}

	// First-run: launch GUI setup wizard
	if !isUtilityCmd && !skipOnboard && (!isOnboarded() || reOnboard) {
		runSetupWizard()
		if reOnboard {
			return
		}
	}

	prompt := ""
	taskType := "general"
	preferredAgent := ""
	isQuery := false
	isParallel := false
	isDebate := false
	isNoContext := false
	isForce := false
	debateRounds := 3

	for i := 1; i < len(os.Args); i++ {
		arg := os.Args[i]
		if arg == "--auth" {
			handleAuth()
			return
		} else if arg == "health" {
			handleHealth()
			return
		} else if arg == "doctor" {
			handleDoctor()
			return
		} else if arg == "--query" {
			isQuery = true
		} else if arg == "debate" {
			isDebate = true
		} else if arg == "hello" {
			fmt.Println("world")
			return
		} else if arg == "--type" && i+1 < len(os.Args) {
			taskType = os.Args[i+1]
			i++
		} else if arg == "--agent" && i+1 < len(os.Args) {
			preferredAgent = os.Args[i+1]
			i++
		} else if arg == "--rounds" && i+1 < len(os.Args) {
			if n, err := strconv.Atoi(os.Args[i+1]); err == nil {
				debateRounds = n
			}
			i++
		} else if arg == "--parallel" {
			isParallel = true
		} else if arg == "--no-context" || arg == "-nc" {
			isNoContext = true
		} else if arg == "--force" || arg == "-f" {
			isForce = true
		} else if arg == "--skip-onboard" {
			// already handled
		} else if prompt == "" {
			prompt = arg
		}
	}

	// Interpret (nc) or (no context) from within the prompt
	promptLower := strings.ToLower(prompt)
	if strings.Contains(promptLower, "(nc)") || strings.Contains(promptLower, "(no context)") {
		isNoContext = true
		prompt = strings.ReplaceAll(prompt, "(nc)", "")
		prompt = strings.ReplaceAll(prompt, "(no context)", "")
		prompt = strings.ReplaceAll(prompt, "(NC)", "")
		prompt = strings.TrimSpace(prompt)
	}

	if isDebate {
		if prompt == "" {
			fmt.Println("Usage: leadagent debate [--rounds N] [--no-context] \"your topic\"")
			return
		}
		ensureBackend()
		cwd, _ := os.Getwd()
		if isNoContext {
			fmt.Printf("%s  No-context mode active — skipping project scanning.%s\n", Yellow, Reset)
			cwd = filepath.Join(os.TempDir(), "leadagent-nc")
			os.MkdirAll(cwd, 0755)
		}
		sendDebate(prompt, debateRounds, nil, cwd, isForce)
		return
	}

	if isQuery && prompt != "" {
		handleQuery(prompt)
		return
	}

	if prompt == "" {
		startREPL()
		return
	}

	ensureBackend()
	cwd, _ := os.Getwd()
	if isNoContext {
		fmt.Printf("%s  No-context mode active — skipping project scanning.%s\n", Yellow, Reset)
		cwd = filepath.Join(os.TempDir(), "leadagent-nc")
		os.MkdirAll(cwd, 0755)
	}

	sessionID := fmt.Sprintf("oneshot-%x", strings.ToLower(cwd))
	sendChat(prompt, taskType, preferredAgent, sessionID, isParallel, cwd)
}


func initProject() {
	cwd, _ := os.Getwd()
	reqBody := map[string]string{"path": cwd}
	jsonData, _ := json.Marshal(reqBody)
	http.Post("http://localhost:8000/project/init", "application/json", bytes.NewBuffer(jsonData))
}

func isBackendUp() bool {
	// Increased timeout to 2s to account for Docker networking overhead
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get("http://localhost:8000/health")
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return true
}

func ensureBackend() {
	// Wait up to 30s — initial check (handles slow Docker networking)
	for i := 0; i < 30; i++ {
		if isBackendUp() {
			return
		}
		if i == 0 {
			fmt.Printf("%s  Checking backend status...%s", Yellow, Reset)
		} else {
			fmt.Printf(".")
		}
		time.Sleep(1 * time.Second)
	}
	fmt.Printf("\n")

	script := projectFile("start_backend.sh")
	if _, err := os.Stat(script); err != nil {
		fmt.Printf("%s  Backend offline and start_backend.sh not found.%s\n", Red, Reset)
		return
	}

	fmt.Printf("%s  Backend offline — starting automatically...%s\n", Yellow, Reset)
	cmd := exec.Command("bash", script)
	if root := findProjectRoot(); root != "" {
		cmd.Dir = root
	}
	cmd.Stdout = nil
	cmd.Stderr = nil
	// Own process group so Ctrl+C in the CLI doesn't SIGINT the backend.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		fmt.Printf("%s  Failed to start backend: %v%s\n", Red, err, Reset)
		return
	}

	// Wait up to 30s — startup report (PTY usage scrapes) takes ~20s
	fmt.Printf("%s  Waiting for backend", Yellow)
	for i := 0; i < 30; i++ {
		time.Sleep(1 * time.Second)
		fmt.Printf(".")
		if isBackendUp() {
			fmt.Printf("%s\n%s  Backend ready.%s\n\n", Reset, Green, Reset)
			return
		}
	}
	fmt.Printf("%s\n%s  Backend did not respond in time — continuing anyway.%s\n", Reset, Yellow, Reset)
}


// ErrInterrupted is returned by readInput when Ctrl+C is pressed.
var ErrInterrupted = errors.New("interrupted")

// ── Bubbletea input model ─────────────────────────────────────────────────────

type inputModel struct {
	ta          textarea.Model
	agent       string
	role        string
	cmdHistory  []string
	histIdx     int
	savedDraft  string
	result      string
	submitted   bool
	interrupted bool
	width       int
}

func newInputModel(agent, role string, history []string, width int) inputModel {
	ta := textarea.New()
	ta.Placeholder = "Type a message…"
	ta.Focus()
	ta.CharLimit = 0
	ta.ShowLineNumbers = false
	ta.SetWidth(width - 4)
	ta.SetHeight(1)
	// Remap Enter so it inserts a real newline only on Alt+Enter;
	// plain Enter is intercepted in Update() to submit.
	ta.KeyMap.InsertNewline = key.NewBinding(key.WithKeys("alt+enter"))

	agentColor := lipgloss.Color("99") // purple default
	switch strings.ToLower(agent) {
	case "gemini":
		agentColor = lipgloss.Color("33")
	case "codex":
		agentColor = lipgloss.Color("46")
	case "grok":
		agentColor = lipgloss.Color("220")
	}

	border := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(agentColor).
		Padding(0, 1)
	dimBorder := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color("240")).
		Padding(0, 1)

	ta.FocusedStyle.Base = border
	ta.BlurredStyle.Base = dimBorder
	ta.FocusedStyle.CursorLine = lipgloss.NewStyle()

	return inputModel{
		ta:         ta,
		agent:      agent,
		role:       role,
		cmdHistory: history,
		histIdx:    len(history),
		width:      width,
	}
}

func (m inputModel) Init() tea.Cmd { return textarea.Blink }

func (m inputModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.ta.SetWidth(msg.Width - 4)
		return m, nil

	case tea.KeyMsg:
		switch msg.Type {
		case tea.KeyCtrlC:
			m.interrupted = true
			return m, tea.Quit

		case tea.KeyEnter:
			if !msg.Alt {
				val := strings.TrimSpace(m.ta.Value())
				if val != "" {
					m.result = m.ta.Value()
					m.submitted = true
					return m, tea.Quit
				}
				return m, nil
			}

		case tea.KeyUp:
			if m.ta.Line() == 0 && m.histIdx > 0 {
				if m.histIdx == len(m.cmdHistory) {
					m.savedDraft = m.ta.Value()
				}
				m.histIdx--
				m.ta.SetValue(m.cmdHistory[m.histIdx])
				m.ta.CursorEnd()
				lines := strings.Count(m.ta.Value(), "\n") + 1
				m.ta.SetHeight(lines)
				return m, nil
			}

		case tea.KeyDown:
			lineCount := strings.Count(m.ta.Value(), "\n") + 1
			if m.ta.Line() == lineCount-1 && m.histIdx < len(m.cmdHistory) {
				m.histIdx++
				if m.histIdx == len(m.cmdHistory) {
					m.ta.SetValue(m.savedDraft)
				} else {
					m.ta.SetValue(m.cmdHistory[m.histIdx])
				}
				m.ta.CursorEnd()
				lines := strings.Count(m.ta.Value(), "\n") + 1
				m.ta.SetHeight(lines)
				return m, nil
			}
		}
	}

	prevLines := strings.Count(m.ta.Value(), "\n")
	var cmd tea.Cmd
	m.ta, cmd = m.ta.Update(msg)
	newLines := strings.Count(m.ta.Value(), "\n")
	if newLines != prevLines {
		h := newLines + 1
		if h < 1 {
			h = 1
		}
		m.ta.SetHeight(h)
	}
	return m, cmd
}

func (m inputModel) View() string {
	agentLabel := "auto"
	agentColor := lipgloss.Color("240")
	if m.agent != "" {
		agentLabel = m.agent
		switch strings.ToLower(m.agent) {
		case "claude":
			agentColor = lipgloss.Color("99")
		case "gemini":
			agentColor = lipgloss.Color("33")
		case "codex":
			agentColor = lipgloss.Color("46")
		case "grok":
			agentColor = lipgloss.Color("220")
		}
	}
	nameStyle := lipgloss.NewStyle().Bold(true).Foreground(agentColor)
	dimStyle := lipgloss.NewStyle().Foreground(lipgloss.Color("240"))

	header := nameStyle.Render("brain("+agentLabel+")")
	if m.role != "" && m.role != "general" {
		header += dimStyle.Render(":" + m.role)
	}
	header += dimStyle.Render("  ↵ send · ↑↓ history · alt+↵ newline")

	return "\n" + header + "\n" + m.ta.View() + "\n"
}

// readInput runs the bubbletea input box and returns the submitted text.
func readInput(agent, role string, history []string) (string, error) {
	// Non-interactive fallback (pipes, scripts)
	if !term.IsTerminal(int(os.Stdin.Fd())) {
		sc := bufio.NewScanner(os.Stdin)
		sc.Buffer(make([]byte, 10<<20), 10<<20)
		if sc.Scan() {
			return sc.Text(), nil
		}
		return "", io.EOF
	}

	width, _, err := term.GetSize(int(os.Stdout.Fd()))
	if err != nil || width < 40 {
		width = 80
	}

	m := newInputModel(agent, role, history, width)
	p := tea.NewProgram(m, tea.WithInput(os.Stdin), tea.WithOutput(os.Stdout))
	final, err := p.Run()
	if err != nil {
		return "", err
	}
	fm := final.(inputModel)
	if fm.interrupted {
		return "", ErrInterrupted
	}
	if fm.submitted {
		return fm.result, nil
	}
	return "", io.EOF
}

func startREPL() {
	ensureBackend()
	drawDashboard()
	fmt.Printf("%s%sCommands:%s %s/agent <name>%s, %s/help%s, %sexit%s\n\n", Bold, Yellow, Reset, White, Gray, White, Gray, White, Reset)

	currentAgent := ""
	currentRole := "general"
	sessionID := fmt.Sprintf("session-%d", os.Getpid())
	history := []conversationTurn{}
	var cmdHistory []string // input box history (up/down arrows)

	for {
		input, err := readInput(currentAgent, currentRole, cmdHistory)
		if errors.Is(err, ErrInterrupted) {
			continue
		}
		if err != nil {
			break
		}
		trimmed := strings.TrimSpace(input)
		if trimmed != "" {
			cmdHistory = append(cmdHistory, trimmed)
			if len(cmdHistory) > 500 {
				cmdHistory = cmdHistory[len(cmdHistory)-500:]
			}
		}

		if trimmed == "exit" || trimmed == "quit" {
			fmt.Printf("\n%sGoodbye.%s\n", Dim, Reset)
			break
		}
		if trimmed == "" {
			continue
		}

		if trimmed == "/help" {
			fmt.Printf("\n%s%sLeadAgent Help%s\n", Bold, Yellow, Reset)
			fmt.Printf(" %s/agent <name>%s          Set agent: claude, gemini, codex, grok\n", Cyan, Reset)
			fmt.Printf(" %s/agent auto%s            Return to automatic routing\n", Cyan, Reset)
			fmt.Printf(" %s/role <name>%s           Set role: general, coding, reviewer, debugger, research, architect\n", Cyan, Reset)
			fmt.Printf(" %s/roles%s                 List all roles with descriptions\n", Cyan, Reset)
			fmt.Printf(" %s/health%s                Show daemon health & agent availability\n", Cyan, Reset)
			fmt.Printf(" %s/doctor%s                Run full environment diagnostic\n", Cyan, Reset)
			fmt.Printf(" %s/debate [--rounds N] [--no-context] <topic>%s  Multi-agent debate\n", Cyan, Reset)
			fmt.Printf(" %s(nc) <msg>%s             Run query without project context\n", Cyan, Reset)
			fmt.Printf(" %s<agent> <msg>%s          One-message agent switch (e.g. 'gemini explain this')\n", Cyan, Reset)
			fmt.Printf(" %sboth/compare/vs <agents> <msg>%s  Fan-out to multiple agents\n", Cyan, Reset)
			fmt.Printf(" %s/help%s                  Show this message\n", Cyan, Reset)
			fmt.Printf(" %sexit%s                   Close the session\n\n", Cyan, Reset)
			continue
		}

		if trimmed == "/roles" {
			printRoles()
			continue
		}

		if trimmed == "/health" {
			handleHealth()
			continue
		}

		if trimmed == "/doctor" {
			handleDoctor()
			continue
		}

		// /debate <topic> [--rounds N] [--no-context] [--force]
		if strings.HasPrefix(trimmed, "/debate") {
			args := strings.TrimSpace(strings.TrimPrefix(trimmed, "/debate"))
			debateRounds := 3
			isNoContext := false
			isForce := false

			// Extract --no-context or -nc
			if strings.Contains(args, "--no-context") {
				isNoContext = true
				args = strings.ReplaceAll(args, "--no-context", "")
			}
			if strings.Contains(args, "-nc") {
				isNoContext = true
				args = strings.ReplaceAll(args, "-nc", "")
			}
			if strings.Contains(args, "--force") || strings.Contains(args, " -f ") {
				isForce = true
				args = strings.ReplaceAll(args, "--force", "")
				args = strings.ReplaceAll(args, " -f ", " ")
			}

			// Extract --rounds N wherever it appears
			if idx := strings.Index(args, "--rounds "); idx != -1 {
				before := strings.TrimSpace(args[:idx])
				after := args[idx+len("--rounds "):]
				parts := strings.SplitN(after, " ", 2)
				if n, err := strconv.Atoi(parts[0]); err == nil {
					debateRounds = n
				}
				rest := ""
				if len(parts) > 1 {
					rest = strings.TrimSpace(parts[1])
				}
				if before != "" && rest != "" {
					args = before + " " + rest
				} else {
					args = before + rest
				}
			}

			args = strings.TrimSpace(args)
			// Interpret (nc) or (no context) from within the topic
			argsLower := strings.ToLower(args)
			if strings.Contains(argsLower, "(nc)") || strings.Contains(argsLower, "(no context)") {
				isNoContext = true
				args = strings.ReplaceAll(args, "(nc)", "")
				args = strings.ReplaceAll(args, "(no context)", "")
				args = strings.ReplaceAll(args, "(NC)", "")
				args = strings.TrimSpace(args)
			}

			if args == "" {
				fmt.Printf("%s  Usage: /debate <topic> [--rounds N] [--no-context]%s\n\n", Yellow, Reset)
			} else {
				cwd, _ := os.Getwd()
				if isNoContext {
					fmt.Printf("%s  No-context mode active — skipping project scanning.%s\n", Yellow, Reset)
					cwd = filepath.Join(os.TempDir(), "leadagent-nc")
					os.MkdirAll(cwd, 0755)
				}

				sendDebate(args, debateRounds, nil, cwd, isForce)
			}
			continue
		}

		if strings.HasPrefix(trimmed, "/agent ") {
			newAgent := strings.TrimSpace(strings.TrimPrefix(trimmed, "/agent "))
			if strings.ToLower(newAgent) == "auto" {
				currentAgent = ""
				fmt.Printf("%s  Switched to automatic routing.%s\n\n", Yellow, Reset)
			} else {
				currentAgent = strings.ToLower(newAgent)
				fmt.Printf("%s  Agent locked: %s%s%s\n\n", Yellow, getAgentColor(currentAgent)+Bold, strings.ToUpper(currentAgent), Reset)
			}
			continue
		}

		if strings.HasPrefix(trimmed, "/role ") {
			newRole := strings.TrimSpace(strings.TrimPrefix(trimmed, "/role "))
			currentRole = strings.ToLower(newRole)
			fmt.Printf("%s  Role set: %s%s%s\n\n", Yellow, Bold+White, strings.ToUpper(currentRole), Reset)
			continue
		}

		isNoContext := false
		prompt := trimmed
		promptLower := strings.ToLower(prompt)
		if strings.Contains(promptLower, "(nc)") || strings.Contains(promptLower, "(no context)") {
			isNoContext = true
			prompt = strings.ReplaceAll(prompt, "(nc)", "")
			prompt = strings.ReplaceAll(prompt, "(no context)", "")
			prompt = strings.ReplaceAll(prompt, "(NC)", "")
			prompt = strings.TrimSpace(prompt)
		}

		cwd, _ := os.Getwd()
		if isNoContext {
			fmt.Printf("%s  No-context mode active — skipping project scanning.%s\n", Yellow, Reset)
			cwd = filepath.Join(os.TempDir(), "leadagent-nc")
			os.MkdirAll(cwd, 0755)
		}

		enriched := buildPromptWithHistory(prompt, history)
		response := sendChat(enriched, currentRole, currentAgent, sessionID, false, cwd)

		// Store turn in rolling window — truncate long responses to control token growth
		stored := response
		if len(stored) > maxTurnResponseLen {
			stored = stored[:maxTurnResponseLen] + "…"
		}
		history = append(history, conversationTurn{user: trimmed, assistant: stored})
		if len(history) > maxHistoryTurns {
			history = history[len(history)-maxHistoryTurns:]
		}
	}
}

func drawDashboard() {
	// Short timeout so the dashboard doesn't stall when the backend is offline.
	client := &http.Client{Timeout: 2 * time.Second}
	var health HealthResponse
	backendOnline := false
	resp, err := client.Get("http://localhost:8000/health")
	if err == nil {
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		if json.Unmarshal(body, &health) == nil {
			backendOnline = true
		}
	}

	fmt.Printf("\n%s%s┌──────────────────────────────────────────────────────────────────────┐%s\n", Bold, Cyan, Reset)
	fmt.Printf("%s%s│%s                %s🧠  %sLEADAGENT %sUNIVERSAL %sORCHESTRATOR %s               %s%s│%s\n", Bold, Cyan, Reset, Purple+Bold, White+Bold, Cyan+Bold, Purple+Bold, Gray, Bold, Cyan, Reset)
	fmt.Printf("%s%s├──────────────────────────────────────────────────────────────────────┤%s\n", Bold, Cyan, Reset)

	for _, name := range []string{"claude", "gemini", "codex", "grok"} {
		ag := health.Components.Agents[name]
		q := health.Quotas[name]
		color := getAgentColor(name)

		installed := ag.Installed

		var dot, statusText string
		switch {
		case !installed:
			dot = Dim + Gray + "○" + Reset
			statusText = fmt.Sprintf("%s%-11s%s", Dim+Gray, "missing", Reset)
		case backendOnline && !ag.Enabled:
			dot = Dim + Gray + "○" + Reset
			statusText = fmt.Sprintf("%s%-11s%s", Dim+Gray, "disabled", Reset)
		case backendOnline && ag.SignedIn != nil && !*ag.SignedIn:
			dot = Yellow + Bold + "●" + Reset
			statusText = fmt.Sprintf("%s%-11s%s", Yellow+Bold, "sign-in req", Reset)
		case backendOnline && ag.Exhausted:
			dot = Red + Bold + "●" + Reset
			statusText = fmt.Sprintf("%s%-11s%s", Red, "exhausted", Reset)
		default:
			dot = Green + Bold + "●" + Reset
			statusText = fmt.Sprintf("%s%-11s%s", Green, "available", Reset)
		}

		usage := ""
		if installed && backendOnline && ag.Available {
			if q.RealDailyPct != nil {
				usage = fmt.Sprintf("  %s%s  %s%3.0f%%%s", Gray, renderProgressBar(*q.RealDailyPct, 12), White, *q.RealDailyPct, Reset)
			} else if q.RealWeeklyPct != nil {
				usage = fmt.Sprintf("  %s%s  %s%3.0f%%%s", Gray, renderProgressBar(*q.RealWeeklyPct, 12), White, *q.RealWeeklyPct, Reset)
			}
		}

		line := fmt.Sprintf("  %s%s%-8s%s  %s  %s %s",
			color, Bold, strings.ToUpper(name), Reset,
			dot, statusText, usage)
		
		fmt.Printf("%s%s│%s%-68s%s%s│%s\n",
			Bold, Cyan, Reset, line, Bold, Cyan, Reset)
	}

	if !backendOnline {
		fmt.Printf("%s%s│%s  %-66s %s%s│%s\n",
			Bold, Cyan, Reset, Yellow+"⚠️  backend offline — run: ./start_backend.sh", Bold, Cyan, Reset)
	}
	fmt.Printf("%s%s└──────────────────────────────────────────────────────────────────────┘%s\n\n", Bold, Cyan, Reset)
}

func renderProgressBar(perc float64, width int) string {
	filled := int(perc / 100 * float64(width))
	if filled > width {
		filled = width
	}
	color := Green
	if perc > 80 {
		color = Red
	} else if perc > 50 {
		color = Yellow
	}
	bar := ""
	for i := 0; i < width; i++ {
		if i < filled {
			bar += color + "█" + Reset
		} else {
			bar += Dim + Gray + "░" + Reset
		}
	}
	return bar
}

func getAgentColor(agent string) string {
	switch strings.ToLower(agent) {
	case "claude":
		return Bold + Purple
	case "gemini":
		return Bold + Blue
	case "codex":
		return Bold + Green
	case "grok":
		return Bold + Yellow
	case "ollama":
		return Bold + Cyan
	default:
		return Bold + White
	}
}

func printRoles() {
	resp, err := http.Get("http://localhost:8000/roles")
	if err != nil {
		fmt.Printf("%s  Could not fetch roles from daemon.%s\n\n", Red, Reset)
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var roles map[string]string
	if json.Unmarshal(body, &roles) != nil {
		return
	}
	order := []string{"general", "coding", "reviewer", "debugger", "research", "architect"}
	fmt.Printf("\n%s%sAvailable Roles%s\n", Bold, Yellow, Reset)
	for _, name := range order {
		desc, ok := roles[name]
		if !ok {
			continue
		}
		fmt.Printf(" %s/role %-12s%s %s\n", Cyan, name+Reset, Dim+Gray, desc+Reset)
	}
	fmt.Println()
}

// isProgressLine returns true for tool-use and status lines emitted by the CLIs.
func isProgressLine(line string) bool {
	t := strings.TrimSpace(line)
	if len(t) == 0 {
		return false
	}
	for _, ch := range []string{"✓", "✗", "⎿", "●", "▸", "…", "·", "↳"} {
		if strings.HasPrefix(t, ch) {
			return true
		}
	}
	return false
}

func printLiveProgress(line string) {
	t := strings.TrimSpace(line)
	switch {
	case strings.HasPrefix(t, "✓"):
		rest := strings.TrimSpace(t[len("✓"):])
		fmt.Printf("  %s✓%s  %s%s%s\n", Green+Bold, Reset, Dim+Gray, rest, Reset)
	case strings.HasPrefix(t, "✗"):
		rest := strings.TrimSpace(t[len("✗"):])
		fmt.Printf("  %s✗%s  %s%s%s\n", Red+Bold, Reset, Dim+Gray, rest, Reset)
	case strings.HasPrefix(t, "⎿"):
		rest := strings.TrimSpace(t[len("⎿"):])
		fmt.Printf("  %s⎿%s  %s%s%s\n", Dim+Gray, Reset, Dim+Gray, rest, Reset)
	case strings.HasPrefix(t, "↳"):
		rest := strings.TrimSpace(t[len("↳"):])
		fmt.Printf("  %s↳%s  %s%s%s\n", Cyan+Bold, Reset, Dim+Gray, rest, Reset)
	default:
		fmt.Printf("  %s%s%s\n", Dim+Gray, t, Reset)
	}
}

// startSpinner shows an animated spinner with elapsed time.
// Returns (stop, updateLabel). Call stop() to clear; call updateLabel(msg) to
// change the status text shown next to the spinner without stopping it.
func startSpinner(agent string) (stop func(), updateLabel func(string)) {
	frames := []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}
	done := make(chan struct{})
	start := time.Now()

	initLabel := "Thinking"
	color := Dim + Gray
	if agent != "" {
		initLabel = strings.ToUpper(agent)
		color = getAgentColor(agent)
	}

	var mu sync.Mutex
	currentLabel := initLabel
	currentColor := color

	go func() {
		for i := 0; ; i++ {
			select {
			case <-done:
				termMu.Lock()
				fmt.Printf("\r%-80s\r", "")
				termMu.Unlock()
				return
			default:
				mu.Lock()
				lbl := currentLabel
				clr := currentColor
				mu.Unlock()

				elapsed := int(time.Since(start).Seconds())
				termMu.Lock()
				fmt.Printf("\r%s%s%s %s%s%s (%ds)",
					clr, frames[i%len(frames)], Reset,
					Dim+Gray, lbl, Reset,
					elapsed,
				)
				termMu.Unlock()
				time.Sleep(80 * time.Millisecond)
			}
		}
	}()

	stop = func() {
		close(done)
		time.Sleep(90 * time.Millisecond)
	}
	updateLabel = func(msg string) {
		mu.Lock()
		currentLabel = msg
		currentColor = Dim + Gray
		mu.Unlock()
	}
	return
}

// conversationTurn holds one user/assistant exchange for the rolling context window.
type conversationTurn struct {
	user      string
	assistant string
}

const maxHistoryTurns = 5
const maxTurnResponseLen = 1500 // chars — keeps history tokens reasonable

// buildPromptWithHistory prepends recent turns as a plain transcript.
// Returns the prompt unchanged when there is no history.
func buildPromptWithHistory(prompt string, history []conversationTurn) string {
	if len(history) == 0 {
		return prompt
	}
	var sb strings.Builder
	sb.WriteString("[Conversation so far]\n")
	for _, t := range history {
		sb.WriteString("User: ")
		sb.WriteString(t.user)
		sb.WriteString("\nAssistant: ")
		sb.WriteString(t.assistant)
		sb.WriteString("\n\n")
	}
	sb.WriteString("---\n")
	sb.WriteString(prompt)
	return sb.String()
}

// keyListener owns stdin while a chat stream is active so single keypresses
// (e.g. [g]uide) can be detected without line buffering.
type keyListener struct {
	keys    chan byte
	stop    chan struct{}
	done    chan struct{}
	restore func()
}

func startKeyListener() *keyListener {
	fd := int(os.Stdin.Fd())
	if !term.IsTerminal(fd) {
		return nil
	}
	restore, err := enterCbreak(fd)
	if err != nil {
		return nil
	}
	kl := &keyListener{
		keys:    make(chan byte, 16),
		stop:    make(chan struct{}),
		done:    make(chan struct{}),
		restore: restore,
	}
	go func() {
		defer close(kl.done)
		buf := make([]byte, 1)
		for {
			select {
			case <-kl.stop:
				return
			default:
			}
			n, err := unix.Read(fd, buf) // VTIME-bounded: returns within ~200ms
			if n > 0 {
				select {
				case kl.keys <- buf[0]:
				case <-kl.stop:
					return
				}
			} else if err != nil && err != unix.EINTR && err != unix.EAGAIN {
				return
			}
		}
	}()
	return kl
}

func (kl *keyListener) Close() {
	close(kl.stop)
	<-kl.done
	kl.restore()
}

func postInterrupt(sessionID string) {
	client := &http.Client{Timeout: 5 * time.Second}
	client.Post(
		fmt.Sprintf("http://localhost:8000/permission/interrupt/%s", sessionID),
		"application/json", nil,
	)
}

// readLineKeys reads a line byte-by-byte with manual echo (terminal echo is
// off while the keyListener is active).
func readLineKeys(getKey func() byte) string {
	var sb []byte
	for {
		b := getKey()
		switch {
		case b == '\r' || b == '\n':
			fmt.Println()
			return string(sb)
		case b == 0x7f || b == 8: // backspace
			if len(sb) > 0 {
				sb = sb[:len(sb)-1]
				fmt.Print("\b \b")
			}
		case b == 3: // ctrl-c
			fmt.Println()
			return ""
		default:
			if b >= 32 {
				sb = append(sb, b)
				fmt.Printf("%c", b)
			}
		}
	}
}

type permissionPayload struct {
	ID       string                 `json:"id"`
	ToolName string                 `json:"tool_name"`
	Input    map[string]interface{} `json:"input"`
	Agent    string                 `json:"agent"`
}

// handlePermissionRequest prompts the user for a tool-permission decision.
// getKey, when non-nil, supplies keypresses from the stream's keyListener;
// otherwise stdin is read directly.
func handlePermissionRequest(rawJSON string, getKey func() byte) {
	var pr permissionPayload
	if err := json.Unmarshal([]byte(rawJSON), &pr); err != nil {
		fmt.Printf("\n%s⚠️  Malformed permission request%s\n", Yellow, Reset)
		return
	}

	inputStr := ""
	if len(pr.Input) > 0 {
		if b, err := json.MarshalIndent(pr.Input, "    ", "  "); err == nil {
			inputStr = string(b)
		}
	}

	fmt.Printf("\n%s%s╭─ Permission Request ──────────────────────────────────╮%s\n", Bold, Yellow, Reset)
	fmt.Printf("%s%s│%s  Tool: %s%s%s\n", Bold, Yellow, Reset, Bold+White, pr.ToolName, Reset)
	if inputStr != "" && inputStr != "null" && inputStr != "{}" {
		fmt.Printf("%s%s│%s  Input:\n    %s%s%s\n", Bold, Yellow, Reset, Dim+Gray, inputStr, Reset)
	}
	fmt.Printf("%s%s╰───────────────────────────────────────────────────────╯%s\n", Bold, Yellow, Reset)
	fmt.Printf("  %s[a]%s allow once  %s[A]%s allow session  %s[d]%s deny  %s[g]%s guide  %s[s]%s stop agent  > ",
		Green+Bold, Reset, Green+Bold, Reset, Red+Bold, Reset, Yellow+Bold, Reset, Red+Bold, Reset)

	behavior := "deny"
	scope := "once"
	message := ""

	readGuidance := func() {
		fmt.Printf("  %sGuidance for the agent:%s ", Yellow, Reset)
		if getKey != nil {
			message = strings.TrimSpace(readLineKeys(getKey))
		} else {
			scanner := bufio.NewScanner(os.Stdin)
			if scanner.Scan() {
				message = strings.TrimSpace(scanner.Text())
			}
		}
		behavior = "deny"
		if message != "" {
			message = "The user paused this tool call to give you guidance: " + message +
				" — adjust your approach accordingly and continue."
			fmt.Printf("  %s→ Guidance sent%s\n", Yellow, Reset)
		} else {
			fmt.Printf("  %s✗ Denied (no guidance entered)%s\n", Red, Reset)
		}
	}

	decideByte := func(b byte) {
		fmt.Println()
		switch b {
		case 'a':
			behavior, scope = "allow", "once"
			fmt.Printf("  %s✓ Allowed once%s\n", Green, Reset)
		case 'A':
			behavior, scope = "allow", "session"
			fmt.Printf("  %s✓ Allowed for session%s\n", Green, Reset)
		case 'd':
			behavior = "deny"
			fmt.Printf("  %s✗ Denied%s\n", Red, Reset)
		case 'g':
			readGuidance()
		case 's':
			behavior = "stop"
			fmt.Printf("  %s✗ Stopped agent%s\n", Red, Reset)
		default:
			behavior = "deny"
			fmt.Printf("  %s✗ Denied%s\n", Red, Reset)
		}
	}

	if getKey != nil {
		decideByte(getKey())
	} else if oldState, err := term.MakeRaw(int(os.Stdin.Fd())); err == nil {
		b := make([]byte, 1)
		os.Stdin.Read(b)
		term.Restore(int(os.Stdin.Fd()), oldState)
		decideByte(b[0])
	} else {
		// Fallback: line-based input when raw mode is unavailable
		scanner := bufio.NewScanner(os.Stdin)
		if scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if len(line) > 0 {
				switch line[0] {
				case 'a':
					behavior, scope = "allow", "once"
				case 'A':
					behavior, scope = "allow", "session"
				case 'd':
					behavior = "deny"
				case 'g':
					readGuidance()
				case 's':
					behavior = "stop"
				}
			}
		}
		fmt.Println()
	}

	decBody := map[string]string{"behavior": behavior, "scope": scope}
	if message != "" {
		decBody["message"] = message
	}
	decJSON, _ := json.Marshal(decBody)
	client := &http.Client{Timeout: 5 * time.Second}
	client.Post(
		fmt.Sprintf("http://localhost:8000/permission/%s/decide", pr.ID),
		"application/json",
		bytes.NewBuffer(decJSON),
	)
}

func sendChat(prompt, taskType, agent, sessionID string, parallel bool, cwd string) string {
	reqBody := ChatRequest{
		Prompt:         prompt,
		TaskType:       taskType,
		PreferredAgent: agent,
		SessionID:      sessionID,
		CWD:            cwd,
		Parallel:       parallel,
	}

	jsonData, err := json.Marshal(reqBody)
	if err != nil {
		fmt.Printf("%s  Error: %v%s\n", Red, err, Reset)
		return ""
	}

	stopSpinner, updateSpinner := startSpinner(agent)

	ctx, cancel := context.WithCancel(context.Background())
	setRequestCancel(cancel)
	defer func() {
		cancel()
		setRequestCancel(func() {})
	}()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, "http://localhost:8000/chat", bytes.NewBuffer(jsonData))
	if err != nil {
		stopSpinner()
		fmt.Printf("%s  Error building request: %v%s\n", Red, err, Reset)
		return ""
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		stopSpinner()
		if errors.Is(err, context.Canceled) {
			fmt.Printf("%s  Request cancelled.%s\n", Yellow, Reset)
			return ""
		}
		fmt.Printf("%s  Error connecting to LeadAgent daemon: %v%s\n", Red, err, Reset)
		return ""
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		stopSpinner()
		body, _ := io.ReadAll(resp.Body)
		fmt.Printf("%s  Error (%d): %s%s\n", Red, resp.StatusCode, string(body), Reset)
		return ""
	}

	// Spinner stays alive while we read the body — stop it on first real content line
	contentType := resp.Header.Get("Content-Type")
	if strings.Contains(contentType, "text/plain") {
		// Listen for [g] mid-run: pauses the agent at its next tool call.
		var permForward atomic.Pointer[chan byte]
		var getPromptKey func() byte
		kl := startKeyListener()
		if kl != nil {
			defer kl.Close()
			dispatchDone := make(chan struct{})
			defer close(dispatchDone)
			go func() {
				for {
					select {
					case <-dispatchDone:
						return
					case b := <-kl.keys:
						if chp := permForward.Load(); chp != nil {
							select {
							case *chp <- b:
							case <-dispatchDone:
								return
							}
							continue
						}
						if b == 'g' || b == 'G' {
							go postInterrupt(sessionID)
							fmt.Printf("\n%s⏸  Guide requested — the agent will pause at its next tool call so you can give guidance.%s\n", Yellow, Reset)
						}
					}
				}
			}()
			promptKeys := make(chan byte)
			getPromptKey = func() byte {
				permForward.Store(&promptKeys)
				b := <-promptKeys
				permForward.Store(nil)
				return b
			}
		}
		agentName := agent
		spinnerStopped := false
		progressHeaderShown := false

		var timingLines []string
		// blocks holds pointers: agentBlock embeds a strings.Builder, which
		// must never be copied after first use (its hidden self-pointer would
		// leak a stack address into the heap and crash the GC).
		type agentBlock struct {
			name    string
			content strings.Builder
		}
		var blocks []*agentBlock
		currentBlock := &agentBlock{name: agentName}
		isFanout := false

		reader := bufio.NewReader(resp.Body)
		for {
			line, err := reader.ReadString('\n')
			if line != "" {
				trimmed := strings.TrimSpace(line)

				// Status update from backend — update spinner label, never print
				if strings.HasPrefix(trimmed, "__STATUS__:") {
					msg := strings.TrimPrefix(trimmed, "__STATUS__:")
					if !spinnerStopped {
						updateSpinner(msg)
					}
					continue
				}

				if !spinnerStopped && strings.HasPrefix(line, "Using CLI Agent:") {
					parts := strings.SplitN(line, ":", 2)
					if len(parts) == 2 {
						agentName = strings.Fields(strings.TrimSpace(parts[1]))[0]
						currentBlock.name = agentName
					}
					stopSpinner()
					stopSpinner, updateSpinner = startSpinner(agentName)
					spinnerStopped = false
					continue
				}

				if strings.HasPrefix(trimmed, "__TIMING__:") {
					timingLines = append(timingLines, strings.TrimPrefix(trimmed, "__TIMING__:"))
					continue
				}

				if strings.HasPrefix(trimmed, "__PERMISSION_REQUEST__:") {
					payload := strings.TrimPrefix(trimmed, "__PERMISSION_REQUEST__:")
					if !spinnerStopped {
						stopSpinner()
						spinnerStopped = true
					}
					handlePermissionRequest(payload, getPromptKey)
					stopSpinner, updateSpinner = startSpinner(agentName)
					spinnerStopped = false
					continue
				}

				// Fan-out agent separator
				if strings.HasPrefix(trimmed, "◆  ") {
					isFanout = true
					blocks = append(blocks, currentBlock)
					newName := strings.ToLower(strings.TrimPrefix(trimmed, "◆  "))
					currentBlock = &agentBlock{name: newName}
					agentName = newName
					if !spinnerStopped {
						stopSpinner()
						spinnerStopped = true
					}
					stopSpinner, updateSpinner = startSpinner(agentName)
					spinnerStopped = false
					continue
				}
				if strings.HasPrefix(trimmed, "━━━") {
					continue
				}

				if !spinnerStopped {
					stopSpinner()
					spinnerStopped = true
				}
				if isProgressLine(line) {
					if !progressHeaderShown {
						color := getAgentColor(agentName)
						label := agentName
						if label == "" {
							label = "agent"
						}
						fmt.Printf("\n%s%s%s %s%s%s\n", Dim+Gray, "  ", color+Bold, strings.ToUpper(label), Reset+Dim+Gray+" processing...", Reset)
						progressHeaderShown = true
					}
					printLiveProgress(line)
				} else {
					currentBlock.content.WriteString(line)
				}
			}
			if err != nil {
				break
			}
		}
		blocks = append(blocks, currentBlock)

		if !spinnerStopped {
			stopSpinner()
		}
		if progressHeaderShown {
			fmt.Println()
		}

		if isFanout {
			agentTimings := map[string]string{}
			for _, tl := range timingLines {
				var tm map[string]interface{}
				if json.Unmarshal([]byte(tl), &tm) == nil {
					if ag, ok := tm["agent"].(string); ok {
						agentTimings[ag] = tl
					}
				}
			}
			for _, blk := range blocks {
				if blk.content.Len() == 0 {
					continue
				}
				printAgentHeader(blk.name)
				fmt.Print(renderMarkdown(blk.content.String()))
				if tj, ok := agentTimings[blk.name]; ok {
					printTimingLedger(tj)
				}
				fmt.Println()
			}
			if len(timingLines) > 0 {
				printTimingLedger(timingLines[len(timingLines)-1])
			}
		} else {
			printAgentHeader(agentName)
			fmt.Print(renderMarkdown(blocks[len(blocks)-1].content.String()))
			if len(timingLines) > 0 {
				printTimingLedger(timingLines[len(timingLines)-1])
			}
		}
		printSeparator()
		return blocks[len(blocks)-1].content.String()

	} else {
		body, _ := io.ReadAll(resp.Body)
		stopSpinner()
		var chatResp ChatResponse
		json.Unmarshal(body, &chatResp)

		printAgentHeader(chatResp.Agent)
		fmt.Print(renderMarkdown(chatResp.Response))
		if chatResp.Timing != nil {
			timingJSON, _ := json.Marshal(chatResp.Timing)
			printTimingLedger(string(timingJSON))
		}
		printSeparator()
		return chatResp.Response
	}
}

// ── Debate ────────────────────────────────────────────────────────────────────

func sendDebate(prompt string, rounds int, agents []string, cwd string, force bool) {
	reqBody := DebateRequest{
		Prompt: prompt,
		Rounds: rounds,
		Agents: agents,
		CWD:    cwd,
		Force:  force,
	}
	jsonData, _ := json.Marshal(reqBody)

	client := &http.Client{Timeout: 0}
	resp, err := client.Post("http://localhost:8000/debate", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		fmt.Printf("%s❌ Could not reach debate endpoint: %v%s\n", Red, err, Reset)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		fmt.Printf("%s❌ Debate endpoint error %d: %s%s\n", Red, resp.StatusCode, strings.TrimSpace(string(body)), Reset)
		return
	}

	type agentBlock struct {
		name    string
		content strings.Builder
	}

	const (
		markerRound      = "__DEBATE_ROUND__:"
		markerAgent      = "__DEBATE_AGENT__:"
		markerAgentEnd   = "__DEBATE_AGENT_END__:"
		markerUmpire     = "__DEBATE_UMPIRE__"
		markerUmpireEnd  = "__DEBATE_UMPIRE_END__"
		markerDropped    = "__DEBATE_DROPPED__:"
		markerSynthesis  = "__DEBATE_SYNTHESIS__"
		markerDone       = "__DEBATE_DONE__"
	)

	roundNum := 0
	inSynthesis := false
	inUmpire := false
	currentAgent := ""
	var currentBlock agentBlock
	var umpireBuf strings.Builder
	stopSpin := func() {}
	spinRunning := false

	flushBlock := func() {
		if currentBlock.name == "" || currentBlock.content.Len() == 0 {
			return
		}
		if spinRunning {
			stopSpin()
			spinRunning = false
		}
		printAgentHeader(currentBlock.name)
		fmt.Print(renderMarkdown(currentBlock.content.String()))
		fmt.Println()
		currentBlock = agentBlock{}
	}

	flushUmpire := func() {
		if umpireBuf.Len() == 0 {
			return
		}
		if spinRunning {
			stopSpin()
			spinRunning = false
		}
		q := strings.TrimSpace(umpireBuf.String())
		// Render umpire question in a distinct callout box
		fmt.Printf("\n%s┌─ Umpire ─────────────────────────────────────────────────%s\n", Bold+Yellow, Reset)
		fmt.Printf("%s│%s  ❓ %s%s%s\n", Bold+Yellow, Reset, Bold+White, q, Reset)
		fmt.Printf("%s└──────────────────────────────────────────────────────────%s\n\n", Bold+Yellow, Reset)
		umpireBuf.Reset()
	}

	reader := bufio.NewReader(resp.Body)
	for {
		line, err := reader.ReadString('\n')
		if line != "" {
			t := strings.TrimSpace(line)

			if strings.HasPrefix(t, markerRound) {
				flushBlock()
				roundNum, _ = strconv.Atoi(strings.TrimPrefix(t, markerRound))
				label := fmt.Sprintf(" ROUND %d ", roundNum)
				pad := (58 - len(label)) / 2
				header := strings.Repeat("━", pad) + label + strings.Repeat("━", 58-pad-len(label))
				fmt.Printf("\n%s%s%s\n", Bold+Cyan, header, Reset)
				_ = inSynthesis
				continue
			}

			if t == markerUmpire {
				flushBlock()
				inUmpire = true
				if spinRunning {
					stopSpin()
					spinRunning = false
				}
				stopSpin, _ = startSpinner("umpire")
				spinRunning = true
				continue
			}

			if t == markerUmpireEnd {
				inUmpire = false
				flushUmpire()
				continue
			}

			if t == markerSynthesis {
				flushBlock()
				inSynthesis = true
				w := strings.Repeat("━", 58)
				fmt.Printf("\n%s%s FINAL SYNTHESIS %s%s\n", Bold+Yellow, w[:20], w[20:], Reset)
				continue
			}

			if t == markerDone {
				flushBlock()
				fmt.Printf("\n%s%s Debate complete. %s%s\n\n", Bold+Green, strings.Repeat("─", 20), strings.Repeat("─", 20), Reset)
				break
			}

			if strings.HasPrefix(t, markerAgent) {
				flushBlock()
				currentAgent = strings.TrimPrefix(t, markerAgent)
				currentBlock = agentBlock{name: currentAgent}
				if spinRunning {
					stopSpin()
				}
				stopSpin, _ = startSpinner(currentAgent)
				spinRunning = true
				continue
			}

			if strings.HasPrefix(t, markerAgentEnd) {
				flushBlock()
				continue
			}

			if strings.HasPrefix(t, markerDropped) {
				dropped := strings.TrimPrefix(t, markerDropped)
				if spinRunning {
					stopSpin()
					spinRunning = false
				}
				fmt.Printf("%s⚠️  %s quota exhausted — dropped from remaining rounds.%s\n\n",
					Yellow+Bold, strings.ToUpper(dropped), Reset)
				continue
			}

			if inUmpire {
				umpireBuf.WriteString(line)
			} else {
				currentBlock.content.WriteString(line)
			}
		}
		if err != nil {
			break
		}
	}

	if spinRunning {
		stopSpin()
	}
	_ = roundNum
	_ = currentAgent
	_ = inSynthesis
}

func formatUptime(seconds float64) string {
	d := time.Duration(seconds) * time.Second
	h := int(d.Hours())
	m := int(d.Minutes()) % 60
	s := int(d.Seconds()) % 60
	if h > 0 {
		return fmt.Sprintf("%dh %dm", h, m)
	}
	if m > 0 {
		return fmt.Sprintf("%dm %ds", m, s)
	}
	return fmt.Sprintf("%ds", s)
}

func statusDot(ok bool) string {
	if ok {
		return Green + Bold + "●" + Reset
	}
	return Red + Bold + "●" + Reset
}

func handleHealth() {
	resp, err := http.Get("http://localhost:8000/health")
	if err != nil {
		fmt.Printf("\n%s%s┌─────────────────────────────────────────┐%s\n", Bold, Cyan, Reset)
		fmt.Printf("%s%s│         %s⚠️  SYSTEM OFFLINE             %s%s│%s\n", Bold, Cyan, Yellow, Bold, Cyan, Reset)
		fmt.Printf("%s%s└─────────────────────────────────────────┘%s\n", Bold, Cyan, Reset)
		fmt.Printf("\n  %s●%s  Backend daemon is not responding.\n", Red+Bold, Reset)
		fmt.Printf("    Start it:  %s./start_backend.sh%s\n\n", Cyan+Bold, Reset)
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	var h HealthResponse
	if err := json.Unmarshal(body, &h); err != nil {
		fmt.Printf("%sError parsing health response: %v%s\n", Red, err, Reset)
		return
	}

	overallColor := Green + Bold
	if h.Status == "degraded" {
		overallColor = Yellow + Bold
	} else if h.Status != "ok" {
		overallColor = Red + Bold
	}

	dbStatus := "ok"
	if s, ok := h.Components.Database["status"].(string); ok {
		dbStatus = s
	}
	entityCount := 0
	if ec, ok := h.Components.Database["entity_count"].(float64); ok {
		entityCount = int(ec)
	}
	memStatus := "ok"
	if s, ok := h.Components.MemoryService["status"].(string); ok {
		memStatus = s
	}

	fmt.Printf("\n%s%s┌─────────────────────────────────────────┐%s\n", Bold, Cyan, Reset)
	fmt.Printf("%s%s│%s         %s🧠  %sLEADAGENT %sHEALTH           %s%s│%s\n", Bold, Cyan, Reset, Purple+Bold, White+Bold, Cyan+Bold, Bold, Cyan, Reset)
	fmt.Printf("%s%s├─────────────────────────────────────────┤%s\n", Bold, Cyan, Reset)

	fmt.Printf("%s%s│%s  %-15s %s%-18s %s%s%s│%s\n",
		Bold, Cyan, Reset,
		"DAEMON", overallColor, strings.ToUpper(h.Status),
		Bold, Cyan, Reset, Reset) // Corrected argument count
	fmt.Printf("%s%s│%s  %-15s %s%-18s %s%s%s│%s\n",
		Bold, Cyan, Reset,
		"UPTIME", White, formatUptime(h.UptimeSec),
		Bold, Cyan, Reset, Reset)

	fmt.Printf("%s%s├─────────────────────────────────────────┤%s\n", Bold, Cyan, Reset)

	dbLabel := fmt.Sprintf("ok (%d nodes)", entityCount)
	if dbStatus != "ok" {
		dbLabel = "ERROR"
	}
	fmt.Printf("%s%s│%s  %s  %-12s %s%-16s %s%s%s│%s\n",
		Bold, Cyan, Reset,
		statusDot(dbStatus == "ok"), "GRAPH",
		Dim+Gray, dbLabel,
		Bold, Cyan, Reset, Reset)

	fmt.Printf("%s%s│%s  %s  %-12s %s%-16s %s%s%s│%s\n",
		Bold, Cyan, Reset,
		statusDot(memStatus == "ok"), "MEMORY",
		Dim+Gray, strings.ToUpper(memStatus),
		Bold, Cyan, Reset, Reset)

	fmt.Printf("%s%s├─────────────────────────────────────────┤%s\n", Bold, Cyan, Reset)

	for _, name := range []string{"claude", "gemini", "codex", "grok"} {
		ag, exists := h.Components.Agents[name]
		if !exists {
			ag = AgentHealth{}
		}

		status := "missing"
		if ag.Available {
			status = "online"
		} else if ag.Installed {
			if ag.SignedIn != nil && !*ag.SignedIn {
				status = "sign-in req"
			} else if ag.Exhausted {
				status = "limit hit"
			} else {
				status = "disabled"
			}
		}

		line := fmt.Sprintf("  %s  %-12s %s%-16s", 
			statusDot(ag.Available), strings.ToUpper(name),
			Dim+Gray, status)
		
		fmt.Printf("%s%s│%s%-39s%s%s│%s\n",
			Bold, Cyan, Reset, line, Bold, Cyan, Reset)
	}
	fmt.Printf("%s%s└─────────────────────────────────────────┘%s\n\n", Bold, Cyan, Reset)
}
func handleAuth() {
	fmt.Printf("\n%sLeadAgent uses subscription CLIs — no API keys needed.%s\n", Bold+Yellow, Reset)
	fmt.Printf("Log in to each CLI directly:\n")
	fmt.Printf("  %sclaude auth login%s\n", Cyan, Reset)
	fmt.Printf("  %sgemini auth login%s\n", Cyan, Reset)
	fmt.Printf("  %scodex login%s\n", Cyan, Reset)
	fmt.Printf("Use %sleadagent /health%s to check status after logging in.\n\n", Cyan, Reset)
}

type doctorCheck struct {
	Name   string `json:"name"`
	OK     bool   `json:"ok"`
	Detail string `json:"detail"`
}

type doctorResp struct {
	Status  string         `json:"status"`
	Summary string         `json:"summary"`
	Checks  []doctorCheck  `json:"checks"`
}

func handleDoctor() {
	fmt.Printf("\n%s%s╭─ LeadAgent Doctor ─────────────────────────╮%s\n", Bold, Cyan, Reset)

	// Local-side prerequisites (work even when backend is down).
	localTools := []string{"python3", "npm", "go"}
	for _, t := range localTools {
		_, err := exec.LookPath(t)
		printDoctorRow(fmt.Sprintf("tool:%s", t), err == nil,
			map[bool]string{true: "available", false: "missing"}[err == nil])
	}

	// Backend-side full doctor.
	client := &http.Client{Timeout: 6 * time.Second}
	resp, err := client.Get("http://localhost:8000/doctor")
	if err != nil {
		printDoctorRow("backend", false, "offline — start with ./start_backend.sh")
		fmt.Printf("%s%s╰────────────────────────────────────────────╯%s\n\n", Bold, Cyan, Reset)
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var d doctorResp
	if err := json.Unmarshal(body, &d); err != nil {
		printDoctorRow("backend", false, "unparseable /doctor response")
		fmt.Printf("%s%s╰────────────────────────────────────────────╯%s\n\n", Bold, Cyan, Reset)
		return
	}
	for _, c := range d.Checks {
		// Skip duplicates of the local-side tool checks we already showed.
		if strings.HasPrefix(c.Name, "tool:") {
			skip := false
			for _, t := range localTools {
				if c.Name == "tool:"+t {
					skip = true
					break
				}
			}
			if skip {
				continue
			}
		}
		printDoctorRow(c.Name, c.OK, c.Detail)
	}
	overall := Green + d.Status + Reset
	if d.Status != "ok" {
		overall = Yellow + d.Status + Reset
	}
	fmt.Printf("%s%s├────────────────────────────────────────────┤%s\n", Bold, Cyan, Reset)
	fmt.Printf("%s%s│%s  %-12s %s  (%s)\n", Bold, Cyan, Reset, "STATUS", overall, d.Summary)
	fmt.Printf("%s%s╰────────────────────────────────────────────╯%s\n\n", Bold, Cyan, Reset)
}

func printDoctorRow(name string, ok bool, detail string) {
	dot := statusDot(ok)
	col := Green
	if !ok {
		col = Red
	}
	if len(detail) > 28 {
		detail = detail[:25] + "..."
	}
	fmt.Printf("%s%s│%s  %s  %-22s %s%-28s%s\n",
		Bold, Cyan, Reset, dot, name, col, detail, Reset)
}

func handleQuery(cypher string) {
	reqBody := map[string]string{"cypher": cypher}
	jsonData, _ := json.Marshal(reqBody)

	resp, err := http.Post("http://localhost:8000/memory/query", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		fmt.Printf("Error: %v\n", err)
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	fmt.Printf("Graph Result: %s\n", string(body))
}
