package service

import (
	"encoding/json"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/service/openaicompat"
)

func TestClaudeMCPToolNamesAreSanitizedAndRestored(t *testing.T) {
	info := &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{},
	}
	req := dto.ClaudeRequest{
		Model: "gpt-5.4-fast-low",
		Tools: []dto.Tool{
			{
				Name:        "mcp__CherryHub__list",
				Description: "List docs",
				InputSchema: map[string]interface{}{"type": "object"},
			},
		},
	}

	openAIReq, err := ClaudeToOpenAIRequest(req, info)
	if err != nil {
		t.Fatalf("ClaudeToOpenAIRequest failed: %v", err)
	}
	if len(openAIReq.Tools) != 1 {
		t.Fatalf("expected 1 tool, got %d", len(openAIReq.Tools))
	}

	safeName := openAIReq.Tools[0].Function.Name
	if safeName == "mcp__CherryHub__list" {
		t.Fatalf("expected sanitized tool name, got original %q", safeName)
	}

	resp := &dto.OpenAITextResponse{
		Model: "gpt-5.4-fast-low",
	}
	message := dto.Message{Role: "assistant"}
	message.SetToolCalls([]dto.ToolCallRequest{
		{
			ID:   "call_1",
			Type: "function",
			Function: dto.FunctionRequest{
				Name:      safeName,
				Arguments: `{"q":"x"}`,
			},
		},
	})
	resp.Choices = []dto.OpenAITextResponseChoice{
		{
			FinishReason: "tool_calls",
			Message:      message,
		},
	}

	claudeResp := ResponseOpenAI2Claude(resp, info)
	if len(claudeResp.Content) != 1 {
		t.Fatalf("expected 1 content block, got %d", len(claudeResp.Content))
	}
	if claudeResp.Content[0].Name != "mcp__CherryHub__list" {
		t.Fatalf("expected restored tool name, got %q", claudeResp.Content[0].Name)
	}
}

func TestClaudeMCPServersAreForwardedToResponses(t *testing.T) {
	info := &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{},
	}
	req := dto.ClaudeRequest{
		Model:      "gpt-5.4-fast-low",
		McpServers: json.RawMessage(`[{"name":"CherryHub","type":"url","url":"https://example.com/mcp"}]`),
	}

	openAIReq, err := ClaudeToOpenAIRequest(req, info)
	if err != nil {
		t.Fatalf("ClaudeToOpenAIRequest failed: %v", err)
	}
	if string(openAIReq.McpServers) == "" {
		t.Fatalf("expected mcp_servers on OpenAI request")
	}

	responsesReq, err := openaicompat.ChatCompletionsRequestToResponsesRequest(openAIReq, info)
	if err != nil {
		t.Fatalf("ChatCompletionsRequestToResponsesRequest failed: %v", err)
	}
	if string(responsesReq.McpServers) == "" {
		t.Fatalf("expected mcp_servers on responses request")
	}
}

func TestOpenAIMCPToolNamesAreSanitizedAndRestored(t *testing.T) {
	info := &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{},
	}
	openAIReq := &dto.GeneralOpenAIRequest{
		Model: "gpt-5.4-fast-low",
		Messages: []dto.Message{
			{
				Role:    "user",
				Content: "use mcp",
			},
		},
		Tools: []dto.ToolCallRequest{
			{
				Type: "function",
				Function: dto.FunctionRequest{
					Name:        "mcp__CherryHub__list",
					Description: "List CherryHub",
					Parameters: map[string]interface{}{
						"type": "object",
					},
				},
			},
		},
		ToolChoice: map[string]any{
			"type": "function",
			"function": map[string]any{
				"name": "mcp__CherryHub__list",
			},
		},
	}

	responsesReq, err := openaicompat.ChatCompletionsRequestToResponsesRequest(openAIReq, info)
	if err != nil {
		t.Fatalf("ChatCompletionsRequestToResponsesRequest failed: %v", err)
	}

	var tools []map[string]any
	if err := common.Unmarshal(responsesReq.Tools, &tools); err != nil {
		t.Fatalf("failed to decode tools: %v", err)
	}
	if len(tools) != 1 {
		t.Fatalf("expected 1 tool, got %d", len(tools))
	}
	if tools[0]["name"] == "mcp__CherryHub__list" {
		t.Fatalf("expected sanitized OpenAI tool name")
	}

	var toolChoice map[string]any
	if err := common.Unmarshal(responsesReq.ToolChoice, &toolChoice); err != nil {
		t.Fatalf("failed to decode tool_choice: %v", err)
	}
	if toolChoice["name"] == "mcp__CherryHub__list" {
		t.Fatalf("expected sanitized OpenAI tool_choice name")
	}

	resp := &dto.OpenAIResponsesResponse{
		Model: "gpt-5.4-fast-low",
		Output: []dto.ResponsesOutput{
			{
				Type:      "function_call",
				ID:        "fc_1",
				CallId:    "call_1",
				Name:      "tool_mcp__CherryHub__list",
				Arguments: `{"q":"x"}`,
			},
		},
	}
	chatResp, _, err := openaicompat.ResponsesResponseToChatCompletionsResponse(resp, "chatcmpl-test", info)
	if err != nil {
		t.Fatalf("ResponsesResponseToChatCompletionsResponse failed: %v", err)
	}
	toolCalls := chatResp.Choices[0].Message.ParseToolCalls()
	if len(toolCalls) != 1 {
		t.Fatalf("expected 1 restored tool call, got %d", len(toolCalls))
	}
	if toolCalls[0].Function.Name != "mcp__CherryHub__list" {
		t.Fatalf("expected restored OpenAI tool name, got %s", toolCalls[0].Function.Name)
	}
}
