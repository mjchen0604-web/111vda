package codex

import (
	"fmt"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
)

const (
	defaultCompactionEnabled       = true
	defaultCompactionMinInputItems = 12
	defaultCompactionMaxInputItems = 24
	defaultCompactionKeepRecent    = 8
	defaultCompactionMaxSummary    = 4000
	defaultCompactionMaxItem       = 480
	compactionSummaryHeader        = "[Gateway compacted conversation summary]"
)

type contextCompactionSettings struct {
	Enabled             bool
	MinInputItems       int
	MaxInputItems       int
	PreserveRecentItems int
	MaxSummaryChars     int
	MaxItemChars        int
}

func clampInt(value, min, max int) int {
	if value < min {
		return min
	}
	if value > max {
		return max
	}
	return value
}

func parseCompactionBool(value any, defaultValue bool) bool {
	switch typed := value.(type) {
	case bool:
		return typed
	case string:
		normalized := strings.TrimSpace(strings.ToLower(typed))
		switch normalized {
		case "1", "true", "yes", "on", "enabled", "enable", "auto":
			return true
		case "0", "false", "no", "off", "disabled", "disable", "none":
			return false
		}
	}
	return defaultValue
}

func parseCompactionInt(value any, defaultValue, min, max int) int {
	parsed, err := common.Any2Type[int](value)
	if err == nil {
		return clampInt(parsed, min, max)
	}
	if floatValue, err := common.Any2Type[float64](value); err == nil {
		return clampInt(int(floatValue), min, max)
	}
	if text, ok := value.(string); ok {
		return clampInt(common.String2Int(text), min, max)
	}
	return clampInt(defaultValue, min, max)
}

func parseContextCompactionSettings(raw []byte) contextCompactionSettings {
	settings := contextCompactionSettings{
		Enabled:             defaultCompactionEnabled,
		MinInputItems:       defaultCompactionMinInputItems,
		MaxInputItems:       defaultCompactionMaxInputItems,
		PreserveRecentItems: defaultCompactionKeepRecent,
		MaxSummaryChars:     defaultCompactionMaxSummary,
		MaxItemChars:        defaultCompactionMaxItem,
	}
	if len(raw) == 0 {
		return settings
	}

	switch common.GetJsonType(raw) {
	case "boolean":
		var enabled bool
		if err := common.Unmarshal(raw, &enabled); err == nil {
			settings.Enabled = enabled
		}
		return settings
	case "string":
		var text string
		if err := common.Unmarshal(raw, &text); err == nil {
			settings.Enabled = parseCompactionBool(text, defaultCompactionEnabled)
		}
		return settings
	case "object":
	default:
		return settings
	}

	var payload map[string]any
	if err := common.Unmarshal(raw, &payload); err != nil {
		return settings
	}
	if nested, ok := payload["compaction"].(map[string]any); ok {
		for key, value := range nested {
			payload[key] = value
		}
	}
	settings.Enabled = parseCompactionBool(payload["enabled"], defaultCompactionEnabled)
	mode := strings.TrimSpace(strings.ToLower(common.Interface2String(payload["mode"])))
	if mode == "" {
		mode = strings.TrimSpace(strings.ToLower(common.Interface2String(payload["type"])))
	}
	if mode == "" {
		mode = strings.TrimSpace(strings.ToLower(common.Interface2String(payload["strategy"])))
	}
	if mode == "disabled" || mode == "off" || mode == "none" {
		settings.Enabled = false
	}
	settings.MinInputItems = parseCompactionInt(payload["min_input_items"], defaultCompactionMinInputItems, 4, 256)
	if value, exists := payload["min_messages"]; exists {
		settings.MinInputItems = parseCompactionInt(value, settings.MinInputItems, 4, 256)
	}
	if value, exists := payload["min_items"]; exists {
		settings.MinInputItems = parseCompactionInt(value, settings.MinInputItems, 4, 256)
	}

	settings.MaxInputItems = parseCompactionInt(payload["max_input_items"], defaultCompactionMaxInputItems, 6, 512)
	if value, exists := payload["max_messages"]; exists {
		settings.MaxInputItems = parseCompactionInt(value, settings.MaxInputItems, 6, 512)
	}
	if value, exists := payload["max_items"]; exists {
		settings.MaxInputItems = parseCompactionInt(value, settings.MaxInputItems, 6, 512)
	}

	settings.PreserveRecentItems = parseCompactionInt(payload["preserve_recent_items"], defaultCompactionKeepRecent, 2, 128)
	if value, exists := payload["keep_recent_items"]; exists {
		settings.PreserveRecentItems = parseCompactionInt(value, settings.PreserveRecentItems, 2, 128)
	}
	if value, exists := payload["keep_recent"]; exists {
		settings.PreserveRecentItems = parseCompactionInt(value, settings.PreserveRecentItems, 2, 128)
	}

	settings.MaxSummaryChars = parseCompactionInt(payload["max_summary_chars"], defaultCompactionMaxSummary, 600, 24000)
	if value, exists := payload["summary_max_chars"]; exists {
		settings.MaxSummaryChars = parseCompactionInt(value, settings.MaxSummaryChars, 600, 24000)
	}

	settings.MaxItemChars = parseCompactionInt(payload["max_item_chars"], defaultCompactionMaxItem, 120, 2000)
	if value, exists := payload["item_max_chars"]; exists {
		settings.MaxItemChars = parseCompactionInt(value, settings.MaxItemChars, 120, 2000)
	}
	return settings
}

