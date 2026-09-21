<script lang="ts">
	import { onDestroy } from 'svelte';
	import { fade, scale } from 'svelte/transition';

	/** The element to spotlight (found via a `data-spotlight-id` attribute) - stays inert while shown, the only way out is the dismiss button */
	export let target: HTMLElement | null = null;
	export let title = '';
	// May contain inline markup like <strong> - always a trusted, static, translated string, never user input
	export let message = '';
	export let dismissLabel = 'Got it';
	export let show = false;
	export let padding = 8;
	// Per-side overrides, defaulting to `padding` on every side when not given
	export let paddingTop: number | null = null;
	export let paddingBottom: number | null = null;
	export let onDismiss: (() => void) | null = null;
	// Optional auto-dismiss after this many ms of no interaction; off (null) by default since only the button should dismiss
	export let autoDismissMs: number | null = null;

	type Rect = { top: number; left: number; width: number; height: number };

	let rect: Rect | null = null;
	let calloutPlacement: 'top' | 'bottom' = 'bottom';
	let autoDismissTimeout: ReturnType<typeof setTimeout> | null = null;
	let listenersAttached = false;

	const rectsEqual = (a: Rect | null, b: Rect | null) =>
		a === b || (!!a && !!b && a.top === b.top && a.left === b.left && a.width === b.width && a.height === b.height);

	// Only reassigns `rect` (which re-renders the overlay) when the position actually changed
	const measure = () => {
		if (!target) {
			if (rect !== null) rect = null;
			return;
		}
		const r = target.getBoundingClientRect();
		const EDGE_MARGIN = 4;
		let top = r.top - (paddingTop ?? padding);
		let bottom = r.bottom + (paddingBottom ?? padding);
		let left = r.left - padding;
		let right = r.right + padding;

		// Shifts the ring to stay on-screen rather than clipping one edge, which would look off-center
		if (bottom > window.innerHeight - EDGE_MARGIN) {
			const overflow = bottom - (window.innerHeight - EDGE_MARGIN);
			top -= overflow;
			bottom -= overflow;
		}
		if (top < EDGE_MARGIN) {
			const overflow = EDGE_MARGIN - top;
			top += overflow;
			bottom += overflow;
		}
		if (right > window.innerWidth - EDGE_MARGIN) {
			const overflow = right - (window.innerWidth - EDGE_MARGIN);
			left -= overflow;
			right -= overflow;
		}
		if (left < EDGE_MARGIN) {
			const overflow = EDGE_MARGIN - left;
			left += overflow;
			right += overflow;
		}
		// Last-resort clamp, only reachable if the target is taller/wider than the viewport itself
		top = Math.max(top, EDGE_MARGIN);
		bottom = Math.min(bottom, window.innerHeight - EDGE_MARGIN);
		left = Math.max(left, EDGE_MARGIN);
		right = Math.min(right, window.innerWidth - EDGE_MARGIN);

		const next: Rect = {
			top,
			left,
			width: Math.max(right - left, 0),
			height: Math.max(bottom - top, 0)
		};
		if (!rectsEqual(rect, next)) {
			rect = next;
		}
		const nextPlacement = r.bottom + 160 < window.innerHeight ? 'bottom' : 'top';
		if (nextPlacement !== calloutPlacement) {
			calloutPlacement = nextPlacement;
		}
	};

	const dismiss = () => {
		show = false;
		onDismiss?.();
	};

	const onResize = () => measure();
	const onScroll = () => measure();

	// Callers should scroll target into view and wait for it to settle before setting it - this only measures once
	$: if (show && target) {
		measure();
	}

	$: {
		if (show && !listenersAttached) {
			window.addEventListener('resize', onResize);
			window.addEventListener('scroll', onScroll, true);
			listenersAttached = true;
		} else if (!show && listenersAttached) {
			window.removeEventListener('resize', onResize);
			window.removeEventListener('scroll', onScroll, true);
			listenersAttached = false;
		}
	}

	// Catches a nearby layout shift that isn't a scroll (e.g. a section still animating open) - cheap since measure() is a no-op once stable
	let pollInterval: ReturnType<typeof setInterval> | null = null;
	$: {
		if (show && target && !pollInterval) {
			pollInterval = setInterval(measure, 400);
		} else if (!show && pollInterval) {
			clearInterval(pollInterval);
			pollInterval = null;
		}
	}

	$: {
		if (show && autoDismissMs !== null) {
			if (autoDismissTimeout) clearTimeout(autoDismissTimeout);
			autoDismissTimeout = setTimeout(dismiss, autoDismissMs);
		} else if (autoDismissTimeout) {
			clearTimeout(autoDismissTimeout);
			autoDismissTimeout = null;
		}
	}

	onDestroy(() => {
		if (listenersAttached) {
			window.removeEventListener('resize', onResize);
			window.removeEventListener('scroll', onScroll, true);
		}
		if (autoDismissTimeout) clearTimeout(autoDismissTimeout);
		if (pollInterval) clearInterval(pollInterval);
	});

	const CALLOUT_WIDTH = 288;

	$: calloutTop = rect
		? calloutPlacement === 'bottom'
			? rect.top + rect.height + 10
			: rect.top - 10
		: 0;
	$: calloutLeft = rect
		? Math.min(
				Math.max(rect.left + rect.width - CALLOUT_WIDTH, 12),
				(typeof window !== 'undefined' ? window.innerWidth : 0) - CALLOUT_WIDTH - 12
			)
		: 0;
</script>

{#if show && rect}
	<!-- Inert overlay - only the callout button dismisses; the cutout's box-shadow darkens everything except the target -->
	<div class="fixed inset-0 z-9999" transition:fade={{ duration: 200 }}>
		<div
			class="absolute rounded-xl"
			style="top:{rect.top}px; left:{rect.left}px; width:{rect.width}px; height:{rect.height}px; box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.7);"
		></div>

		<div
			class="absolute rounded-xl ring-2 ring-inset ring-white/90 pointer-events-none"
			style="top:{rect.top}px; left:{rect.left}px; width:{rect.width}px; height:{rect.height}px"
		></div>

		<div
			class="absolute bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 rounded-xl shadow-xl p-3.5 text-sm pointer-events-auto"
			style="top:{calloutTop}px; left:{calloutLeft}px; width:{CALLOUT_WIDTH}px; {calloutPlacement === 'top'
				? 'transform: translateY(-100%);'
				: ''}"
			transition:scale={{ duration: 200, start: 0.95 }}
		>
			{#if title}
				<div class="font-medium mb-1">{title}</div>
			{/if}
			<div class="text-gray-600 dark:text-gray-300">{@html message}</div>
			<div class="flex justify-end mt-2.5">
				<button
					type="button"
					class="px-3 py-1.5 text-xs font-medium bg-gray-900 dark:bg-white text-white dark:text-gray-900 rounded-full"
					on:click={dismiss}
				>
					{dismissLabel}
				</button>
			</div>
		</div>
	</div>
{/if}
