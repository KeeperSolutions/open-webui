export function shouldShowTrustmiderFeedback(
	config: { features?: { enable_trustmider_feedback?: boolean } } | undefined | null,
	user: unknown
): boolean {
	return Boolean(config?.features?.enable_trustmider_feedback) && Boolean(user);
}
