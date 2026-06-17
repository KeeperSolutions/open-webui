export function shouldShowTrustminderFeedback(
	config: { features?: { enable_trustminder_feedback?: boolean } } | undefined | null,
	user: unknown
): boolean {
	return Boolean(config?.features?.enable_trustminder_feedback) && Boolean(user);
}
