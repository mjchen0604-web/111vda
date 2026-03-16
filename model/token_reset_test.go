package model

import (
	"testing"
	"time"
)

func TestPrepareTokenResetFields(t *testing.T) {
	now := time.Date(2026, 3, 17, 10, 0, 0, 0, time.UTC).Unix()
	token := &Token{
		RemainQuota:      12345,
		ExpiredTime:      -1,
		QuotaResetPeriod: SubscriptionResetDaily,
	}

	PrepareTokenResetFields(token, now)

	if token.QuotaResetAmount != 12345 {
		t.Fatalf("expected reset amount 12345, got %d", token.QuotaResetAmount)
	}
	if token.LastResetTime != now {
		t.Fatalf("expected last reset %d, got %d", now, token.LastResetTime)
	}
	if token.NextResetTime <= now {
		t.Fatalf("expected next reset after now, got %d", token.NextResetTime)
	}
}

func TestCalcTokenNextResetTimeRespectsExpiry(t *testing.T) {
	base := time.Date(2026, 3, 17, 10, 0, 0, 0, time.UTC)
	token := &Token{
		QuotaResetPeriod: SubscriptionResetMonthly,
	}

	next := calcTokenNextResetTime(base, token, base.Add(12*time.Hour).Unix())
	if next != 0 {
		t.Fatalf("expected reset to be suppressed by expiry, got %d", next)
	}
}
