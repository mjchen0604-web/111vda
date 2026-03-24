package service

import (
	"context"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/logger"
	"github.com/QuantumNous/new-api/model"

	"github.com/bytedance/gopkg/util/gopool"
)

const (
	userResetTickInterval = 1 * time.Minute
	userResetBatchSize    = 300
)

var (
	userResetOnce    sync.Once
	userResetRunning atomic.Bool
)

func StartUserQuotaResetTask() {
	userResetOnce.Do(func() {
		if !common.IsMasterNode {
			return
		}
		gopool.Go(func() {
			logger.LogInfo(context.Background(), fmt.Sprintf("user quota reset task started: tick=%s", userResetTickInterval))
			ticker := time.NewTicker(userResetTickInterval)
			defer ticker.Stop()

			runUserQuotaResetOnce()
			for range ticker.C {
				runUserQuotaResetOnce()
			}
		})
	})
}

func runUserQuotaResetOnce() {
	if !userResetRunning.CompareAndSwap(false, true) {
		return
	}
	defer userResetRunning.Store(false)

	ctx := context.Background()
	totalReset := 0
	for {
		n, err := model.ResetDueUsers(userResetBatchSize)
		if err != nil {
			logger.LogWarn(ctx, fmt.Sprintf("user quota reset task failed: %v", err))
			return
		}
		if n == 0 {
			break
		}
		totalReset += n
		if n < userResetBatchSize {
			break
		}
	}
	if common.DebugEnabled && totalReset > 0 {
		logger.LogDebug(ctx, "user quota reset: reset_count=%d", totalReset)
	}
}
