package controller

import (
	"sort"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
)

func applyPlaygroundRuntimeDefaults(c *gin.Context, request dto.Request) {
	config, applyPrompt, applyModelConfig, ok := loadPlaygroundRuntimeConfig(c)
	if !ok {
		return
	}
	switch req := request.(type) {
	case *dto.GeneralOpenAIRequest:
		if applyModelConfig {
			applyPlaygroundModelConfigToOpenAIRequest(req, config)
		}
		if applyPrompt {
			applyPlaygroundPromptConfigToOpenAIRequest(req, config)
		}
	case *dto.OpenAIResponsesRequest:
		if applyModelConfig {
			applyPlaygroundModelConfigToResponsesRequest(req, config)
		}
		if applyPrompt {
			applyPlaygroundPromptConfigToResponsesRequest(req, config)
		}
	case *dto.ClaudeRequest:
		if applyModelConfig {
			applyPlaygroundModelConfigToClaudeRequest(req, config)
		}
		if applyPrompt {
			applyPlaygroundPromptConfigToClaudeRequest(req, config)
		}
	}
}

func buildPlaygroundRuntimePreview(c *gin.Context) map[string]any {
	config, applyPromptEnabled, applyModelConfigEnabled, ok := loadPlaygroundRuntimePreviewConfig(c)
	if !ok {
		return map[string]any{
			"applyToRealAPI":            false,
			"applyPromptToRealAPI":      false,
			"applyModelConfigToRealAPI": false,
			"mergedInputs":              map[string]any{},
			"injectWhenMissing":         map[string]any{},
			"enabledKeys":               []string{},
		}
	}
	injected := map[string]any{}
	enabledKeys := make([]string, 0, len(playgroundAllowedParameterKeys))
	for key := range playgroundAllowedParameterKeys {
		if !playgroundParamEnabled(config, key) {
			continue
		}
		if value, ok := playgroundInput(config, key); ok {
			injected[key] = value
			enabledKeys = append(enabledKeys, key)
		}
	}
	sort.Strings(enabledKeys)
	inputs, _ := config["inputs"].(map[string]any)
	parameterEnabled, _ := config["parameterEnabled"].(map[string]any)
	if inputs == nil {
		inputs = map[string]any{}
	}
	if parameterEnabled == nil {
		parameterEnabled = map[string]any{}
	}
	return map[string]any{
		"applyToRealAPI":            applyPromptEnabled,
		"applyPromptToRealAPI":      applyPromptEnabled,
		"applyModelConfigToRealAPI": applyModelConfigEnabled,
		"mergedInputs":              inputs,
		"parameterEnabled":          parameterEnabled,
		"injectWhenMissing":         injected,
		"enabledKeys":               enabledKeys,
		"mode":                      "fill_missing_only",
	}
}

func loadPlaygroundRuntimeConfig(c *gin.Context) (map[string]any, bool, bool, bool) {
	config, applyPromptEnabled, applyModelConfigEnabled, ok := loadPlaygroundRuntimePreviewConfig(c)
	if !ok || (!applyPromptEnabled && !applyModelConfigEnabled) {
		return nil, false, false, false
	}
	return config, applyPromptEnabled, applyModelConfigEnabled, true
}

func loadPlaygroundRuntimePreviewConfig(c *gin.Context) (map[string]any, bool, bool, bool) {
	userID := c.GetInt("id")
	if userID <= 0 {
		return nil, false, false, false
	}
	userSetting, err := model.GetUserSetting(userID, false)
	if err != nil {
		return nil, false, false, false
	}
	merged := map[string]any{
		"inputs":           map[string]any{},
		"parameterEnabled": map[string]any{},
	}
	mergePlaygroundConfigInto(merged, decodePlaygroundConfig("PlaygroundGlobalDefaults"))
	if c.GetInt("role") >= common.RoleAdminUser {
		mergePlaygroundConfigInto(merged, decodePlaygroundConfig("PlaygroundAdminDefaults"))
	}
	mergePlaygroundConfigInto(merged, sanitizePersonalPlaygroundConfig(userSetting.PlaygroundDefaults))
	applyPromptEnabled, applyModelConfigEnabled := resolvePlaygroundApplyFlags(userSetting)
	return merged, applyPromptEnabled, applyModelConfigEnabled, true
}

