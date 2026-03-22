package dto

import (
	"testing"

	"github.com/samber/lo"
	"github.com/stretchr/testify/require"
)

func TestGeneralOpenAIRequestDefaultsToStream(t *testing.T) {
	req := &GeneralOpenAIRequest{}
	require.True(t, req.IsStream(nil))

	req.Stream = lo.ToPtr(false)
	require.False(t, req.IsStream(nil))

	req.Stream = lo.ToPtr(true)
	require.True(t, req.IsStream(nil))
}

func TestOpenAIResponsesRequestDefaultsToStream(t *testing.T) {
	req := &OpenAIResponsesRequest{}
	require.True(t, req.IsStream(nil))

	req.Stream = lo.ToPtr(false)
	require.False(t, req.IsStream(nil))

	req.Stream = lo.ToPtr(true)
	require.True(t, req.IsStream(nil))
}

func TestClaudeRequestDefaultsToStream(t *testing.T) {
	req := &ClaudeRequest{}
	require.True(t, req.IsStream(nil))

	req.Stream = lo.ToPtr(false)
	require.False(t, req.IsStream(nil))

	req.Stream = lo.ToPtr(true)
	require.True(t, req.IsStream(nil))
}
