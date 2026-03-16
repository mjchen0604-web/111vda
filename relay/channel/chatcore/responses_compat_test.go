package chatcore

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/gin-gonic/gin"
)

func TestResponsesRequestToChatCompletionsRequest(t *testing.T) {
	stream := true
	temperature := 0.2
	toolChoice, _ := common.Marshal("auto")
	tools, _ := common.Marshal([]map[string]any{
		{
			"type": "function",
			"name": "test_tool",
			"parameters": map[string]any{
				"type": "object",
			},
		},
	})
	input, _ := common.Marshal([]map[string]any{
		{
			"role":    "developer",
			"content": "you are a helper",
		},
		{
			"role": "user",
			"content": []map[string]any{
				{"type": "input_text", "text": "look at this image"},
				{"type": "input_image", "image_url": "https://example.com/image.png"},
			},
		},
		{
			"role": "assistant",
			"content": []map[string]any{
				{"type": "output_text", "text": "this is an image"},
			},
		},
	})

	req := dto.OpenAIResponsesRequest{
		Model:       "gpt-5.4-fast",
		Input:       input,
		Stream:      &stream,
		Temperature: &temperature,
		ToolChoice:  toolChoice,
		Tools:       tools,
	}

	chatReq, err := responsesRequestToChatCompletionsRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if chatReq["model"] != "gpt-5.4-fast" {
		t.Fatalf("unexpected model: %#v", chatReq["model"])
	}
	if streamValue, ok := chatReq["stream"].(bool); !ok || !streamValue {
		t.Fatalf("expected stream=true, got %#v", chatReq["stream"])
	}
	messagesPayload, ok := chatReq["messages"].([]any)
	if !ok || len(messagesPayload) != 3 {
		t.Fatalf("expected 3 messages, got %#v", chatReq["messages"])
	}
	firstMessage, _ := messagesPayload[0].(map[string]any)
	if firstMessage["role"] != "developer" || firstMessage["content"] != "you are a helper" {
		t.Fatalf("unexpected developer message: %#v", firstMessage)
	}
	secondMessage, _ := messagesPayload[1].(map[string]any)
	contentPayload, ok := secondMessage["content"].([]any)
	if !ok || len(contentPayload) != 2 {
		t.Fatalf("expected user mixed content, got %#v", secondMessage["content"])
	}
	if chatReq["tool_choice"] != "auto" {
		t.Fatalf("expected tool_choice=auto, got %#v", chatReq["tool_choice"])
	}
	toolsPayload, ok := chatReq["tools"].([]any)
	if !ok || len(toolsPayload) != 1 {
		t.Fatalf("unexpected tools: %#v", chatReq["tools"])
	}
}