func mergePlaygroundConfigInto(target map[string]any, source map[string]any) {
	if target == nil || source == nil {
		return
	}
	targetInputs, _ := target["inputs"].(map[string]any)
	if targetInputs == nil {
		targetInputs = map[string]any{}
		target["inputs"] = targetInputs
	}
	if sourceInputs, ok := source["inputs"].(map[string]any); ok {
		for key, value := range sourceInputs {
			targetInputs[key] = value
		}
	}

	targetEnabled, _ := target["parameterEnabled"].(map[string]any)
	if targetEnabled == nil {
		targetEnabled = map[string]any{}
		target["parameterEnabled"] = targetEnabled
	}
	if sourceEnabled, ok := source["parameterEnabled"].(map[string]any); ok {
		for key, value := range sourceEnabled {
			targetEnabled[key] = value
		}
	}
}

func applyPlaygroundModelConfigToOpenAIRequest(req *dto.GeneralOpenAIRequest, config map[string]any) {
	if req == nil {
		return
	}
	if req.Temperature == nil && playgroundParamEnabled(config, "temperature") {
		if value, ok := playgroundFloat(config, "temperature"); ok {
			req.Temperature = &value
		}
	}
	if req.TopP == nil && playgroundParamEnabled(config, "top_p") {
		if value, ok := playgroundFloat(config, "top_p"); ok {
			req.TopP = &value
		}
	}
	if req.FrequencyPenalty == nil && playgroundParamEnabled(config, "frequency_penalty") {
		if value, ok := playgroundFloat(config, "frequency_penalty"); ok {
			req.FrequencyPenalty = &value
		}
	}
	if req.PresencePenalty == nil && playgroundParamEnabled(config, "presence_penalty") {
		if value, ok := playgroundFloat(config, "presence_penalty"); ok {
			req.PresencePenalty = &value
		}
	}
	if req.Seed == nil && playgroundParamEnabled(config, "seed") {
		if value, ok := playgroundFloat(config, "seed"); ok {
			req.Seed = &value
		}
	}
}

func applyPlaygroundModelConfigToResponsesRequest(req *dto.OpenAIResponsesRequest, config map[string]any) {
	if req == nil {
		return
	}
	if req.Temperature == nil && playgroundParamEnabled(config, "temperature") {
		if value, ok := playgroundFloat(config, "temperature"); ok {
			req.Temperature = &value
		}
	}
	if req.TopP == nil && playgroundParamEnabled(config, "top_p") {
		if value, ok := playgroundFloat(config, "top_p"); ok {
			req.TopP = &value
		}
	}
}

func applyPlaygroundModelConfigToClaudeRequest(req *dto.ClaudeRequest, config map[string]any) {
	if req == nil {
		return
	}
	if req.Temperature == nil && playgroundParamEnabled(config, "temperature") {
		if value, ok := playgroundFloat(config, "temperature"); ok {
			req.Temperature = &value
		}
	}
	if req.TopP == nil && playgroundParamEnabled(config, "top_p") {
		if value, ok := playgroundFloat(config, "top_p"); ok {
			req.TopP = &value
		}
	}
}

func applyPlaygroundPromptConfigToOpenAIRequest(req *dto.GeneralOpenAIRequest, config map[string]any) {
	if req == nil {
		return
	}
	if req.PromptMode == "" {
		if value, ok := playgroundInput(config, "promptMode"); ok {
			if text, ok := value.(string); ok && text != "" {
				req.PromptMode = text
			}
		}
	}
	if req.PromptMode == "native" && strings.TrimSpace(req.SystemPrompt) == "" {
		if value, ok := playgroundInput(config, "systemPrompt"); ok {
			if text, ok := value.(string); ok {
				req.SystemPrompt = text
			}
		}
	}
}

