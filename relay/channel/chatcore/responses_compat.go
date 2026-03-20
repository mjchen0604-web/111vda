package chatcore

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relay/helper"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/types"

	"github.com/gin-gonic/gin"
)

type chatcoreToolCallState struct {
	ID        string
	Name      string
	Arguments strings.Builder
	Added     bool
}

func isUndefinedText(value string) bool {
	normalized := strings.TrimSpace(strings.ToLower(value))
	return normalized == "" || normalized == "undefined" || normalized == "[undefined]"
}

func rawMessageToString(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var value string
	if err := common.Unmarshal(raw, &value); err == nil {
		if isUndefinedText(value) {
			return ""
		}
		return value
	}
	return ""
}

func rawMessageToAny(raw json.RawMessage) any {
	if len(raw) == 0 {
		return nil
	}
	var value any
	if err := common.Unmarshal(raw, &value); err != nil {
		return nil
	}
	if text, ok := value.(string); ok && isUndefinedText(text) {
		return nil
	}
	return value
}

func rawMessageToBoolPtr(raw json.RawMessage) *bool {
	value := rawMessageToAny(raw)
	boolValue, ok := value.(bool)
	if !ok {
		return nil
	}
	return &boolValue
}

func normalizeResponsesContent(raw any, role string) any {
	switch value := raw.(type) {
	case string:
		return value
	case []any:
		parts := make([]dto.MediaContent, 0, len(value))
		var textBuilder strings.Builder
		textOnly := true
		for _, item := range value {
			itemMap, ok := item.(map[string]any)
			if !ok {
				continue
			}
			typeName := common.Interface2String(itemMap["type"])
			switch typeName {
			case "input_text", "output_text", "text":
				text := common.Interface2String(itemMap["text"])
				parts = append(parts, dto.MediaContent{
					Type: dto.ContentTypeText,
					Text: text,
				})
				textBuilder.WriteString(text)
			case "input_image", "image_url":
				textOnly = false
				var imageURL string
				switch img := itemMap["image_url"].(type) {
				case string:
					imageURL = img
				case map[string]any:
					imageURL = common.Interface2String(img["url"])
				}
				parts = append(parts, dto.MediaContent{
					Type: dto.ContentTypeImageURL,
					ImageUrl: &dto.MessageImageUrl{
						Url:    imageURL,
						Detail: common.Interface2String(itemMap["detail"]),
					},
				})
			case "input_file":
				textOnly = false
				fileMap, _ := itemMap["file"].(map[string]any)
				parts = append(parts, dto.MediaContent{
					Type: dto.ContentTypeFile,
					File: &dto.MessageFile{
						FileName: common.Interface2String(fileMap["filename"]),
						FileData: common.Interface2String(fileMap["file_data"]),
						FileId:   common.Interface2String(fileMap["file_id"]),
					},
				})
			case "input_audio":
				textOnly = false
				audioMap, _ := itemMap["input_audio"].(map[string]any)
				parts = append(parts, dto.MediaContent{
					Type: dto.ContentTypeInputAudio,
					InputAudio: &dto.MessageInputAudio{
						Data:   common.Interface2String(audioMap["data"]),
						Format: common.Interface2String(audioMap["format"]),
					},
				})
			}
		}
		if len(parts) == 0 {
			return ""
		}
		if textOnly && role != "assistant" {
			return textBuilder.String()
		}
		return parts
	default:
		if marshaled, err := common.Marshal(value); err == nil {
			return string(marshaled)
		}
		return fmt.Sprintf("%v", value)
	}
}