func TestResponsesRequestToChatCompletionsRequestStripsBuiltInSearchTools(t *testing.T) {
	toolChoice, _ := common.Marshal("auto")
	tools, _ := common.Marshal([]map[string]any{
		{"type": "web_search"},
		{
			"type":       "function",
			"name":       "mcp__CherryHub__list",
			"parameters": map[string]any{"type": "object"},
		},
	})
	input, _ := common.Marshal([]map[string]any{
		{
			"role": "user",
			"content": []map[string]any{
				{"type": "input_text", "text": "today news"},
			},
		},
	})

	chatReq, err := responsesRequestToChatCompletionsRequest(dto.OpenAIResponsesRequest{
		Model:      "gpt-5.4",
		Input:      input,
		ToolChoice: toolChoice,
		Tools:      tools,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	toolsPayload, ok := chatReq["tools"].([]any)
	if !ok || len(toolsPayload) != 1 {
		t.Fatalf("expected only non-built-in tool to remain, got %#v", chatReq["tools"])
	}
}

func TestChatcoreAdaptorConvertResponsesRequestRewritesPath(t *testing.T) {
	adaptor := &Adaptor{}
	info := &relaycommon.RelayInfo{
		RelayMode: relayconstant.RelayModeResponses,
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelBaseUrl: "http://127.0.0.1:8787",
		},
	}

	input, _ := common.Marshal([]map[string]any{
		{
			"role":    "user",
			"content": "hello",
		},
	})
	converted, err := adaptor.ConvertOpenAIResponsesRequest(nil, info, dto.OpenAIResponsesRequest{
		Model: "gpt-5.4",
		Input: input,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if info.RequestURLPath != "/v1/chat/completions" {
		t.Fatalf("expected rewritten path, got %s", info.RequestURLPath)
	}
	if _, ok := converted.(map[string]any); !ok {
		t.Fatalf("expected map[string]any, got %T", converted)
	}
}

func TestChatCompletionsToResponsesResponse(t *testing.T) {
	chatResp := &dto.OpenAITextResponse{
		Id:      "chatcmpl_123",
		Object:  "chat.completion",
		Created: 123,
		Model:   "gpt-5.4-fast",
		Choices: []dto.OpenAITextResponseChoice{
			{
				Index: 0,
				Message: dto.Message{
					Role:    "assistant",
					Content: "image description",
				},
				FinishReason: "stop",
			},
		},
		Usage: dto.Usage{
			PromptTokens:     10,
			CompletionTokens: 5,
			TotalTokens:      15,
		},
	}

	resp := chatCompletionsToResponsesResponse(chatResp)
	if resp == nil {
		t.Fatal("expected response")
	}
	if resp.Object != "response" || resp.ID != "chatcmpl_123" {
		t.Fatalf("unexpected response wrapper: %#v", resp)
	}
	if len(resp.Output) != 1 {
		t.Fatalf("expected 1 output item, got %#v", resp.Output)
	}
	if resp.Output[0].Type != "message" || len(resp.Output[0].Content) != 1 || resp.Output[0].Content[0].Text != "image description" {
		t.Fatalf("unexpected output content: %#v", resp.Output)
	}
}

func TestResponsesCompatStreamHandlerEmitsAddedEvents(t *testing.T) {
	gin.SetMode(gin.TestMode)
	constant.StreamingTimeout = 1

	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	ctx.Request = httptest.NewRequest(http.MethodGet, "/", nil)
	ctx.Set(common.RequestIdKey, "chatcore_resp_stream")

	streamBody := strings.Join([]string{
		`data: {"id":"chatcmpl_123","object":"chat.completion.chunk","created":123,"model":"gpt-5.4-fast","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}`,
		``,
		`data: {"id":"chatcmpl_123","object":"chat.completion.chunk","created":123,"model":"gpt-5.4-fast","choices":[{"index":0,"delta":{"content":"image description"},"finish_reason":null}]}`,
		``,
		`data: {"id":"chatcmpl_123","object":"chat.completion.chunk","created":123,"model":"gpt-5.4-fast","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}`,
		``,
	}, "\n")

	resp := &http.Response{
		StatusCode: http.StatusOK,
		Body:       io.NopCloser(strings.NewReader(streamBody)),
		Header:     make(http.Header),
	}
	info := &relaycommon.RelayInfo{}

	usage, err := responsesCompatStreamHandler(ctx, resp, info)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if usage == nil || usage.TotalTokens != 15 {
		t.Fatalf("unexpected usage: %#v", usage)
	}

	body := recorder.Body.String()
	if !strings.Contains(body, `"type":"response.output_item.added"`) {
		t.Fatalf("expected response.output_item.added, got %s", body)
	}
	if !strings.Contains(body, `"type":"response.content_part.added"`) {
		t.Fatalf("expected response.content_part.added, got %s", body)
	}
	if !strings.Contains(body, `"type":"response.output_text.done"`) {
		t.Fatalf("expected response.output_text.done, got %s", body)
	}
	if !strings.Contains(body, `"type":"response.output_item.done"`) {
		t.Fatalf("expected response.output_item.done, got %s", body)
	}
}

func TestResponsesCompatStreamHandlerEmitsFunctionCallEvents(t *testing.T) {
	gin.SetMode(gin.TestMode)
	constant.StreamingTimeout = 1

	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	ctx.Request = httptest.NewRequest(http.MethodGet, "/", nil)
	ctx.Set(common.RequestIdKey, "chatcore_resp_tool_stream")

	streamBody := strings.Join([]string{
		`data: {"id":"chatcmpl_234","object":"chat.completion.chunk","created":123,"model":"gpt-5.4-fast","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"mcp__CherryHub__list","arguments":"{\"query\":\"today\"}"}}]},"finish_reason":null}]}`,
		``,
		`data: {"id":"chatcmpl_234","object":"chat.completion.chunk","created":123,"model":"gpt-5.4-fast","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}`,
		``,
	}, "\n")

	resp := &http.Response{
		StatusCode: http.StatusOK,
		Body:       io.NopCloser(strings.NewReader(streamBody)),
		Header:     make(http.Header),
	}
	info := &relaycommon.RelayInfo{}

	_, err := responsesCompatStreamHandler(ctx, resp, info)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	body := recorder.Body.String()
	if !strings.Contains(body, `"type":"response.output_item.added"`) {
		t.Fatalf("expected response.output_item.added, got %s", body)
	}
	if !strings.Contains(body, `"type":"response.function_call_arguments.delta"`) {
		t.Fatalf("expected response.function_call_arguments.delta, got %s", body)
	}
	if !strings.Contains(body, `"type":"response.function_call_arguments.done"`) {
		t.Fatalf("expected response.function_call_arguments.done, got %s", body)
	}
}
