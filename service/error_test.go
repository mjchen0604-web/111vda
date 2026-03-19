package service

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/types"
	"github.com/stretchr/testify/require"
)

func TestResetStatusCode(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name             string
		statusCode       int
		statusCodeConfig string
		expectedCode     int
	}{
		{
			name:             "map string value",
			statusCode:       429,
			statusCodeConfig: `{"429":"503"}`,
			expectedCode:     503,
		},
		{
			name:             "map int value",
			statusCode:       429,
			statusCodeConfig: `{"429":503}`,
			expectedCode:     503,
		},
		{
			name:             "skip invalid string value",
			statusCode:       429,
			statusCodeConfig: `{"429":"bad-code"}`,
			expectedCode:     429,
		},
		{
			name:             "skip status code 200",
			statusCode:       200,
			statusCodeConfig: `{"200":503}`,
			expectedCode:     200,
		},
	}

	for _, tc := range testCases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			newAPIError := &types.NewAPIError{
				StatusCode: tc.statusCode,
			}
			ResetStatusCode(newAPIError, tc.statusCodeConfig)
			require.Equal(t, tc.expectedCode, newAPIError.StatusCode)
		})
	}
}

func TestRelayErrorHandlerConvertsBillingLike429To402AndSkipRetry(t *testing.T) {
	t.Parallel()

	resp := &http.Response{
		StatusCode: http.StatusTooManyRequests,
		Body: io.NopCloser(strings.NewReader(
			`{"error":{"type":"rate_limit_exceeded","message":"Payment Required (request id: abc123)"}}`,
		)),
	}

	newAPIError := RelayErrorHandler(context.Background(), resp, false)
	require.NotNil(t, newAPIError)
	require.Equal(t, http.StatusPaymentRequired, newAPIError.StatusCode)
	require.False(t, types.IsSkipRetryError(newAPIError))

	openAIError := newAPIError.ToOpenAIError()
	require.Equal(t, "insufficient_quota", openAIError.Type)
	require.Equal(t, "insufficient_quota", openAIError.Code)
	require.Contains(t, openAIError.Message, "Payment Required")
}

func TestRelayErrorHandlerKeepsTrueRateLimit429(t *testing.T) {
	t.Parallel()

	resp := &http.Response{
		StatusCode: http.StatusTooManyRequests,
		Body: io.NopCloser(strings.NewReader(
			`{"error":{"type":"rate_limit_exceeded","message":"Rate limit exceeded, retry later"}}`,
		)),
	}

	newAPIError := RelayErrorHandler(context.Background(), resp, false)
	require.NotNil(t, newAPIError)
	require.Equal(t, http.StatusTooManyRequests, newAPIError.StatusCode)
	require.False(t, types.IsSkipRetryError(newAPIError))
}

func TestRelayErrorHandlerKeepsCodexUsageLimit429(t *testing.T) {
	t.Parallel()

	resp := &http.Response{
		StatusCode: http.StatusTooManyRequests,
		Body: io.NopCloser(strings.NewReader(
			`{"error":{"type":"rate_limit_exceeded","message":"You have reached your included credits usage limit. Try again at Mar 21, 2026 9:00 AM or upgrade to Plus to continue using Codex."}}`,
		)),
	}

	newAPIError := RelayErrorHandler(context.Background(), resp, false)
	require.NotNil(t, newAPIError)
	require.Equal(t, http.StatusTooManyRequests, newAPIError.StatusCode)
	require.False(t, types.IsSkipRetryError(newAPIError))

	openAIError := newAPIError.ToOpenAIError()
	require.NotEqual(t, "insufficient_quota", openAIError.Type)
	require.NotEqual(t, "insufficient_quota", openAIError.Code)
}
