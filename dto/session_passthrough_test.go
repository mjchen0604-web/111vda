package dto

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
)

func TestGeneralOpenAIRequestPreservesSessionFields(t *testing.T) {
	req := &GeneralOpenAIRequest{
		Model:          "gpt-5.4-fast-xhigh",
		SessionID:      "sess-123",
		ConversationID: "conv-456",
	}

	data, err := common.Marshal(req)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded map[string]any
	if err := common.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded["session_id"] != "sess-123" {
		t.Fatalf("expected session_id to survive, got %v", decoded["session_id"])
	}
	if decoded["conversation_id"] != "conv-456" {
		t.Fatalf("expected conversation_id to survive, got %v", decoded["conversation_id"])
	}
}

func TestOpenAIResponsesRequestPreservesSessionFields(t *testing.T) {
	req := OpenAIResponsesRequest{
		Model:          "gpt-5.4-fast-xhigh",
		SessionID:      "sess-123",
		ConversationID: "conv-456",
	}

	data, err := common.Marshal(req)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded map[string]any
	if err := common.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded["session_id"] != "sess-123" {
		t.Fatalf("expected session_id to survive, got %v", decoded["session_id"])
	}
	if decoded["conversation_id"] != "conv-456" {
		t.Fatalf("expected conversation_id to survive, got %v", decoded["conversation_id"])
	}
}

func TestClaudeRequestPreservesSessionFields(t *testing.T) {
	req := &ClaudeRequest{
		Model:          "claude-opus-4-6",
		SessionID:      "sess-123",
		ConversationID: "conv-456",
	}

	data, err := common.Marshal(req)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded map[string]any
	if err := common.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded["session_id"] != "sess-123" {
		t.Fatalf("expected session_id to survive, got %v", decoded["session_id"])
	}
	if decoded["conversation_id"] != "conv-456" {
		t.Fatalf("expected conversation_id to survive, got %v", decoded["conversation_id"])
	}
}
