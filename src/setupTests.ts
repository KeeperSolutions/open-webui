import '@testing-library/jest-dom/vitest';

// jsdom lacks these browser APIs that some components (e.g. the TipTap-based
// RichTextInput) reference at mount. Provide minimal no-op polyfills.
class ResizeObserverStub {
	observe() {}
	unobserve() {}
	disconnect() {}
}

class IntersectionObserverStub {
	root = null;
	rootMargin = '';
	thresholds = [];
	observe() {}
	unobserve() {}
	disconnect() {}
	takeRecords() {
		return [];
	}
}

if (!('ResizeObserver' in globalThis)) {
	// @ts-expect-error - assigning stub to global
	globalThis.ResizeObserver = ResizeObserverStub;
}

if (!('IntersectionObserver' in globalThis)) {
	// @ts-expect-error - assigning stub to global
	globalThis.IntersectionObserver = IntersectionObserverStub;
}

// jsdom does not implement scrolling
if (!Element.prototype.scrollIntoView) {
	Element.prototype.scrollIntoView = () => {};
}

// jsdom lacks the Web Animations API that Svelte's transition directives use internally
if (!Element.prototype.animate) {
	Element.prototype.animate = function () {
		return {
			finished: Promise.resolve(),
			cancel: () => {},
			play: () => {},
			pause: () => {},
			reverse: () => {},
			onfinish: null,
			oncancel: null
		} as unknown as Animation;
	};
}
