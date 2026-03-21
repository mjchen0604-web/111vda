package ratio_setting

import "testing"

func TestSeedDefaultGPTPricingUsesMediumForBaseModels(t *testing.T) {
	InitRatioSettings()

	cases := []struct {
		model           string
		wantModelRatio  float64
		wantCacheRatio  float64
		wantOutputRatio float64
	}{
		{model: "gpt-5.4", wantModelRatio: 1.25, wantCacheRatio: 0.1, wantOutputRatio: 6.0},
		{model: "gpt-5.4-fast", wantModelRatio: 2.5, wantCacheRatio: 0.1, wantOutputRatio: 6.0},
		{model: "gpt-5.4-mini", wantModelRatio: 0.1, wantCacheRatio: 0.125, wantOutputRatio: 6.25},
		{model: "gpt-5.2", wantModelRatio: 0.875, wantCacheRatio: 0.1, wantOutputRatio: 8.0},
		{model: "gpt-5.2-codex", wantModelRatio: 0.875, wantCacheRatio: 0.1, wantOutputRatio: 8.0},
		{model: "gpt-5", wantModelRatio: 0.625, wantCacheRatio: 0.1, wantOutputRatio: 8.0},
		{model: "gpt-5-minimal", wantModelRatio: 0.625, wantCacheRatio: 0.1, wantOutputRatio: 8.0},
		{model: "gpt-5-mini", wantModelRatio: 0.125, wantCacheRatio: 0.1, wantOutputRatio: 8.0},
	}

	for _, tt := range cases {
		gotRatio, ok, _ := GetModelRatio(tt.model)
		if !ok {
			t.Fatalf("GetModelRatio(%q) not found", tt.model)
		}
		if gotRatio != tt.wantModelRatio {
			t.Fatalf("GetModelRatio(%q) = %v, want %v", tt.model, gotRatio, tt.wantModelRatio)
		}

		gotCacheRatio, ok := GetCacheRatio(tt.model)
		if !ok {
			t.Fatalf("GetCacheRatio(%q) not found", tt.model)
		}
		if gotCacheRatio != tt.wantCacheRatio {
			t.Fatalf("GetCacheRatio(%q) = %v, want %v", tt.model, gotCacheRatio, tt.wantCacheRatio)
		}

		gotOutputRatio := GetCompletionRatio(tt.model)
		if gotOutputRatio != tt.wantOutputRatio {
			t.Fatalf("GetCompletionRatio(%q) = %v, want %v", tt.model, gotOutputRatio, tt.wantOutputRatio)
		}
	}
}

func TestSeedDefaultGPTPricingUsesEffortSpecificOutputRatios(t *testing.T) {
	InitRatioSettings()

	cases := []struct {
		model string
		want  float64
	}{
		{model: "gpt-5.4-low", want: 6.0},
		{model: "gpt-5.4-high", want: 6.0},
		{model: "gpt-5.4-fast-low", want: 6.0},
		{model: "gpt-5.4-fast-xhigh", want: 6.0},
		{model: "gpt-5.4-mini-high", want: 6.25},
		{model: "gpt-5.2-low", want: 6.0},
		{model: "gpt-5.2-xhigh", want: 12.0},
	}

	for _, tt := range cases {
		if got := GetCompletionRatio(tt.model); got != tt.want {
			t.Fatalf("GetCompletionRatio(%q) = %v, want %v", tt.model, got, tt.want)
		}
	}
}
