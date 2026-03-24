package model

import (
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

func TestPrepareUserResetFields(t *testing.T) {
	now := time.Date(2026, 3, 24, 10, 0, 0, 0, time.UTC).Unix()
	user := &User{
		Quota:            12345,
		QuotaResetPeriod: SubscriptionResetDaily,
	}

	PrepareUserResetFields(user, now)

	if user.QuotaResetAmount != 12345 {
		t.Fatalf("expected reset amount 12345, got %d", user.QuotaResetAmount)
	}
	if user.LastResetTime != now {
		t.Fatalf("expected last reset %d, got %d", now, user.LastResetTime)
	}
	if user.NextResetTime <= now {
		t.Fatalf("expected next reset after now, got %d", user.NextResetTime)
	}
}

func TestMaybeResetUserQuota(t *testing.T) {
	origDB := DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatalf("failed to init test db: %v", err)
	}
	DB = db
	t.Cleanup(func() { DB = origDB })
	if err := DB.AutoMigrate(&User{}); err != nil {
		t.Fatalf("failed to migrate user: %v", err)
	}

	now := common.GetTimestamp()
	user := &User{
		Username:         "quota-reset-user",
		Password:         "password123",
		DisplayName:      "quota-reset-user",
		Role:             common.RoleCommonUser,
		Status:           common.UserStatusEnabled,
		Group:            "default",
		Quota:            10,
		UsedQuota:        99,
		MaxConcurrency:   5,
		QuotaResetPeriod: SubscriptionResetDaily,
		QuotaResetAmount: 777,
		LastResetTime:    now - 86400,
		NextResetTime:    now - 10,
	}
	if err := user.Insert(0); err != nil {
		t.Fatalf("failed to insert user: %v", err)
	}
	stored, err := GetUserById(user.Id, true)
	if err != nil {
		t.Fatalf("failed to load user: %v", err)
	}
	stored.Quota = 10
	stored.UsedQuota = 99
	stored.QuotaResetPeriod = SubscriptionResetDaily
	stored.QuotaResetAmount = 777
	stored.LastResetTime = now - 86400
	stored.NextResetTime = now - 10
	if err := DB.Model(&User{}).Where("id = ?", stored.Id).Updates(map[string]any{
		"quota":              stored.Quota,
		"used_quota":         stored.UsedQuota,
		"quota_reset_period": stored.QuotaResetPeriod,
		"quota_reset_amount": stored.QuotaResetAmount,
		"last_reset_time":    stored.LastResetTime,
		"next_reset_time":    stored.NextResetTime,
	}).Error; err != nil {
		t.Fatalf("failed to seed reset fields: %v", err)
	}
	if err := maybeResetUserQuota(stored); err != nil {
		t.Fatalf("maybeResetUserQuota failed: %v", err)
	}
	if stored.Quota != 777 {
		t.Fatalf("expected quota reset to 777, got %d", stored.Quota)
	}
	if stored.UsedQuota != 0 {
		t.Fatalf("expected used quota reset to 0, got %d", stored.UsedQuota)
	}
	if stored.NextResetTime <= now {
		t.Fatalf("expected next reset moved forward, got %d", stored.NextResetTime)
	}
}