func responsesRequestToChatCompletionsRequest(request dto.OpenAIResponsesRequest) (map[string]any, error) {
	chatReq := &dto.GeneralOpenAIRequest{
		Model:                request.Model,
		Stream:               request.Stream,
		StreamOptions:        request.StreamOptions,
		MaxCompletionTokens:  request.MaxOutputTokens,
		Temperature:          request.Temperature,
		TopP:                 request.TopP,
		TopLogProbs:          request.TopLogProbs,
		Metadata:             request.Metadata,
		Store:                request.Store,
		PromptCacheRetention: request.PromptCacheRetention,
		SafetyIdentifier:     request.SafetyIdentifier,
		User:                 request.User,
		PromptCacheKey:       rawMessageToString(request.PromptCacheKey),
	}

	if request.ServiceTier != "" && !isUndefinedText(request.ServiceTier) {
		if encoded, err := common.Marshal(request.ServiceTier); err == nil {
			chatReq.ServiceTier = encoded
		}
	}
	if request.Reasoning != nil {
		chatReq.ReasoningEffort = request.Reasoning.Effort
	}
	if parallel := rawMessageToBoolPtr(request.ParallelToolCalls); parallel != nil {
		chatReq.ParallelTooCalls = parallel
	}
	if toolChoice := rawMessageToAny(request.ToolChoice); toolChoice != nil {
		if toolChoiceMap, ok := toolChoice.(map[string]any); ok {
			if name := common.Interface2String(toolChoiceMap["name"]); name != "" {
				chatReq.ToolChoice = map[string]any{
					"type": "function",
					"function": map[string]any{
						"name": name,
					},
				}
			} else {
				chatReq.ToolChoice = toolChoice
			}
		} else {
			chatReq.ToolChoice = toolChoice
		}
	}
	responsesTools := make([]map[string]any, 0)
	if len(request.Tools) > 0 {
		var rawTools []map[string]any
		if err := common.Unmarshal(request.Tools, &rawTools); err == nil {
			tools := make([]dto.ToolCallRequest, 0, len(rawTools))
			for _, rawTool := range rawTools {
				toolType := common.Interface2String(rawTool["type"])
				if toolType == "" {
					toolType = "function"
				}
				if toolType == "web_search" || toolType == "web_search_preview" {
					continue
				}
				tool := dto.ToolCallRequest{
					Type: toolType,
					Function: dto.FunctionRequest{
						Name:        common.Interface2String(rawTool["name"]),
						Description: common.Interface2String(rawTool["description"]),
						Parameters:  rawTool["parameters"],
					},
				}
				if tool.Function.Name == "" && rawTool["function"] != nil {
					functionMap, _ := rawTool["function"].(map[string]any)
					tool.Function.Name = common.Interface2String(functionMap["name"])
					tool.Function.Description = common.Interface2String(functionMap["description"])
					if tool.Function.Parameters == nil {
						tool.Function.Parameters = functionMap["parameters"]
					}
				}
				tools = append(tools, tool)
			}
			chatReq.Tools = tools
		}
	}

	messages := make([]dto.Message, 0)
	if instructions := rawMessageToString(request.Instructions); instructions != "" {
		messages = append(messages, dto.Message{
			Role:    "developer",
			Content: instructions,
		})
	}

	if common.GetJsonType(request.Input) == "string" {
		messages = append(messages, dto.Message{
			Role:    "user",
			Content: rawMessageToString(request.Input),
		})
	} else if common.GetJsonType(request.Input) == "array" {
		var items []map[string]any
		if err := common.Unmarshal(request.Input, &items); err != nil {
			return nil, err
		}
		for _, item := range items {
			typeName := common.Interface2String(item["type"])
			switch typeName {
			case "function_call_output":
				callID := common.Interface2String(item["call_id"])
				output := item["output"]
				content := ""
				switch typed := output.(type) {
				case string:
					content = typed
				default:
					if marshaled, err := common.Marshal(typed); err == nil {
						content = string(marshaled)
					}
				}
				messages = append(messages, dto.Message{
					Role:       "tool",
					ToolCallId: callID,
					Content:    content,
				})
			case "function_call":
				callID := common.Interface2String(item["call_id"])
				name := common.Interface2String(item["name"])
				arguments := common.Interface2String(item["arguments"])
				msg := dto.Message{
					Role:    "assistant",
					Content: "",
				}
				msg.SetToolCalls([]dto.ToolCallRequest{
					{
						ID:   callID,
						Type: "function",
						Function: dto.FunctionRequest{
							Name:      name,
							Arguments: arguments,
						},
					},
				})
				messages = append(messages, msg)
			default:
				role := common.Interface2String(item["role"])
				if isUndefinedText(role) {
					continue
				}
				content := normalizeResponsesContent(item["content"], role)
				msg := dto.Message{Role: role}
				switch typed := content.(type) {
				case string:
					msg.Content = typed
				case []dto.MediaContent:
					msg.SetMediaContent(typed)
				default:
					msg.Content = typed
				}
				messages = append(messages, msg)
			}
		}
	}

	chatReq.Messages = messages
	chatReqMap := chatReq.ToMap()
	chatReqMap["_chatcore_responses_compat"] = true
	if len(responsesTools) > 0 {
		chatReqMap["responses_tools"] = responsesTools
	}
	if toolChoice := rawMessageToAny(request.ToolChoice); toolChoice != nil {
		if text, ok := toolChoice.(string); ok && strings.TrimSpace(text) != "" {
			chatReqMap["responses_tool_choice"] = text
		}
	}
	return chatReqMap, nil
}