func safeJSON(value any) string {
	data, err := common.Marshal(value)
	if err != nil {
		return fmt.Sprintf("%v", value)
	}
	return string(data)
}

func truncateText(text string, limit int) string {
	if len(text) <= limit {
		return text
	}
	head := clampInt(limit/2, 32, limit)
	tail := clampInt(limit-head-16, 24, limit)
	if head+tail >= len(text) {
		return text
	}
	return text[:head] + " ...[trimmed]... " + text[len(text)-tail:]
}

func contentPartToText(part map[string]any) string {
	partType := strings.TrimSpace(strings.ToLower(common.Interface2String(part["type"])))
	switch partType {
	case "input_text", "output_text", "text", "summary_text":
		return common.Interface2String(part["text"])
	case "input_image":
		imageURL := common.Interface2String(part["image_url"])
		if strings.HasPrefix(imageURL, "data:") {
			return "[image:data-url]"
		}
		if imageURL != "" {
			return fmt.Sprintf("[image:%s]", imageURL)
		}
		return "[image]"
	case "input_file":
		if fileMap, ok := part["file"].(map[string]any); ok {
			fileName := common.Interface2String(fileMap["filename"])
			if fileName != "" {
				return fmt.Sprintf("[file:%s]", fileName)
			}
		}
		return "[file]"
	case "input_audio":
		return "[audio]"
	default:
		return ""
	}
}

func summarizeMessageItem(item map[string]any, maxItemChars int) string {
	role := strings.ToUpper(strings.TrimSpace(common.Interface2String(item["role"])))
	if role == "" {
		role = "USER"
	}
	content, _ := item["content"].([]any)
	textParts := make([]string, 0, len(content))
	for _, partAny := range content {
		part, ok := partAny.(map[string]any)
		if !ok {
			continue
		}
		text := contentPartToText(part)
		if text != "" {
			textParts = append(textParts, text)
		}
	}
	if len(textParts) == 0 {
		textParts = append(textParts, "[empty]")
	}
	return truncateText(role+": "+strings.Join(textParts, " "), maxItemChars)
}

func summarizeFunctionCall(item map[string]any, maxItemChars int) string {
	name := strings.TrimSpace(common.Interface2String(item["name"]))
	if name == "" {
		name = "tool"
	}
	callID := strings.TrimSpace(common.Interface2String(item["call_id"]))
	arguments := safeJSON(item["arguments"])
	prefix := "TOOL_CALL[" + name
	if callID != "" {
		prefix += "#" + callID
	}
	prefix += "]"
	return truncateText(prefix+": "+arguments, maxItemChars)
}

func summarizeFunctionResult(item map[string]any, maxItemChars int) string {
	callID := strings.TrimSpace(common.Interface2String(item["call_id"]))
	output := safeJSON(item["output"])
	prefix := "TOOL_RESULT"
	if callID != "" {
		prefix += "[" + callID + "]"
	}
	return truncateText(prefix+": "+output, maxItemChars)
}

