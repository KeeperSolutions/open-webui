import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectConnector } from './index';

const IPHONE_UA =
	'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15';
const DESKTOP_UA =
	'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0';

const setUserAgent = (ua: string) => vi.stubGlobal('navigator', { userAgent: ua });

describe('connectConnector', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.restoreAllMocks();
	});

	it('skips the popup and redirects the full page on a mobile user agent', async () => {
		setUserAgent(IPHONE_UA);
		const openSpy = vi.spyOn(window, 'open');
		delete (window as unknown as { location?: unknown }).location;
		window.location = { href: '' } as unknown as Location;

		await connectConnector('/connectors/google-drive/connect');

		expect(openSpy).not.toHaveBeenCalled();
		expect(window.location.href).toBe('http://localhost:8080/api/v1/connectors/google-drive/connect');
	});

	it('opens a sized popup on a desktop user agent', async () => {
		setUserAgent(DESKTOP_UA);
		const fakePopup = { closed: true } as Window;
		const openSpy = vi.spyOn(window, 'open').mockReturnValue(fakePopup);

		await connectConnector('/connectors/google-drive/connect');

		expect(openSpy).toHaveBeenCalledWith(
			expect.stringContaining('/connectors/google-drive/connect'),
			'connector-oauth',
			expect.stringContaining('width=520')
		);
	});
});
