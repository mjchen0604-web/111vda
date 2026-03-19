package service

import (
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/gin-gonic/gin"
)

func TestAcquireRequestConcurrencyHonorsUserLimit(t *testing.T) {
	gin.SetMode(gin.TestMode)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Set("id", 123)
	common.SetContextKey(c, constant.ContextKeyUserMaxConcurrency, 1)

	release, err := AcquireRequestConcurrency(c)
	if err != nil {
		t.Fatalf("first acquire should succeed: %v", err)
	}
	defer release()

	secondRelease, secondErr := AcquireRequestConcurrency(c)
	if secondErr == nil {
		if secondRelease != nil {
			secondRelease()
		}
		t.Fatalf("second acquire should fail when user limit reached")
	}
}

func TestAcquireRequestConcurrencyHonorsTokenLimit(t *testing.T) {
	gin.SetMode(gin.TestMode)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Set("id", 123)
	c.Set("token_id", 456)
	common.SetContextKey(c, constant.ContextKeyUserMaxConcurrency, 0)
	common.SetContextKey(c, constant.ContextKeyTokenMaxConcurrency, 1)

	release, err := AcquireRequestConcurrency(c)
	if err != nil {
		t.Fatalf("first acquire should succeed: %v", err)
	}
	defer release()

	secondRelease, secondErr := AcquireRequestConcurrency(c)
	if secondErr == nil {
		if secondRelease != nil {
			secondRelease()
		}
		t.Fatalf("second acquire should fail when token limit reached")
	}
}