func createdAtToInt(value any) int {
	switch typed := value.(type) {
	case int:
		return typed
	case int64:
		return int(typed)
	case float64:
		return int(typed)
	default:
		return 0
	}
}

func buildResponsesOutputFromChatMessage(message dto.Message, responseID string) []dto.ResponsesOutput {
	output := make([]dto.ResponsesOutput, 0, 2)
	text := strings.TrimSpace(message.StringContent())
	if text != "" {
		output = append(output, dto.ResponsesOutput{
			Type:   "message",
			ID:     "msg_" + responseID,
			Status: "completed",
			Role:   "assistant",
			Content: []dto.ResponsesOutputContent{
				{
					Type: "output_text",
					Text: text,
				},
			},
		})
	}
	for idx, toolCall := range message.ParseToolCalls() {
		callID := strings.TrimSpace(toolCall.ID)
		if callID == "" {
			callID = fmt.Sprintf("call_%d", idx)
		}
		output = append(output, dto.ResponsesOutput{
			Type:      "function_call",
			ID:        "fc_" + callID,
			Status:    "completed",
			CallId:    callID,
			Name:      toolCall.Function.Name,
			Arguments: toolCall.Function.Arguments,
		})
	}
	return output
}

func chatCompletionsToResponsesResponse(chatResp *dto.OpenAITextResponse) *dto.OpenAIResponsesResponse {
	if chatResp == nil {
		return nil
	}
	output := make([]dto.ResponsesOutput, 0)
	if len(chatResp.Choices) > 0 {
		output = buildResponsesOutputFromChatMessage(chatResp.Choices[0].Message, chatResp.Id)
	}
	status, _ := common.Marshal("completed")
	previousResponseID := json.RawMessage("null")
	return &dto.OpenAIResponsesResponse{
		ID:                 chatResp.Id,
		Object:             "response",
		CreatedAt:          createdAtToInt(chatResp.Created),
		Status:             status,
		Model:              chatResp.Model,
		Output:             output,
		PreviousResponseID: previousResponseID,
		Usage:              &chatResp.Usage,
	}
}