func summarizeInputItem(item map[string]any, maxItemChars int) string {
	itemType := strings.TrimSpace(strings.ToLower(common.Interface2String(item["type"])))
	switch itemType {
	case "message":
		return summarizeMessageItem(item, maxItemChars)
	case "function_call":
		return summarizeFunctionCall(item, maxItemChars)
	case "function_call_output":
		return summarizeFunctionResult(item, maxItemChars)
	default:
		return truncateText(itemType+": "+safeJSON(item), maxItemChars)
	}
}

func fitSummaryLines(lines []string, maxChars int) string {
	if len(lines) == 0 {
		return ""
	}
	joined := strings.Join(lines, "\n")
	if len(joined) <= maxChars {
		return joined
	}
	headCount := clampInt(4, 1, len(lines))
	if headCount > len(lines) {
		headCount = len(lines)
	}
	head := append([]string(nil), lines[:headCount]...)
	marker := fmt.Sprintf("[...%d earlier items compacted...]", max(0, len(lines)-headCount))
	used := len(strings.Join(append(append([]string(nil), head...), marker), "\n"))
	tail := make([]string, 0, len(lines)-headCount)
	for idx := len(lines) - 1; idx >= headCount; idx-- {
		line := lines[idx]
		extra := len(line) + 1
		if used+extra > maxChars {
			break
		}
		tail = append([]string{line}, tail...)
		used += extra
	}
	candidate := append(head, marker)
	candidate = append(candidate, tail...)
	fitted := strings.Join(candidate, "\n")
	if len(fitted) <= maxChars {
		return fitted
	}
	return truncateText(fitted, maxChars)
}

func mergeInstructionsWithSummary(rawInstructions []byte, summaryText string, compactedCount, totalItems int) ([]byte, error) {
	summaryBlock := fmt.Sprintf(
		"%s\nCompacted earlier input items: %d of %d.\nTreat the summary below as background context. If it conflicts with the preserved recent turns, prefer the preserved recent turns.\n%s",
		compactionSummaryHeader,
		compactedCount,
		totalItems,
		summaryText,
	)
	var existing string
	if len(rawInstructions) > 0 {
		if err := common.Unmarshal(rawInstructions, &existing); err != nil {
			existing = strings.TrimSpace(string(rawInstructions))
		}
	}
	existing = strings.TrimSpace(existing)
	if existing != "" {
		return common.Marshal(existing + "\n\n" + summaryBlock)
	}
	return common.Marshal(summaryBlock)
}

func maybeCompactResponsesRequest(request *dto.OpenAIResponsesRequest) error {
	if request == nil {
		return nil
	}
	settings := parseContextCompactionSettings(request.ContextManagement)
	if !settings.Enabled {
		return nil
	}
	if common.GetJsonType(request.Input) != "array" {
		return nil
	}

	var inputItems []map[string]any
	if err := common.Unmarshal(request.Input, &inputItems); err != nil {
		return nil
	}
	if len(inputItems) == 0 {
		return nil
	}

	totalSerializedChars := 0
	for _, item := range inputItems {
		totalSerializedChars += len(safeJSON(item))
	}
	keepRecent := settings.PreserveRecentItems
	if keepRecent >= len(inputItems) {
		keepRecent = len(inputItems) - 1
	}
	if keepRecent < 1 {
		return nil
	}

	shouldCompact := len(inputItems) > settings.MaxInputItems ||
		(len(inputItems) >= settings.MinInputItems && totalSerializedChars > settings.MaxSummaryChars*2)
	if !shouldCompact {
		return nil
	}

	oldItems := inputItems[:len(inputItems)-keepRecent]
	recentItems := inputItems[len(inputItems)-keepRecent:]
	lines := make([]string, 0, len(oldItems))
	for _, item := range oldItems {
		line := summarizeInputItem(item, settings.MaxItemChars)
		if strings.TrimSpace(line) != "" {
			lines = append(lines, line)
		}
	}
	summaryText := fitSummaryLines(lines, settings.MaxSummaryChars)
	if strings.TrimSpace(summaryText) == "" {
		return nil
	}

	instructions, err := mergeInstructionsWithSummary(request.Instructions, summaryText, len(oldItems), len(inputItems))
	if err != nil {
		return err
	}
	encodedInput, err := common.Marshal(recentItems)
	if err != nil {
		return err
	}
	request.Input = encodedInput
	request.Instructions = instructions
	return nil
}
