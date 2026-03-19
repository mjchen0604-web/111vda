package helper

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/setting/ratio_setting"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
)

func newPriceTestContext() *gin.Context {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	ctx.Request = httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	common.SetContextKey(ctx, constant.ContextKeyUsingGroup, "default")
	common.SetContextKey(ctx, constant.ContextKeyUserGroup, "default")
	common.SetContextKey(ctx, constant.ContextKeyOriginalModel, "gpt-4o")
	return ctx
}

func TestModelPriceHelperAppliesConsumeRatioToPreconsume(t *testing.T) {
	ratio_setting.InitRatioSettings()
	if err := ratio_setting.UpdateModelConsumeRatioByJSONString(`{"gpt-4o":2}`); err != nil {
		t.Fatalf("failed to seed consume ratio: %v", err)
	}
	defer ratio_setting.UpdateModelConsumeRatioByJSONString(`{}`)

	ctx := newPriceTestContext()
	info := &relaycommon.RelayInfo{
		OriginModelName: "gpt-4o",
		UsingGroup:      "default",
		UserGroup:       "default",
	}

	priceData, err := ModelPriceHelper(ctx, info, 100, &types.TokenCountMeta{})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if priceData.ConsumeRatio != 2 {
		t.Fatalf("expected consume ratio 2, got %v", priceData.ConsumeRatio)
	}
	if priceData.QuotaToPreConsume != 1250 {
		t.Fatalf("expected pre-consume quota 1250, got %d", priceData.QuotaToPreConsume)
	}
}

func TestModelPriceHelperPerCallAppliesConsumeRatio(t *testing.T) {
	ratio_setting.InitRatioSettings()
	if err := ratio_setting.UpdateModelPriceByJSONString(`{"test-per-call":0.04}`); err != nil {
		t.Fatalf("failed to seed model price: %v", err)
	}
	if err := ratio_setting.UpdateModelConsumeRatioByJSONString(`{"test-per-call":1.5}`); err != nil {
		t.Fatalf("failed to seed consume ratio: %v", err)
	}
	defer ratio_setting.UpdateModelPriceByJSONString(`{}`)
	defer ratio_setting.UpdateModelConsumeRatioByJSONString(`{}`)

	ctx := newPriceTestContext()
	info := &relaycommon.RelayInfo{
		OriginModelName: "test-per-call",
		UsingGroup:      "default",
		UserGroup:       "default",
	}

	priceData, err := ModelPriceHelperPerCall(ctx, info)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if priceData.ConsumeRatio != 1.5 {
		t.Fatalf("expected consume ratio 1.5, got %v", priceData.ConsumeRatio)
	}
	if priceData.Quota != 30000 {
		t.Fatalf("expected per-call quota 30000, got %d", priceData.Quota)
	}
}
