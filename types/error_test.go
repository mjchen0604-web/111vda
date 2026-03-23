package types

import "testing"

func TestToClaudeErrorPrefersOpenAITypeWhenCodeIsNil(t *testing.T) {
	err := WithOpenAIError(
		OpenAIError{
			Message: "Invalid request",
			Type:    "invalid_request_error",
			Code:    nil,
		},
		400,
	)

	claudeErr := err.ToClaudeError()
	if claudeErr.Type != "invalid_request_error" {
		t.Fatalf("unexpected claude error type: %q", claudeErr.Type)
	}
}

func TestToClaudeErrorFallsBackToNormalizedTypeWhenOpenAIFieldsEmpty(t *testing.T) {
	err := WithOpenAIError(
		OpenAIError{
			Message: "Rate limit exceeded",
			Type:    "",
			Code:    nil,
		},
		429,
	)

	claudeErr := err.ToClaudeError()
	if claudeErr.Type != "rate_limit_error" {
		t.Fatalf("unexpected claude error type: %q", claudeErr.Type)
	}
}
