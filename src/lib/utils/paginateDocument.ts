// A4 at 96dpi with Word's default 2.54cm margins
export const PAGE_WIDTH = 794;
export const PAGE_HEIGHT = 1123;
export const PAGE_MARGIN = 96;
export const PAGE_CONTENT_WIDTH = PAGE_WIDTH - 2 * PAGE_MARGIN;
export const PAGE_CONTENT_HEIGHT = PAGE_HEIGHT - 2 * PAGE_MARGIN;

const isPageBreak = (el: Element) => el.tagName === 'HR' && el.classList.contains('page-break');

const isHeading = (el: Element) => /^H[1-6]$/.test(el.tagName);

// Moves items into target while the probe still fits, keeping at least one when force is set, and returns how many moved
const fillWhileFits = (
	items: HTMLElement[],
	target: HTMLElement,
	fits: () => boolean,
	force: boolean
) => {
	let moved = 0;
	for (const item of items) {
		const parent = item.parentNode;
		const next = item.nextSibling;
		target.appendChild(item);
		if (!fits() && !(force && moved === 0)) {
			// Put it back where it was so it stays in what is left for the next page
			parent?.insertBefore(item, next);
			break;
		}
		moved++;
	}
	return moved;
};

type Split = { moved: boolean; rest: HTMLElement | null };

// Puts as much of a table or list on the current page as fits, and says whether any of it moved and what is left for the next page
const splitBlock = (
	block: HTMLElement,
	probe: HTMLElement,
	fits: () => boolean,
	force: boolean
): Split => {
	const unmoved = { moved: false, rest: block };

	if (block instanceof HTMLTableElement) {
		const rows = Array.from(block.tBodies).flatMap((body) => Array.from(body.rows));
		if (rows.length < 2 && !force) return unmoved;

		const piece = block.cloneNode(false) as HTMLTableElement;
		if (block.tHead) piece.appendChild(block.tHead.cloneNode(true));
		const body = document.createElement('tbody');
		piece.appendChild(body);
		probe.appendChild(piece);

		const moved = fillWhileFits(rows, body, fits, force);
		if (moved === 0) {
			probe.removeChild(piece);
			return unmoved;
		}
		return { moved: true, rest: moved < rows.length ? block : null };
	}

	if (block instanceof HTMLUListElement || block instanceof HTMLOListElement) {
		const items = Array.from(block.children) as HTMLElement[];
		if (items.length < 2 && !force) return unmoved;

		const piece = block.cloneNode(false) as HTMLElement;
		probe.appendChild(piece);

		const moved = fillWhileFits(items, piece, fits, force);
		if (moved === 0) {
			probe.removeChild(piece);
			return unmoved;
		}
		if (moved === items.length) return { moved: true, rest: null };
		// The rest of a numbered list carries on counting from where this page stopped
		if (block instanceof HTMLOListElement) block.start = (block.start || 1) + moved;
		return { moved: true, rest: block };
	}

	return unmoved;
};

// Splits document HTML into A4 pages by measuring its blocks inside probe, an empty element exactly one page of content wide
export const paginateDocument = (
	html: string,
	probe: HTMLElement,
	contentHeight = PAGE_CONTENT_HEIGHT
): string[] => {
	const source = document.createElement('div');
	source.innerHTML = html;
	const queue = Array.from(source.children) as HTMLElement[];

	const pages: string[] = [];
	const fits = () => probe.scrollHeight <= contentHeight;
	const flush = () => {
		if (probe.childElementCount > 0) pages.push(probe.innerHTML);
		probe.innerHTML = '';
	};

	probe.innerHTML = '';
	while (queue.length > 0) {
		const block = queue.shift()!;
		if (isPageBreak(block)) {
			flush();
			continue;
		}
		// A page break leaves empty paragraphs around it, which would only push the next page's text down
		if (probe.childElementCount === 0 && block.tagName === 'P' && !block.innerHTML.trim()) {
			continue;
		}

		probe.appendChild(block);
		if (fits()) continue;
		probe.removeChild(block);

		const pageEmpty = probe.childElementCount === 0;
		const { moved, rest } = splitBlock(block, probe, fits, pageEmpty);
		if (moved) {
			flush();
			if (rest) queue.unshift(rest);
			continue;
		}

		if (pageEmpty) {
			// Taller than a whole page and can't be split, so it gets a page of its own
			probe.appendChild(block);
			flush();
			continue;
		}

		// A heading shouldn't be left alone at the bottom of a page, so it moves down with what follows it
		const carried: HTMLElement[] = [];
		while (
			probe.childElementCount > 1 &&
			probe.lastElementChild &&
			isHeading(probe.lastElementChild)
		) {
			carried.unshift(probe.removeChild(probe.lastElementChild) as HTMLElement);
		}
		flush();
		queue.unshift(...carried, block);
	}
	flush();

	return pages.length > 0 ? pages : [''];
};
