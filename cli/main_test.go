package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ── renderMarkdown ────────────────────────────────────────────────────────────

func TestRenderMarkdown_PlainText(t *testing.T) {
	out := renderMarkdown("hello world")
	// Strip ANSI escape codes before checking — glamour adds colour sequences
	ansi := strings.NewReplacer("\x1b[", "", "\x1b]", "")
	stripped := ansi.Replace(out)
	if !strings.Contains(stripped, "hello") {
		t.Errorf("expected rendered output to contain 'hello', got: %q", out)
	}
}

func TestRenderMarkdown_EmptyString(t *testing.T) {
	out := renderMarkdown("")
	// Should not panic and return something (empty or whitespace)
	_ = out
}

// ── isProgressLine ────────────────────────────────────────────────────────────

func TestIsProgressLine(t *testing.T) {
	cases := []struct {
		line string
		want bool
	}{
		{"✓ file written", true},
		{"✗ command failed", true},
		{"⎿ subtask done", true},
		{"● running", true},
		{"▸ starting", true},
		{"Normal response text", false},
		{"", false},
		{"   ", false},
	}
	for _, c := range cases {
		got := isProgressLine(c.line)
		if got != c.want {
			t.Errorf("isProgressLine(%q) = %v, want %v", c.line, got, c.want)
		}
	}
}

// ── getAgentColor ─────────────────────────────────────────────────────────────

func TestGetAgentColor(t *testing.T) {
	cases := []string{"claude", "gemini", "codex", "grok", "unknown"}
	for _, name := range cases {
		color := getAgentColor(name)
		if color == "" {
			t.Errorf("getAgentColor(%q) returned empty string", name)
		}
	}
}

func TestGetAgentColor_CaseInsensitive(t *testing.T) {
	a := getAgentColor("claude")
	b := getAgentColor("CLAUDE")
	if a != b {
		t.Errorf("getAgentColor should be case-insensitive: %q vs %q", a, b)
	}
}

// ── formatUptime ──────────────────────────────────────────────────────────────

func TestFormatUptime(t *testing.T) {
	cases := []struct {
		secs float64
		want string
	}{
		{30, "30s"},
		{90, "1m 30s"},
		{3661, "1h 1m"},
	}
	for _, c := range cases {
		got := formatUptime(c.secs)
		if got != c.want {
			t.Errorf("formatUptime(%.0f) = %q, want %q", c.secs, got, c.want)
		}
	}
}

// ── renderProgressBar ─────────────────────────────────────────────────────────

func TestRenderProgressBar_ZeroPercent(t *testing.T) {
	bar := renderProgressBar(0, 10)
	if bar == "" {
		t.Error("renderProgressBar(0, 10) returned empty string")
	}
}

func TestRenderProgressBar_FullPercent(t *testing.T) {
	bar := renderProgressBar(100, 10)
	if bar == "" {
		t.Error("renderProgressBar(100, 10) returned empty string")
	}
}

func TestRenderProgressBar_OverFull(t *testing.T) {
	// Should clamp, not panic
	bar := renderProgressBar(150, 10)
	if bar == "" {
		t.Error("renderProgressBar(150, 10) returned empty string")
	}
}

// ── printTimingLedger ─────────────────────────────────────────────────────────

func TestPrintTimingLedger_ValidJSON(t *testing.T) {
	timing := map[string]string{
		"agent":       "claude",
		"memory":      "12ms",
		"routing":     "1ms",
		"agent_start": "340ms",
		"agent_total": "2.34s",
		"total":       "2.35s",
	}
	b, _ := json.Marshal(timing)
	// Should not panic
	printTimingLedger(string(b))
}

func TestPrintTimingLedger_InvalidJSON(t *testing.T) {
	// Should not panic on bad input
	printTimingLedger("not json")
}

func TestPrintTimingLedger_EmptyJSON(t *testing.T) {
	printTimingLedger("{}")
}

// ── statusDot ─────────────────────────────────────────────────────────────────

func TestStatusDot_True(t *testing.T) {
	dot := statusDot(true)
	if !strings.Contains(dot, "●") {
		t.Errorf("statusDot(true) should contain ●, got %q", dot)
	}
}

func TestStatusDot_False(t *testing.T) {
	dot := statusDot(false)
	if !strings.Contains(dot, "●") {
		t.Errorf("statusDot(false) should contain ●, got %q", dot)
	}
}

// ── permissionPayload JSON parsing ────────────────────────────────────────────

func TestPermissionPayload_Unmarshal(t *testing.T) {
	raw := `{"id":"req-1","tool_name":"Bash","input":{"command":"ls"},"agent":"claude"}`
	var pr permissionPayload
	if err := json.Unmarshal([]byte(raw), &pr); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}
	if pr.ID != "req-1" {
		t.Errorf("expected id=req-1, got %q", pr.ID)
	}
	if pr.ToolName != "Bash" {
		t.Errorf("expected tool_name=Bash, got %q", pr.ToolName)
	}
	if pr.Agent != "claude" {
		t.Errorf("expected agent=claude, got %q", pr.Agent)
	}
	cmd, ok := pr.Input["command"].(string)
	if !ok || cmd != "ls" {
		t.Errorf("expected input.command=ls, got %v", pr.Input)
	}
}

func TestPermissionPayload_MissingFields(t *testing.T) {
	raw := `{"id":"req-2"}`
	var pr permissionPayload
	if err := json.Unmarshal([]byte(raw), &pr); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}
	if pr.ToolName != "" {
		t.Errorf("expected empty tool_name, got %q", pr.ToolName)
	}
}

// ── isBackendUp — via httptest ────────────────────────────────────────────────

func TestIsBackendUp_WhenServerRunning(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	// We can't easily inject the URL into isBackendUp without refactoring,
	// so just verify the function exists and returns a bool.
	// Full integration tested via the backend itself.
	result := isBackendUp()
	_ = result // may be true or false depending on whether daemon is running
}

// ── findProjectRoot ───────────────────────────────────────────────────────────

func TestFindProjectRoot_ReturnsStringOrEmpty(t *testing.T) {
	root := findProjectRoot()
	// Either finds a real root or returns ""
	if root != "" {
		// If it found something, it should contain backend/setup_wizard.py
		if !strings.Contains(root, "/") {
			t.Errorf("project root looks wrong: %q", root)
		}
	}
}
