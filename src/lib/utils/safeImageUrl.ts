import { WEBUI_BASE_URL } from '$lib/constants';

const PLACEHOLDER_IMAGE = '/favicon.png';

/**
 * Validates an image URL against an allowlist of safe patterns and returns
 * the URL if trusted, or a placeholder otherwise.
 *
 * Allowed patterns:
 *   - Relative paths (starting with '/')
 *   - data:image/* URIs
 *   - Same-origin URLs (starting with WEBUI_BASE_URL)
 *   - Gravatar URLs (https://www.gravatar.com/avatar/)
<<<<<<< HEAD
 *
 * All other URLs (including arbitrary http(s):// origins) are rejected to
 * prevent client-side IP/UA/Referer leaks to attacker-controlled servers.
 */
export function safeImageUrl(url: string): string {
=======
 *   - External HTTP(S) URLs when allowExternal is true
 *
 * All other URLs (including arbitrary http(s):// origins by default) are
 * rejected to prevent client-side IP/UA/Referer leaks to attacker-controlled servers.
 */
export function safeImageUrl(url: string, allowExternal = false): string {
>>>>>>> v0.11.0
	if (!url || url === '') {
		return `${WEBUI_BASE_URL}${PLACEHOLDER_IMAGE}`;
	}

	if (
<<<<<<< HEAD
		url.startsWith(WEBUI_BASE_URL) ||
		url.startsWith('https://www.gravatar.com/avatar/') ||
=======
		(WEBUI_BASE_URL && url.startsWith(WEBUI_BASE_URL)) ||
		url.startsWith('https://www.gravatar.com/avatar/') ||
		(allowExternal && /^https?:\/\//i.test(url)) ||
>>>>>>> v0.11.0
		url.startsWith('data:') ||
		url.startsWith('/')
	) {
		return url;
	}

	return `${WEBUI_BASE_URL}${PLACEHOLDER_IMAGE}`;
}