func responsesCompatHandler(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (*dto.Usage, *types.NewAPIError) {
	if resp == nil || resp.Body == nil {
		return nil, types.NewOpenAIError(fmt.Errorf("invalid response"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
	}
	defer service.CloseResponseBodyGracefully(resp)

	var chatResp dto.OpenAITextResponse
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, types.NewOpenAIError(err, types.ErrorCodeReadResponseBodyFailed, http.StatusInternalServerError)
	}
	if err := common.Unmarshal(body, &chatResp); err != nil {
		return nil, types.NewOpenAIError(err, types.ErrorCodeBadResponseBody, http.StatusInternalServerError)
	}
	if oaiErr := chatResp.GetOpenAIError(); oaiErr != nil && oaiErr.Type != "" {
		return nil, types.WithOpenAIError(*oaiErr, resp.StatusCode)
	}

	responsesResp := chatCompletionsToResponsesResponse(&chatResp)
	usage := chatResp.Usage
	if usage.TotalTokens == 0 && info != nil {
		content := ""
		if len(chatResp.Choices) > 0 {
			content = chatResp.Choices[0].Message.StringContent()
		}
		if content != "" {
			usage = *service.ResponseText2Usage(c, content, info.UpstreamModelName, info.GetEstimatePromptTokens())
			responsesResp.Usage = &usage
		}
	}
	responseBody, err := common.Marshal(responsesResp)
	if err != nil {
		return nil, types.NewOpenAIError(err, types.ErrorCodeJsonMarshalFailed, http.StatusInternalServerError)
	}
	service.IOCopyBytesGracefully(c, resp, responseBody)
	return &usage, nil
}

func responsesCompatStreamHandler(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (*dto.Usage, *types.NewAPIError) {
	if resp == nil || resp.Body == nil {
		return nil, types.NewOpenAIError(fmt.Errorf("invalid response"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
	}
	defer service.CloseResponseBodyGracefully(resp)

	helper.SetEventStreamHeaders(c)

	responseID := helper.GetResponseID(c)
	messageID := "msg_" + responseID
	model := ""
	createdAt := int(time.Now().Unix())
	sentCreated := false
	sentMessageItemAdded := false
	sentMessagePartAdded := false
	var usage dto.Usage
	var outputText strings.Builder
	toolCalls := make(map[int]*chatcoreToolCallState)

	sendEvent := func(event dto.ResponsesStreamResponse) bool {
		data, err := common.Marshal(event)
		if err != nil {
			return false
		}
		helper.ResponseChunkData(c, event, string(data))
		return true
	}

	sendCreated := func() bool {
		if sentCreated {
			return true
		}
		status, _ := common.Marshal("in_progress")
		return sendEvent(dto.ResponsesStreamResponse{
			Type: "response.created",
			Response: &dto.OpenAIResponsesResponse{
				ID:        responseID,
				Object:    "response",
				CreatedAt: createdAt,
				Model:     model,
				Status:    status,
			},
		})
	}

	sendMessageTextOpenIfNeeded := func() bool {
		if !sentMessageItemAdded {
			item := dto.ResponsesOutput{
				Type:    "message",
				ID:      messageID,
				Status:  "in_progress",
				Role:    "assistant",
				Content: []dto.ResponsesOutputContent{},
			}
			if !sendEvent(dto.ResponsesStreamResponse{
				Type:        "response.output_item.added",
				OutputIndex: common.GetPointer(0),
				Item:        &item,
			}) {
				return false
			}
			sentMessageItemAdded = true
		}
		if !sentMessagePartAdded {
			part := dto.ResponsesReasoningSummaryPart{
				Type: "output_text",
				Text: "",
			}
			if !sendEvent(dto.ResponsesStreamResponse{
				Type:         "response.content_part.added",
				ItemID:       messageID,
				OutputIndex:  common.GetPointer(0),
				ContentIndex: common.GetPointer(0),
				Part:         &part,
			}) {
				return false
			}
			sentMessagePartAdded = true
		}
		return true
	}

	helper.StreamScannerHandler(c, resp, info, func(data string) bool {
		var chunk dto.ChatCompletionsStreamResponse
		if err := common.UnmarshalJsonStr(data, &chunk); err != nil {
			return true
		}
		if strings.TrimSpace(chunk.Id) != "" {
			responseID = chunk.Id
			messageID = "msg_" + responseID
		}
		if chunk.Created != 0 {
			createdAt = int(chunk.Created)
		}
		if strings.TrimSpace(chunk.Model) != "" {
			model = chunk.Model
		}
		if chunk.Usage != nil {
			usage = *chunk.Usage
		}
		if !sentCreated {
			if !sendCreated() {
				return false
			}
			sentCreated = true
		}

		for _, choice := range chunk.Choices {
			if delta := choice.Delta.GetContentString(); delta != "" {
				if !sendMessageTextOpenIfNeeded() {
					return false
				}
				outputText.WriteString(delta)
				if !sendEvent(dto.ResponsesStreamResponse{
					Type:         "response.output_text.delta",
					ItemID:       messageID,
					OutputIndex:  common.GetPointer(0),
					ContentIndex: common.GetPointer(0),
					Delta:        delta,
				}) {
					return false
				}
			}
			for _, toolCall := range choice.Delta.ToolCalls {
				index := 0
				if toolCall.Index != nil {
					index = *toolCall.Index
				}
				state := toolCalls[index]
				if state == nil {
					state = &chatcoreToolCallState{}
					toolCalls[index] = state
				}
				if strings.TrimSpace(toolCall.ID) != "" {
					state.ID = toolCall.ID
				}
				if strings.TrimSpace(toolCall.Function.Name) != "" {
					state.Name = toolCall.Function.Name
				}
				callID := strings.TrimSpace(state.ID)
				if callID == "" {
					callID = fmt.Sprintf("call_%d", index)
				}
				itemID := "fc_" + callID
				outputIndex := index
				if sentMessageItemAdded || outputText.Len() > 0 {
					outputIndex = index + 1
				}
				if !state.Added {
					item := dto.ResponsesOutput{
						Type:      "function_call",
						ID:        itemID,
						Status:    "in_progress",
						CallId:    callID,
						Name:      state.Name,
						Arguments: "",
					}
					if !sendEvent(dto.ResponsesStreamResponse{
						Type:        "response.output_item.added",
						OutputIndex: common.GetPointer(outputIndex),
						Item:        &item,
					}) {
						return false
					}
					state.Added = true
				}
				if toolCall.Function.Arguments != "" {
					if !sendEvent(dto.ResponsesStreamResponse{
						Type:        "response.function_call_arguments.delta",
						ItemID:      itemID,
						OutputIndex: common.GetPointer(outputIndex),
						Delta:       toolCall.Function.Arguments,
					}) {
						return false
					}
					state.Arguments.WriteString(toolCall.Function.Arguments)
				}
			}
		}
		return true
	})

	if !sentCreated {
		if !sendCreated() {
			return nil, types.NewOpenAIError(fmt.Errorf("failed to write response.created"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
		}
	}
	if outputText.Len() == 0 && len(toolCalls) == 0 {
		return nil, types.NewOpenAIError(
			fmt.Errorf("stream ended before any response output was emitted"),
			types.ErrorCodeBadResponse,
			http.StatusBadGateway,
		)
	}

	if outputText.Len() > 0 {
		text := outputText.String()
		if !sendMessageTextOpenIfNeeded() {
			return nil, types.NewOpenAIError(fmt.Errorf("failed to write response item/part start"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
		}
		if !sendEvent(dto.ResponsesStreamResponse{
			Type:         "response.content_part.done",
			ItemID:       messageID,
			OutputIndex:  common.GetPointer(0),
			ContentIndex: common.GetPointer(0),
			Part: &dto.ResponsesReasoningSummaryPart{
				Type: "output_text",
				Text: text,
			},
		}) {
			return nil, types.NewOpenAIError(fmt.Errorf("failed to write response.content_part.done"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
		}
		if !sendEvent(dto.ResponsesStreamResponse{
			Type:         "response.output_text.done",
			ItemID:       messageID,
			OutputIndex:  common.GetPointer(0),
			ContentIndex: common.GetPointer(0),
			Text:         text,
		}) {
			return nil, types.NewOpenAIError(fmt.Errorf("failed to write response.output_text.done"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
		}
	}

	output := make([]dto.ResponsesOutput, 0, 1+len(toolCalls))
	if outputText.Len() > 0 {
		messageItem := dto.ResponsesOutput{
			Type:   "message",
			ID:     messageID,
			Status: "completed",
			Role:   "assistant",
			Content: []dto.ResponsesOutputContent{
				{
					Type: "output_text",
					Text: outputText.String(),
				},
			},
		}
		output = append(output, messageItem)
		if !sendEvent(dto.ResponsesStreamResponse{
			Type:        "response.output_item.done",
			OutputIndex: common.GetPointer(0),
			Item:        &messageItem,
		}) {
			return nil, types.NewOpenAIError(fmt.Errorf("failed to write response.output_item.done(message)"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
		}
	}
	for idx, state := range toolCalls {
		callID := strings.TrimSpace(state.ID)
		if callID == "" {
			callID = fmt.Sprintf("call_%d", idx)
		}
		item := dto.ResponsesOutput{
			Type:      "function_call",
			ID:        "fc_" + callID,
			Status:    "completed",
			CallId:    callID,
			Name:      state.Name,
			Arguments: state.Arguments.String(),
		}
		output = append(output, item)
		if !sendEvent(dto.ResponsesStreamResponse{
			Type:        "response.function_call_arguments.done",
			ItemID:      item.ID,
			OutputIndex: common.GetPointer(idx),
		}) {
			return nil, types.NewOpenAIError(fmt.Errorf("failed to write response.function_call_arguments.done"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
		}
		if !sendEvent(dto.ResponsesStreamResponse{
			Type:        "response.output_item.done",
			OutputIndex: common.GetPointer(idx),
			Item:        &item,
		}) {
			return nil, types.NewOpenAIError(fmt.Errorf("failed to write response.output_item.done"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
		}
	}

	status, _ := common.Marshal("completed")
	previousResponseID := json.RawMessage("null")
	finalResponse := dto.OpenAIResponsesResponse{
		ID:                 responseID,
		Object:             "response",
		CreatedAt:          createdAt,
		Status:             status,
		Model:              model,
		Output:             output,
		PreviousResponseID: previousResponseID,
		Usage:              &usage,
	}
	if usage.TotalTokens == 0 {
		fallback := service.ResponseText2Usage(c, outputText.String(), info.UpstreamModelName, info.GetEstimatePromptTokens())
		if fallback != nil {
			usage = *fallback
			finalResponse.Usage = &usage
		}
	}
	if !sendEvent(dto.ResponsesStreamResponse{
		Type:     "response.completed",
		Response: &finalResponse,
	}) {
		return nil, types.NewOpenAIError(fmt.Errorf("failed to write response.completed"), types.ErrorCodeBadResponse, http.StatusInternalServerError)
	}
	return &usage, nil
}
