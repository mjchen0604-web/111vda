package openai

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
)

func TestApplyPresentedServiceTierToChatResponseFastModel(t *testing.T) {
	info := &relaycommon.RelayInfo{
		OriginModelName: "gpt-5.4-fast",
	}
	resp := &dto.OpenAITextResponse{ServiceTier: "default"}
	applyPresentedServiceTierToChatResponse(info, resp)
	if resp.ServiceTier != "priority" {
		t.Fatalf("expected priority, got %q", resp.ServiceTier)
	}
}

func TestApplyPresentedServiceTierToChatResponsePriorityRequest(t *testing.T) {
	raw, _ := common.Marshal("priority")
	info := &relaycommon.RelayInfo{
		OriginModelName: "gpt-5.4",
		Request: &dto.GeneralOpenAIRequest{
			ServiceTier: raw,
		},
	}
	resp := &dto.OpenAITextResponse{ServiceTier: "auto"}
	applyPresentedServiceTierToChatResponse(info, resp)
	if resp.ServiceTier != "priority" {
		t.Fatalf("expected priority, got %q", resp.ServiceTier)
	}
}

func TestPatchServiceTierInOpenAIResponseBody(t *testing.T) {
	raw, _ := common.Marshal("priority")
	info := &relaycommon.RelayInfo{
		OriginModelName: "gpt-5.4",
		Request: &dto.GeneralOpenAIRequest{
			ServiceTier: raw,
		},
	}
	body := []byte(`{"model":"gpt-5.4-fast","service_tier":"default"}`)
	patched := patchServiceTierInOpenAIResponseBody(info, body)
	expected := `{"model":"gpt-5.4-fast","service_tier":"priority"}`
	if string(patched) != expected {
		t.Fatalf("unexpected patched body: %s", string(patched))
	}
}
