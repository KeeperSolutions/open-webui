// Press-and-hold gesture for touch devices, which have no hover to reveal a row's actions.

const DEFAULT_DURATION_MS = 500;
const MOVE_TOLERANCE_PX = 10;
// Upper bound for the click a browser sends after touchend.
const CLICK_WINDOW_MS = 700;

export type LongPressParams = {
	// Only armed while true, so pointer devices keep their native context menu.
	enabled?: boolean;
	// Defaults to `enabled`. Set it separately for rows that have no menu to open but
	// must not fall back to the browser's own either.
	suppressNativeMenu?: boolean;
	duration?: number;
	onLongPress: () => void;
};

export function longPress(node: HTMLElement, params: LongPressParams) {
	let current = params;

	let timeout: ReturnType<typeof setTimeout> | null = null;
	let origin: { x: number; y: number } | null = null;
	let firedLongPress = false;

	// Live while the click produced by the hold still has to be swallowed.
	let swallowTimeout: ReturnType<typeof setTimeout> | null = null;

	const isEnabled = () => current.enabled !== false;
	const suppressesNativeMenu = () => current.suppressNativeMenu ?? isEnabled();

	const cancel = () => {
		if (timeout) {
			clearTimeout(timeout);
			timeout = null;
		}

		origin = null;
	};

	const onTouchStart = (event: TouchEvent) => {
		cancel();
		stopSwallowingClick();
		firedLongPress = false;

		if (!isEnabled() || event.touches.length !== 1) return;

		const touch = event.touches[0];
		origin = { x: touch.clientX, y: touch.clientY };

		timeout = setTimeout(() => {
			timeout = null;
			origin = null;
			firedLongPress = true;

			current.onLongPress();
		}, current.duration ?? DEFAULT_DURATION_MS);
	};

	const onTouchEnd = () => {
		cancel();

		if (firedLongPress) {
			firedLongPress = false;
			startSwallowingClick();
		}
	};

	const onTouchCancel = () => {
		cancel();
		firedLongPress = false;
	};

	// A press that turns into a scroll is not a long press.
	const onTouchMove = (event: TouchEvent) => {
		if (!origin) return;

		const touch = event.touches[0];
		if (!touch) return;

		if (
			Math.abs(touch.clientX - origin.x) > MOVE_TOLERANCE_PX ||
			Math.abs(touch.clientY - origin.y) > MOVE_TOLERANCE_PX
		) {
			cancel();
		}
	};

	// The hold belongs to this gesture, not to the browser's own link menu.
	const onContextMenu = (event: Event) => {
		if (!suppressesNativeMenu()) return;

		event.preventDefault();
	};

	// On document, because a listener here would lose the race when the node handles the click itself.
	const onClickCapture = (event: MouseEvent) => {
		if (!(event.target instanceof Node) || !node.contains(event.target)) return;

		stopSwallowingClick();

		event.preventDefault();
		event.stopPropagation();
	};

	const startSwallowingClick = () => {
		document.addEventListener('click', onClickCapture, true);
		swallowTimeout = setTimeout(stopSwallowingClick, CLICK_WINDOW_MS);
	};

	function stopSwallowingClick() {
		if (swallowTimeout) {
			clearTimeout(swallowTimeout);
			swallowTimeout = null;
		}

		document.removeEventListener('click', onClickCapture, true);
	}

	// Suppresses the iOS callout and text selection; both properties inherit to the row.
	const applyTouchStyles = () => {
		if (suppressesNativeMenu()) {
			node.style.setProperty('-webkit-touch-callout', 'none');
			node.style.setProperty('user-select', 'none');
		} else {
			node.style.removeProperty('-webkit-touch-callout');
			node.style.removeProperty('user-select');
		}
	};

	applyTouchStyles();

	node.addEventListener('touchstart', onTouchStart, { passive: true });
	node.addEventListener('touchmove', onTouchMove, { passive: true });
	node.addEventListener('touchend', onTouchEnd);
	node.addEventListener('touchcancel', onTouchCancel);
	node.addEventListener('contextmenu', onContextMenu);

	return {
		update(next: LongPressParams) {
			current = next;
			applyTouchStyles();

			if (!isEnabled()) {
				cancel();
				stopSwallowingClick();
				firedLongPress = false;
			}
		},
		destroy() {
			cancel();
			stopSwallowingClick();

			node.removeEventListener('touchstart', onTouchStart);
			node.removeEventListener('touchmove', onTouchMove);
			node.removeEventListener('touchend', onTouchEnd);
			node.removeEventListener('touchcancel', onTouchCancel);
			node.removeEventListener('contextmenu', onContextMenu);
		}
	};
}
