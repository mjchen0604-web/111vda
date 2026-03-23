package common

import (
	"slices"
	"testing"

	"github.com/QuantumNous/new-api/constant"
)

func TestResolveSkinnedModelAlias(t *testing.T) {
	if got := ResolveSkinnedModelAlias("claude-opus-4-6"); got != "gpt-5.4-fast-xhigh" {
		t.Fatalf("unexpected alias target: %s", got)
	}
	if got := ResolveSkinnedModelAlias("gpt-5.4-fast-xhigh"); got != "gpt-5.4-fast-xhigh" {
		t.Fatalf("unexpected passthrough target: %s", got)
	}
}

func TestPublicSkinnedModelName(t *testing.T) {
	if got := PublicSkinnedModelName("gpt-5.4-fast-xhigh"); got != "claude-opus-4-6" {
		t.Fatalf("unexpected public name: %s", got)
	}
	if got := PublicSkinnedModelName("claude-opus-4-6"); got != "claude-opus-4-6" {
		t.Fatalf("unexpected passthrough name: %s", got)
	}
}

func TestExposeSkinnedModelNames(t *testing.T) {
	got := ExposeSkinnedModelNames([]string{"gpt-5.4-fast-xhigh", "gpt-5.4-fast-low", "claude-opus-4-6"})
	if len(got) != 2 {
		t.Fatalf("unexpected exposed size: %v", got)
	}
	if !slices.Contains(got, "claude-sonnet-4-5") {
		t.Fatalf("expected sonnet alias in exposed models: %v", got)
	}
	if !slices.Contains(got, "claude-opus-4-6") {
		t.Fatalf("expected exact claude model to win collision: %v", got)
	}
}

func TestResolveBillingModelName(t *testing.T) {
	if got := ResolveBillingModelName("claude-opus-4-6", constant.ChannelTypeChatCore); got != "gpt-5.4-fast-xhigh" {
		t.Fatalf("unexpected billing model: %s", got)
	}
	if got := ResolveBillingModelName("claude-sonnet-4-6", 0); got != "gpt-5.4-fast-medium" {
		t.Fatalf("unexpected skinned billing model before channel selection: %s", got)
	}
	if got := ResolveBillingModelName("gpt-5.4-fast-xhigh", constant.ChannelTypeAnthropic); got != "gpt-5.4-fast-xhigh" {
		t.Fatalf("unexpected passthrough billing model: %s", got)
	}
}
