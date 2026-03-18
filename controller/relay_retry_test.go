package controller

import (
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
)

func TestShouldRetryAllowsInternalRetryForInsufficientQuota402(t *testing.T) {
	gin.SetMode(gin.TestMode)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)

	err := types.WithOpenAIError(
		types.OpenAIError{
			Message: "Payment Required",
			Type:    "insufficient_quota",
			Code:    "insufficient_quota",
		},
		402,
	)

	if !shouldRetry(c, err, 1) {
		t.Fatalf("expected insufficient_quota 402 to remain retryable inside gateway")
	}
}