func applyPlaygroundPromptConfigToResponsesRequest(req *dto.OpenAIResponsesRequest, config map[string]any) {
	if req == nil {
		return
	}
	if req.PromptMode == "" {
		if value, ok := playgroundInput(config, "promptMode"); ok {
			if text, ok := value.(string); ok && text != "" {
				req.PromptMode = text
			}
		}
	}
	if req.PromptMode == "native" && strings.TrimSpace(req.SystemPrompt) == "" {
		if value, ok := playgroundInput(config, "systemPrompt"); ok {
			if text, ok := value.(string); ok {
				req.SystemPrompt = text
			}
		}
	}
}

func applyPlaygroundPromptConfigToClaudeRequest(req *dto.ClaudeRequest, config map[string]any) {
	if req == nil {
		return
	}
	if req.PromptMode == "" {
		if value, ok := playgroundInput(config, "promptMode"); ok {
			if text, ok := value.(string); ok && text != "" {
				req.PromptMode = text
			}
		}
	}
	if req.PromptMode == "native" && strings.TrimSpace(req.SystemPrompt) == "" {
		if value, ok := playgroundInput(config, "systemPrompt"); ok {
			if text, ok := value.(string); ok {
				req.SystemPrompt = text
			}
		}
	}
}

func applyPlaygroundDefaultsToOpenAIRequest(req *dto.GeneralOpenAIRequest, config map[string]any) {
	applyPlaygroundModelConfigToOpenAIRequest(req, config)
}

func applyPlaygroundDefaultsToResponsesRequest(req *dto.OpenAIResponsesRequest, config map[string]any) {
	applyPlaygroundModelConfigToResponsesRequest(req, config)
}

func playgroundParamEnabled(config map[string]any, key string) bool {
	enabledMap, _ := config["parameterEnabled"].(map[string]any)
	if enabledMap == nil {
		return false
	}
	value, exists := enabledMap[key]
	if !exists {
		return false
	}
	switch typed := value.(type) {
	case bool:
		return typed
	case string:
		parsed, err := strconv.ParseBool(typed)
		return err == nil && parsed
	case float64:
		return typed != 0
	case int:
		return typed != 0
	default:
		return false
	}
}

func playgroundInput(config map[string]any, key string) (any, bool) {
	inputs, _ := config["inputs"].(map[string]any)
	if inputs == nil {
		return nil, false
	}
	value, ok := inputs[key]
	return value, ok
}

func playgroundFloat(config map[string]any, key string) (float64, bool) {
	value, ok := playgroundInput(config, key)
	if !ok || value == nil {
		return 0, false
	}
	switch typed := value.(type) {
	case float64:
		return typed, true
	case float32:
		return float64(typed), true
	case int:
		return float64(typed), true
	case int64:
		return float64(typed), true
	case string:
		parsed, err := strconv.ParseFloat(typed, 64)
		return parsed, err == nil
	default:
		return 0, false
	}
}

func playgroundUint(config map[string]any, key string) (uint, bool) {
	value, ok := playgroundInput(config, key)
	if !ok || value == nil {
		return 0, false
	}
	switch typed := value.(type) {
	case float64:
		if typed < 0 {
			return 0, false
		}
		return uint(typed), true
	case int:
		if typed < 0 {
			return 0, false
		}
		return uint(typed), true
	case int64:
		if typed < 0 {
			return 0, false
		}
		return uint(typed), true
	case uint:
		return typed, true
	case string:
		parsed, err := strconv.ParseUint(typed, 10, 64)
		return uint(parsed), err == nil
	default:
		return 0, false
	}
}
