package common

import (
	"slices"
	"strings"

	"github.com/QuantumNous/new-api/constant"
)

type ModelSkinAlias struct {
	PublicName   string
	InternalName string
	Icon         string
}

var modelSkinAliases = []ModelSkinAlias{
	{PublicName: "claude-opus-4-6", InternalName: "gpt-5.4-fast-xhigh", Icon: "Claude.Color"},
	{PublicName: "claude-opus-4-5", InternalName: "gpt-5.4-fast-high", Icon: "Claude.Color"},
	{PublicName: "claude-sonnet-4-6", InternalName: "gpt-5.4-fast-medium", Icon: "Claude.Color"},
	{PublicName: "claude-sonnet-4-5", InternalName: "gpt-5.4-fast-low", Icon: "Claude.Color"},
	{PublicName: "claude-haiku-4-5", InternalName: "gpt-5.4-mini-xhigh", Icon: "Claude.Color"},
	{PublicName: "claude-haiku-3-5", InternalName: "gpt-5.4-mini-high", Icon: "Claude.Color"},
}

var (
	publicToInternalSkinAlias = make(map[string]ModelSkinAlias, len(modelSkinAliases))
	internalToPublicSkinAlias = make(map[string]ModelSkinAlias, len(modelSkinAliases))
)

func init() {
	for _, alias := range modelSkinAliases {
		publicToInternalSkinAlias[strings.ToLower(alias.PublicName)] = alias
		internalToPublicSkinAlias[strings.ToLower(alias.InternalName)] = alias
	}
}

func ResolveSkinnedModelAlias(name string) string {
	alias, ok := publicToInternalSkinAlias[strings.ToLower(strings.TrimSpace(name))]
	if !ok {
		return name
	}
	return alias.InternalName
}

func PublicSkinnedModelName(name string) string {
	trimmed := strings.TrimSpace(name)
	if trimmed == "" {
		return name
	}
	if alias, ok := internalToPublicSkinAlias[strings.ToLower(trimmed)]; ok {
		return alias.PublicName
	}
	return trimmed
}

func ModelSkinIcon(name string) string {
	trimmed := strings.TrimSpace(name)
	if trimmed == "" {
		return ""
	}
	if alias, ok := publicToInternalSkinAlias[strings.ToLower(trimmed)]; ok {
		return alias.Icon
	}
	if alias, ok := internalToPublicSkinAlias[strings.ToLower(trimmed)]; ok {
		return alias.Icon
	}
	return ""
}

func PublicSkinnedModelNames() []string {
	names := make([]string, 0, len(modelSkinAliases))
	for _, alias := range modelSkinAliases {
		names = append(names, alias.PublicName)
	}
	return names
}

func PublicModelAliases() []ModelSkinAlias {
	return append([]ModelSkinAlias{}, modelSkinAliases...)
}

func ExposeSkinnedModelNames(modelNames []string) []string {
	if len(modelNames) == 0 {
		return []string{}
	}
	rawSet := make(map[string]struct{}, len(modelNames))
	for _, modelName := range modelNames {
		rawSet[strings.ToLower(strings.TrimSpace(modelName))] = struct{}{}
	}
	result := make([]string, 0, len(modelNames))
	seen := make(map[string]struct{}, len(modelNames))
	for _, modelName := range modelNames {
		trimmed := strings.TrimSpace(modelName)
		if trimmed == "" {
			continue
		}
		exposed := PublicSkinnedModelName(trimmed)
		if exposed != trimmed {
			if _, exists := rawSet[strings.ToLower(exposed)]; exists {
				continue
			}
		}
		key := strings.ToLower(exposed)
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		result = append(result, exposed)
	}
	return result
}

func ResolveBillingModelName(modelName string, channelType int) string {
	if IsPublicSkinnedModel(modelName) {
		return ResolveSkinnedModelAlias(modelName)
	}
	if channelType != constant.ChannelTypeChatCore {
		return modelName
	}
	return ResolveSkinnedModelAlias(modelName)
}

func FindModelSkinAliasByInternal(name string) (ModelSkinAlias, bool) {
	alias, ok := internalToPublicSkinAlias[strings.ToLower(strings.TrimSpace(name))]
	return alias, ok
}

func FindModelSkinAliasByPublic(name string) (ModelSkinAlias, bool) {
	alias, ok := publicToInternalSkinAlias[strings.ToLower(strings.TrimSpace(name))]
	return alias, ok
}

func IsPublicSkinnedModel(name string) bool {
	_, ok := publicToInternalSkinAlias[strings.ToLower(strings.TrimSpace(name))]
	return ok
}

func IsInternalSkinnedModel(name string) bool {
	_, ok := internalToPublicSkinAlias[strings.ToLower(strings.TrimSpace(name))]
	return ok
}

func WithSkinnedModelNames(existing []string) []string {
	result := append([]string{}, existing...)
	for _, alias := range PublicSkinnedModelNames() {
		if !slices.Contains(result, alias) {
			result = append(result, alias)
		}
	}
	return result
}
