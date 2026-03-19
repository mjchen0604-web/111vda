package openai

import (
	"strings"
	"testing"

	relaycommon "github.com/QuantumNous/new-api/relay/common"
)

func TestOaiResponsesHandler_FallbackUsageFromOutputText(t *testing.T) {
	ctx, _ := newResponsesStreamTestContext()
	info := &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "claude-3-5-sonnet",
		},
	}
	info.SetEstimatePromptTokens(12)

	resp := newResponsesStreamHTTPResponse(`{"id":"resp_1","object":"response","model":"claude-3-5-sonnet","output":[{"type":"message","id":"msg_1","role":"assistant","content":[{"type":"output_text","text":"hello from codex","annotations":[]}]}]}`)

	usage, err := OaiResponsesHandler(ctx, info, resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if usage == nil {
		t.Fatalf("expected usage, got nil")
	}
	if usage.PromptTokens != 12 {
		t.Fatalf("expected prompt tokens 12, got %d", usage.PromptTokens)
	}
	if usage.CompletionTokens <= 0 || usage.TotalTokens <= 0 {
		t.Fatalf("expected fallback usage to be populated, got %#v", usage)
	}
}

func TestOaiResponsesStreamHandler_FallbackUsageFromOutputTextDone(t *testing.T) {
	ctx, _ := newResponsesStreamTestContext()
	info := &relaycommon.RelayInfo{
		DisablePing: true,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "claude-3-5-sonnet",
		},
	}
	info.SetEstimatePromptTokens(10)

	resp := newResponsesStreamHTTPResponse(strings.Join([]string{
		`data: {"type":"response.created","response":{"model":"claude-3-5-sonnet","created_at":123}}`,
		``,
		`data: {"type":"response.output_text.done","item_id":"msg_1","content_index":0,"text":"hello from output_text.done"}`,
		``,
		`data: {"type":"response.completed","response":{"model":"claude-3-5-sonnet","created_at":123}}`,
		``,
	}, "\n"))

	usage, err := OaiResponsesStreamHandler(ctx, info, resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if usage == nil {
		t.Fatalf("expected usage, got nil")
	}
	if usage.PromptTokens != 10 {
		t.Fatalf("expected prompt tokens 10, got %d", usage.PromptTokens)
	}
	if usage.CompletionTokens <= 0 || usage.TotalTokens <= 0 {
		t.Fatalf("expected fallback usage to be populated, got %#v", usage)
	}
}

func TestOaiResponsesStreamHandler_FallbackUsageFromOutputItemDoneMessage(t *testing.T) {
	ctx, _ := newResponsesStreamTestContext()
	info := &relaycommon.RelayInfo{
		DisablePing: true,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "claude-3-5-sonnet",
		},
	}
	info.SetEstimatePromptTokens(9)

	resp := newResponsesStreamHTTPResponse(strings.Join([]string{
		`data: {"type":"response.created","response":{"model":"claude-3-5-sonnet","created_at":123}}`,
		``,
		`data: {"type":"response.output_item.done","item":{"type":"message","id":"msg_1","role":"assistant","content":[{"type":"output_text","text":"hello from output item done"}]}}`,
		``,
		`data: {"type":"response.completed","response":{"model":"claude-3-5-sonnet","created_at":123}}`,
		``,
	}, "\n"))

	usage, err := OaiResponsesStreamHandler(ctx, info, resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if usage == nil {
		t.Fatalf("expected usage, got nil")
	}
	if usage.PromptTokens != 9 {
		t.Fatalf("expected prompt tokens 9, got %d", usage.PromptTokens)
	}
	if usage.CompletionTokens <= 0 || usage.TotalTokens <= 0 {
		t.Fatalf("expected fallback usage to be populated, got %#v", usage)
	}
}
