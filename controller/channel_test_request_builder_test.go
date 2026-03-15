package controller

import (
	"encoding/json"
	"testing"

	"github.com/QuantumNous/new-api/dto"
)

func TestBuildResponsesTestInputUsesMessageBlocks(t *testing.T) {
	raw := buildResponsesTestInput("hello")

	var payload []map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatalf("unmarshal input: %v", err)
	}
	if len(payload) != 1 {
		t.Fatalf("unexpected item count: %d", len(payload))
	}
	if payload[0]["type"] != "message" {
		t.Fatalf("unexpected outer type: %v", payload[0]["type"])
	}
	if payload[0]["role"] != "user" {
		t.Fatalf("unexpected role: %v", payload[0]["role"])
	}
	content, ok := payload[0]["content"].([]any)
	if !ok || len(content) != 1 {
		t.Fatalf("unexpected content: %#v", payload[0]["content"])
	}
	part, ok := content[0].(map[string]any)
	if !ok {
		t.Fatalf("unexpected content part: %#v", content[0])
	}
	if part["type"] != "input_text" || part["text"] != "hello" {
		t.Fatalf("unexpected content part values: %#v", part)
	}
}

func TestBuildResponsesTestRequestSetsOfficialStyleDefaults(t *testing.T) {
	req := buildResponsesTestRequest("gpt-5", "hello", true)

	if req.Model != "gpt-5" {
		t.Fatalf("unexpected model: %s", req.Model)
	}
	if req.Stream == nil || !*req.Stream {
		t.Fatalf("stream flag not set")
	}
	if req.StreamOptions == nil || !req.StreamOptions.IncludeUsage {
		t.Fatalf("stream options missing include_usage")
	}
	if req.MaxOutputTokens == nil || *req.MaxOutputTokens != 64 {
		t.Fatalf("unexpected max_output_tokens: %#v", req.MaxOutputTokens)
	}
	if string(req.Store) != "false" {
		t.Fatalf("unexpected store value: %s", string(req.Store))
	}
	if len(req.Instructions) == 0 {
		t.Fatalf("instructions should not be empty")
	}

	var decoded dto.OpenAIResponsesRequest
	if err := json.Unmarshal(mustMarshal(t, req), &decoded); err != nil {
		t.Fatalf("roundtrip request: %v", err)
	}
	if len(decoded.Input) == 0 {
		t.Fatalf("input should survive roundtrip")
	}
}

func mustMarshal(t *testing.T, value any) []byte {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal value: %v", err)
	}
	return raw
}
