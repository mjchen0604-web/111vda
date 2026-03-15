package common

import (
	"testing"

	"github.com/QuantumNous/new-api/constant"
)

func TestGetEndpointTypesByChannelTypeOpenAIIncludesResponses(t *testing.T) {
	got := GetEndpointTypesByChannelType(constant.ChannelTypeOpenAI, "gpt-5.4")
	want := []constant.EndpointType{
		constant.EndpointTypeOpenAI,
		constant.EndpointTypeOpenAIResponse,
	}
	if len(got) != len(want) {
		t.Fatalf("unexpected endpoint count: got %v want %v", got, want)
	}
	for index := range want {
		if got[index] != want[index] {
			t.Fatalf("unexpected endpoint at %d: got %q want %q", index, got[index], want[index])
		}
	}
}

func TestGetEndpointTypesByChannelTypeResponseOnlyModelUsesResponses(t *testing.T) {
	got := GetEndpointTypesByChannelType(constant.ChannelTypeOpenAI, "o3-pro")
	want := []constant.EndpointType{constant.EndpointTypeOpenAIResponse}
	if len(got) != len(want) || got[0] != want[0] {
		t.Fatalf("unexpected endpoints for response-only model: got %v want %v", got, want)
	}
}

func TestGetEndpointTypesByChannelTypeCodexUsesResponses(t *testing.T) {
	got := GetEndpointTypesByChannelType(constant.ChannelTypeCodex, "gpt-5-codex")
	want := []constant.EndpointType{constant.EndpointTypeOpenAIResponse}
	if len(got) != len(want) || got[0] != want[0] {
		t.Fatalf("unexpected codex endpoints: got %v want %v", got, want)
	}
}
