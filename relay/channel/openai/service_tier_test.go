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
	if resp.ServiceTier != "" {
		t.Fatalf("expected hidden service tier, got %q", resp.ServiceTier)
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
	if resp.ServiceTier != "" {
		t.Fatalf("expected hidden service tier, got %q", resp.ServiceTier)
	}
}

func TestApplyPresentedServiceTierToStreamRemovesSystemFingerprint(t *testing.T) {
	info := &relaycommon.RelayInfo{
		OriginModelName: "gpt-5.4-fast",
	}
	fingerprint := "fp_test"
	resp := &dto.ChatCompletionsStreamResponse{
		ServiceTier:       "default",
		SystemFingerprint: &fingerprint,
	}
	applyPresentedServiceTierToStream(info, resp)
	if resp.ServiceTier != "" {
		t.Fatalf("expected hidden service tier, got %q", resp.ServiceTier)
	}
	if resp.SystemFingerprint != nil {
		t.Fatalf("expected system fingerprint to be removed, got %q", resp.GetSystemFingerprint())
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
	expected := `{"model":"gpt-5.4-fast"}`
	if string(patched) != expected {
		t.Fatalf("unexpected patched body: %s", string(patched))
	}
}

func TestPatchServiceTierInOpenAIResponseBodyRemovesSystemFingerprint(t *testing.T) {
	info := &relaycommon.RelayInfo{
		OriginModelName: "gpt-5.4",
	}
	body := []byte(`{"model":"gpt-5.4","service_tier":"default","system_fingerprint":"fp_123"}`)
	patched := patchServiceTierInOpenAIResponseBody(info, body)
	expected := `{"model":"gpt-5.4"}`
	if string(patched) != expected {
		t.Fatalf("unexpected patched body: %s", string(patched))
	}
}

func TestNormalizePresentedServiceTierMalformedDefault(t *testing.T) {
	if got := normalizePresentedServiceTier("de fault"); got != "default" {
		t.Fatalf("expected default, got %q", got)
	}
}

func TestApplyPresentedServiceTierToChatResponsePreservesDefaultForNonFast(t *testing.T) {
	info := &relaycommon.RelayInfo{
		OriginModelName: "gpt-5.4",
	}
	resp := &dto.OpenAITextResponse{ServiceTier: "default"}
	applyPresentedServiceTierToChatResponse(info, resp)
	if resp.ServiceTier != "" {
		t.Fatalf("expected hidden service tier, got %q", resp.ServiceTier)
	}
}

func TestPatchServiceTierInOpenAIResponseBodyPreservesDefaultForNonFast(t *testing.T) {
	info := &relaycommon.RelayInfo{
		OriginModelName: "gpt-5.4",
	}
	body := []byte(`{"model":"gpt-5.4","service_tier":"de fault"}`)
	patched := patchServiceTierInOpenAIResponseBody(info, body)
	expected := `{"model":"gpt-5.4"}`
	if string(patched) != expected {
		t.Fatalf("unexpected patched body: %s", string(patched))
	}
}
