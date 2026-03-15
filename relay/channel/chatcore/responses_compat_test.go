package chatcore

import (
	"encoding/json"
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
			"content": "你是一个助手",
		},
		{
			"role": "user",
			"content": []map[string]any{
				{"type": "input_text", "text": "看这张图"},
				{"type": "input_image", "image_url": "https://example.com/image.png"},
			},
		},
		{
			"role": "assistant",
			"content": []map[string]any{
				{"type": "output_text", "text": "这是一张图"},
			},
		},
	})

	req := dto.OpenAIResponsesRequest{
		Model:       "gpt-5.4-lightning",
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
	if chatReq.Model != "gpt-5.4-lightning" {
		t.Fatalf("unexpected model: %s", chatReq.Model)
	}
	if chatReq.Stream == nil || !*chatReq.Stream {
		t.Fatalf("expected stream=true, got %#v", chatReq.Stream)
	}
	if len(chatReq.Messages) != 3 {
		t.Fatalf("expected 3 messages, got %d", len(chatReq.Messages))
	}
	if chatReq.Messages[0].Role != "developer" || chatReq.Messages[0].StringContent() != "你是一个助手" {
		t.Fatalf("unexpected developer message: %#v", chatReq.Messages[0])
	}
	content := chatReq.Messages[1].ParseContent()
	if len(content) != 2 {
		t.Fatalf("expected user mixed content, got %#v", content)
	}
	if chatReq.ToolChoice != "auto" {
		t.Fatalf("expected tool_choice=auto, got %#v", chatReq.ToolChoice)
	}
	if len(chatReq.Tools) != 1 || chatReq.Tools[0].Function.Name != "test_tool" {
		t.Fatalf("unexpected tools: %#v", chatReq.Tools)
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
			"content": "你好",
		},
	})
	converted, err := adaptor.ConvertOpenAIResponsesRequest(nil, info, dto.OpenAIResponsesRequest{
		Model: "gpt-5.4-lightning",
		Input: input,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if info.RequestURLPath != "/v1/chat/completions" {
		t.Fatalf("expected rewritten path, got %s", info.RequestURLPath)
	}
	if _, ok := converted.(*dto.GeneralOpenAIRequest); !ok {
		t.Fatalf("expected *dto.GeneralOpenAIRequest, got %T", converted)
	}
}

func TestChatCompletionsToResponsesResponse(t *testing.T) {
	chatResp := &dto.OpenAITextResponse{
		Id:      "chatcmpl_123",
		Object:  "chat.completion",
		Created: 123,
		Model:   "gpt-5.4-lightning",
		Choices: []dto.OpenAITextResponseChoice{
			{
				Index: 0,
				Message: dto.Message{
					Role:    "assistant",
					Content: "这是图片说明",
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
	if resp.Output[0].Type != "message" || len(resp.Output[0].Content) != 1 || resp.Output[0].Content[0].Text != "这是图片说明" {
		raw, _ := json.Marshal(resp)
		t.Fatalf("unexpected output content: %s", string(raw))
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
		`data: {"id":"chatcmpl_123","object":"chat.completion.chunk","created":123,"model":"gpt-5.4-fast","choices":[{"index":0,"delta":{"content":"图片描述"},"finish_reason":null}]}`,
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
