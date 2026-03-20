package codex

import (
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
)

func TestMaybeCompactResponsesRequestCompactsLongInput(t *testing.T) {
	input, _ := common.Marshal([]map[string]any{
		{"type": "message", "role": "user", "content": []map[string]any{{"type": "input_text", "text": "turn-0"}}},
		{"type": "message", "role": "assistant", "content": []map[string]any{{"type": "output_text", "text": "turn-1"}}},
		{"type": "message", "role": "user", "content": []map[string]any{{"type": "input_text", "text": "turn-2"}}},
		{"type": "message", "role": "assistant", "content": []map[string]any{{"type": "output_text", "text": "turn-3"}}},
		{"type": "message", "role": "user", "content": []map[string]any{{"type": "input_text", "text": "turn-4"}}},
		{"type": "message", "role": "assistant", "content": []map[string]any{{"type": "output_text", "text": "turn-5"}}},
		{"type": "message", "role": "user", "content": []map[string]any{{"type": "input_text", "text": "turn-6"}}},
		{"type": "message", "role": "assistant", "content": []map[string]any{{"type": "output_text", "text": "turn-7"}}},
	})
	contextManagement, _ := common.Marshal(map[string]any{
		"enabled":               true,
		"max_input_items":       6,
		"preserve_recent_items": 2,
		"max_summary_chars":     1200,
	})
	instructions, _ := common.Marshal("Base instructions")

	req := &dto.OpenAIResponsesRequest{
		Model:             "gpt-5.4",
		Input:             input,
		Instructions:      instructions,
		ContextManagement: contextManagement,
	}

	if err := maybeCompactResponsesRequest(req); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	var compactedInput []map[string]any
	if err := common.Unmarshal(req.Input, &compactedInput); err != nil {
		t.Fatalf("unexpected input decode error: %v", err)
	}
	if len(compactedInput) != 2 {
		t.Fatalf("expected 2 recent items after compaction, got %d", len(compactedInput))
	}

	var compactedInstructions string
	if err := common.Unmarshal(req.Instructions, &compactedInstructions); err != nil {
		t.Fatalf("unexpected instructions decode error: %v", err)
	}
	if !strings.Contains(compactedInstructions, compactionSummaryHeader) {
		t.Fatalf("expected compacted summary header, got %q", compactedInstructions)
	}
	if !strings.Contains(compactedInstructions, "turn-0") {
		t.Fatalf("expected early turns in summary, got %q", compactedInstructions)
	}
}

func TestMaybeCompactResponsesRequestDisabled(t *testing.T) {
	input, _ := common.Marshal([]map[string]any{
		{"type": "message", "role": "user", "content": []map[string]any{{"type": "input_text", "text": "turn-0"}}},
		{"type": "message", "role": "assistant", "content": []map[string]any{{"type": "output_text", "text": "turn-1"}}},
		{"type": "message", "role": "user", "content": []map[string]any{{"type": "input_text", "text": "turn-2"}}},
		{"type": "message", "role": "assistant", "content": []map[string]any{{"type": "output_text", "text": "turn-3"}}},
		{"type": "message", "role": "user", "content": []map[string]any{{"type": "input_text", "text": "turn-4"}}},
		{"type": "message", "role": "assistant", "content": []map[string]any{{"type": "output_text", "text": "turn-5"}}},
		{"type": "message", "role": "user", "content": []map[string]any{{"type": "input_text", "text": "turn-6"}}},
	})
	contextManagement, _ := common.Marshal(map[string]any{"enabled": false})
	instructions, _ := common.Marshal("Base instructions")

	req := &dto.OpenAIResponsesRequest{
		Model:             "gpt-5.4",
		Input:             input,
		Instructions:      instructions,
		ContextManagement: contextManagement,
	}

	if err := maybeCompactResponsesRequest(req); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	var compactedInput []map[string]any
	if err := common.Unmarshal(req.Input, &compactedInput); err != nil {
		t.Fatalf("unexpected input decode error: %v", err)
	}
	if len(compactedInput) != 7 {
		t.Fatalf("expected input to stay unchanged, got %d items", len(compactedInput))
	}
}
