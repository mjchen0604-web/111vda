package openai

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
)

func newResponsesStreamHTTPResponse(body string) *http.Response {
	return &http.Response{
		StatusCode: http.StatusOK,
		Body:       io.NopCloser(strings.NewReader(body)),
		Header:     make(http.Header),
	}
}

func newResponsesStreamTestContext() (*gin.Context, *httptest.ResponseRecorder) {
	gin.SetMode(gin.TestMode)
	constant.StreamingTimeout = 1
	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	ctx.Request = httptest.NewRequest(http.MethodGet, "/", nil)
	ctx.Set(common.RequestIdKey, "testreq")
	return ctx, recorder
}

func TestOaiResponsesToChatStreamHandler_OutputTextDoneOnly(t *testing.T) {
	ctx, recorder := newResponsesStreamTestContext()
	info := &relaycommon.RelayInfo{
		RelayFormat: types.RelayFormatOpenAI,
		DisablePing: true,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "gpt-5.4",
		},
	}
	resp := newResponsesStreamHTTPResponse(strings.Join([]string{
		`data: {"type":"response.created","response":{"model":"gpt-5.4","created_at":123}}`,
		``,
		`data: {"type":"response.output_text.done","item_id":"msg_1","content_index":0,"text":"图片里是绿色光带。"}`,
		``,
		`data: {"type":"response.completed","response":{"model":"gpt-5.4","created_at":123,"usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}}`,
		``,
	}, "\n"))

	usage, err := OaiResponsesToChatStreamHandler(ctx, info, resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if usage == nil || usage.TotalTokens != 15 {
		t.Fatalf("expected usage total tokens 15, got %#v", usage)
	}
	if body := recorder.Body.String(); !strings.Contains(body, "图片里是绿色光带。") {
		t.Fatalf("expected streamed body to contain output_text.done text, got %s", body)
	}
}

func TestOaiResponsesToChatStreamHandler_OutputItemDoneMessageOnly(t *testing.T) {
	ctx, recorder := newResponsesStreamTestContext()
	info := &relaycommon.RelayInfo{
		RelayFormat: types.RelayFormatOpenAI,
		DisablePing: true,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "gpt-5.4",
		},
	}
	resp := newResponsesStreamHTTPResponse(strings.Join([]string{
		`data: {"type":"response.created","response":{"model":"gpt-5.4","created_at":123}}`,
		``,
		`data: {"type":"response.output_item.done","item":{"type":"message","id":"msg_1","role":"assistant","content":[{"type":"output_text","text":"图片主体是黑底上的绿色与金色流线。"}]}}`,
		``,
		`data: {"type":"response.completed","response":{"model":"gpt-5.4","created_at":123,"usage":{"input_tokens":10,"output_tokens":6,"total_tokens":16}}}`,
		``,
	}, "\n"))

	usage, err := OaiResponsesToChatStreamHandler(ctx, info, resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if usage == nil || usage.TotalTokens != 16 {
		t.Fatalf("expected usage total tokens 16, got %#v", usage)
	}
	if body := recorder.Body.String(); !strings.Contains(body, "图片主体是黑底上的绿色与金色流线。") {
		t.Fatalf("expected streamed body to contain output_item.done message text, got %s", body)
	}
}
