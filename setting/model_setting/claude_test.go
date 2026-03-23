package model_setting

import "testing"

func TestClaudeSkinModelsUse128kDefaultMaxTokens(t *testing.T) {
	settings := GetClaudeSettings()
	cases := []string{
		"claude-opus-4-6",
		"claude-opus-4-5",
		"claude-sonnet-4-6",
		"claude-sonnet-4-5",
		"claude-haiku-4-5",
		"claude-haiku-3-5",
	}
	for _, model := range cases {
		if got := settings.GetDefaultMaxTokens(model); got != 128000 {
			t.Fatalf("GetDefaultMaxTokens(%q) = %d, want 128000", model, got)
		}
	}
}
