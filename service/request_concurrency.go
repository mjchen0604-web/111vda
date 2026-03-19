package service

import (
	"fmt"
	"net/http"
	"sync"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
)

var (
	requestConcurrencyMu     sync.Mutex
	requestConcurrencyCounts = map[string]int{}
)

func acquireRequestConcurrencySlot(key string, limit int) bool {
	if key == "" || limit <= 0 {
		return true
	}
	requestConcurrencyMu.Lock()
	defer requestConcurrencyMu.Unlock()
	current := requestConcurrencyCounts[key]
	if current >= limit {
		return false
	}
	requestConcurrencyCounts[key] = current + 1
	return true
}

func releaseRequestConcurrencySlot(key string) {
	if key == "" {
		return
	}
	requestConcurrencyMu.Lock()
	defer requestConcurrencyMu.Unlock()
	current := requestConcurrencyCounts[key]
	if current <= 1 {
		delete(requestConcurrencyCounts, key)
		return
	}
	requestConcurrencyCounts[key] = current - 1
}

func buildConcurrencyLimitError(scope string, limit int) *types.NewAPIError {
	return types.WithOpenAIError(
		types.OpenAIError{
			Message: fmt.Sprintf("%s concurrency limit exceeded (%d)", scope, limit),
			Type:    "rate_limit_error",
			Code:    "concurrency_limit_exceeded",
		},
		http.StatusTooManyRequests,
		types.ErrOptionWithSkipRetry(),
	)
}

func AcquireRequestConcurrency(c *gin.Context) (func(), *types.NewAPIError) {
	type slot struct {
		key string
	}
	acquired := make([]slot, 0, 2)

	userID := c.GetInt("id")
	userLimit := common.GetContextKeyInt(c, constant.ContextKeyUserMaxConcurrency)
	if userID > 0 && userLimit > 0 {
		key := fmt.Sprintf("user:%d", userID)
		if !acquireRequestConcurrencySlot(key, userLimit) {
			return nil, buildConcurrencyLimitError("user", userLimit)
		}
		acquired = append(acquired, slot{key: key})
	}

	tokenID := c.GetInt("token_id")
	tokenLimit := common.GetContextKeyInt(c, constant.ContextKeyTokenMaxConcurrency)
	if tokenID > 0 && tokenLimit > 0 {
		key := fmt.Sprintf("token:%d", tokenID)
		if !acquireRequestConcurrencySlot(key, tokenLimit) {
			for _, item := range acquired {
				releaseRequestConcurrencySlot(item.key)
			}
			return nil, buildConcurrencyLimitError("token", tokenLimit)
		}
		acquired = append(acquired, slot{key: key})
	}

	return func() {
		for i := len(acquired) - 1; i >= 0; i-- {
			releaseRequestConcurrencySlot(acquired[i].key)
		}
	}, nil
}
