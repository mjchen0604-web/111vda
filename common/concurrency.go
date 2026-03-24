package common

const DefaultMaxConcurrency = 5

func NormalizeMaxConcurrency(limit int) int {
	if limit <= 0 {
		return DefaultMaxConcurrency
	}
	return limit
}
