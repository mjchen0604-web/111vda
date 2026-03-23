package controller

import (
	"testing"

	"github.com/QuantumNous/new-api/model"
)

func TestExposePricingModelsPrefersInternalClaudeSkinPricing(t *testing.T) {
	input := []model.Pricing{
		{
			ModelName:        "gpt-5.4-fast-xhigh",
			ModelRatio:       1.25,
			CompletionRatio:  6.0,
			CacheRatio:       ptrFloat64(0.1),
			CreateCacheRatio: ptrFloat64(1.25),
			VendorID:         9,
		},
		{
			ModelName:        "claude-opus-4-6",
			ModelRatio:       2.5,
			CompletionRatio:  10.0,
			CacheRatio:       ptrFloat64(0.2),
			CreateCacheRatio: ptrFloat64(2.5),
			VendorID:         99,
		},
	}

	got := exposePricingModels(input)
	var found *model.Pricing
	for i := range got {
		if got[i].ModelName == "claude-opus-4-6" {
			found = &got[i]
			break
		}
	}
	if found == nil {
		t.Fatal("expected claude-opus-4-6 pricing row")
	}
	if found.VendorID != 9 {
		t.Fatalf("expected internal vendor id 9, got %d", found.VendorID)
	}
	if found.ModelRatio != 1.25 {
		t.Fatalf("expected internal model ratio 1.25, got %v", found.ModelRatio)
	}
	if found.CompletionRatio != 6.0 {
		t.Fatalf("expected internal completion ratio 6.0, got %v", found.CompletionRatio)
	}
}

func ptrFloat64(v float64) *float64 { return &v }
