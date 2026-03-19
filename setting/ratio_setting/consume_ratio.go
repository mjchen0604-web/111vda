package ratio_setting

import "github.com/QuantumNous/new-api/types"

var defaultModelConsumeRatio = map[string]float64{}

var modelConsumeRatioMap = types.NewRWMap[string, float64]()

func ModelConsumeRatio2JSONString() string {
	return modelConsumeRatioMap.MarshalJSONString()
}

func UpdateModelConsumeRatioByJSONString(jsonStr string) error {
	return types.LoadFromJsonStringWithCallback(modelConsumeRatioMap, jsonStr, InvalidateExposedDataCache)
}

func GetModelConsumeRatio(name string) float64 {
	name = FormatMatchingModelName(name)
	if ratio, ok := modelConsumeRatioMap.Get(name); ok && ratio >= 0 {
		return ratio
	}
	return 1
}

func GetModelConsumeRatioCopy() map[string]float64 {
	return modelConsumeRatioMap.ReadAll()
}
