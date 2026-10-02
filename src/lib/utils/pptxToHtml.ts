/**
 * Lightweight PPTX → Image renderer.
 *
 * Extracts text and images from each slide and renders them
 * directly to canvas, returning PNG data URLs.
 *
 * Uses jszip (dynamically imported) and the browser Canvas 2D API.
 * No theme resolution, charts, SmartArt, or animations — preview only.
 */

import type JSZip from 'jszip';

const EMU_PER_PX = 9525;
const emuToPx = (emu: number) => Math.round(emu / EMU_PER_PX);

const parseEmu = (val: string | null | undefined): number => (val ? parseInt(val, 10) || 0 : 0);

/** Load a data URI into an Image element and wait for it. */
const loadImage = (src: string): Promise<HTMLImageElement> =>
	new Promise((resolve, reject) => {
		const img = new Image();
		img.onload = () => resolve(img);
		img.onerror = () => reject(new Error('Failed to load image'));
		img.src = src;
	});

type Zip = JSZip;

const parseXml = (text: string) => new DOMParser().parseFromString(text, 'application/xml');

/** Map each relationship id of a part (slide, layout...) to its target path and type. */
const readRels = async (
	zip: Zip,
	partPath: string
): Promise<Record<string, { target: string; type: string }>> => {
	const dir = partPath.slice(0, partPath.lastIndexOf('/'));
	const file = zip.file(`${dir}/_rels/${partPath.slice(dir.length + 1)}.rels`);
	const rels: Record<string, { target: string; type: string }> = {};
	if (!file) return rels;

	const relEls = parseXml(await file.async('text')).getElementsByTagName('Relationship');
	for (let i = 0; i < relEls.length; i++) {
		const target = relEls[i].getAttribute('Target') ?? '';
		rels[relEls[i].getAttribute('Id') ?? ''] = {
			target: target.startsWith('../') ? 'ppt/' + target.replace('../', '') : target,
			type: relEls[i].getAttribute('Type') ?? ''
		};
	}
	return rels;
};

/** The part a relationship of the given kind (e.g. "slideLayout") points to, with its own path. */
const loadRelatedPart = async (zip: Zip, partPath: string, kind: string) => {
	const rel = Object.values(await readRels(zip, partPath)).find((r) => r.type.endsWith(`/${kind}`));
	const file = rel ? zip.file(rel.target) : null;
	return file && rel ? { path: rel.target, doc: parseXml(await file.async('text')) } : null;
};

// Placeholders with no type are content/body boxes, and a centered title is styled like a title
const TITLE_PLACEHOLDERS = ['title', 'ctrTitle'];
const placeholderKind = (type: string) => (TITLE_PLACEHOLDERS.includes(type) ? 'title' : type || 'body');

const placeholderOf = (shape: Element) => {
	const ph = shape.getElementsByTagName('p:ph')[0];
	return ph ? { type: ph.getAttribute('type') ?? '', idx: ph.getAttribute('idx') ?? '' } : null;
};

/** What a placeholder inherits when the slide doesn't say: from its layout (same idx, or same type), then its master. */
const inherited = <T>(
	ph: { type: string; idx: string },
	layout: Document | null,
	master: Document | null,
	pick: (sp: Element) => T | null | undefined
): T | null => {
	const find = (doc: Document | null, matches: (other: { type: string; idx: string }) => boolean) => {
		if (!doc) return null;
		for (const sp of Array.from(doc.getElementsByTagName('p:sp'))) {
			const other = placeholderOf(sp);
			const value = other && matches(other) ? pick(sp) : null;
			if (value) return value;
		}
		return null;
	};
	const kind = placeholderKind(ph.type);
	return (
		(ph.idx ? find(layout, (o) => o.idx === ph.idx) : null) ??
		find(layout, (o) => placeholderKind(o.type) === kind) ??
		find(master, (o) => placeholderKind(o.type) === kind)
	);
};

const bodyAnchor = (sp: Element) => sp.getElementsByTagName('a:bodyPr')[0]?.getAttribute('anchor');

/** The master's bullet character for a body paragraph at the given indent level. */
const masterBullet = (master: Document | null, level: number) =>
	master
		?.getElementsByTagName('p:bodyStyle')[0]
		?.getElementsByTagName(`a:lvl${level + 1}pPr`)[0]
		?.getElementsByTagName('a:buChar')[0]
		?.getAttribute('char') ?? '•';

/** The master's default font size in pt for a title or body paragraph at the given indent level. */
const masterFontPt = (master: Document | null, kind: string, level: number): number | null => {
	const style = master?.getElementsByTagName(kind === 'title' ? 'p:titleStyle' : 'p:bodyStyle')[0];
	const sz = style
		?.getElementsByTagName(`a:lvl${level + 1}pPr`)[0]
		?.getElementsByTagName('a:defRPr')[0]
		?.getAttribute('sz');
	return sz ? parseInt(sz, 10) / 100 : null;
};

/**
 * Convert PPTX ArrayBuffer → array of PNG data URL strings, one per slide.
 */
export async function pptxToImages(
	buffer: ArrayBuffer
): Promise<{ images: string[]; width: number; height: number }> {
	const JSZip = (await import('jszip')).default;
	const zip = await JSZip.loadAsync(buffer);

	// ── Read slide dimensions from presentation.xml ──────────────────
	let slideW = 960;
	let slideH = 540;
	const presXml = zip.file('ppt/presentation.xml');
	if (presXml) {
		const presText = await presXml.async('text');
		const presDoc = new DOMParser().parseFromString(presText, 'application/xml');
		const sldSz = presDoc.getElementsByTagName('p:sldSz')[0];
		if (sldSz) {
			slideW = emuToPx(parseEmu(sldSz.getAttribute('cx')));
			slideH = emuToPx(parseEmu(sldSz.getAttribute('cy')));
		}
	}

	// ── Collect media files (images) as base64 data URIs ─────────────
	const media: Record<string, string> = {};
	const mediaFiles = Object.keys(zip.files).filter((f) => f.startsWith('ppt/media/'));
	await Promise.all(
		mediaFiles.map(async (path) => {
			const file = zip.file(path);
			if (!file) return;
			const base64 = await file.async('base64');
			const ext = path.split('.').pop()?.toLowerCase() ?? '';
			const mime =
				ext === 'png'
					? 'image/png'
					: ext === 'gif'
						? 'image/gif'
						: ext === 'svg'
							? 'image/svg+xml'
							: ext === 'emf' || ext === 'wmf'
								? 'image/x-emf'
								: 'image/jpeg';
			media[path] = `data:${mime};base64,${base64}`;
		})
	);

	// ── Discover slide files ─────────────────────────────────────────
	const slideFiles = Object.keys(zip.files)
		.filter((f) => /^ppt\/slides\/slide\d+\.xml$/.test(f))
		.sort((a, b) => {
			const na = parseInt(a.match(/slide(\d+)/)?.[1] ?? '0');
			const nb = parseInt(b.match(/slide(\d+)/)?.[1] ?? '0');
			return na - nb;
		});

	const images: string[] = [];

	for (const slidePath of slideFiles) {
		const slideText = await zip.file(slidePath)!.async('text');
		const slideDoc = new DOMParser().parseFromString(slideText, 'application/xml');

		// Load relationship file for this slide to resolve image references
		const rels = Object.fromEntries(
			Object.entries(await readRels(zip, slidePath)).map(([id, rel]) => [id, rel.target])
		);

		// Placeholders (title, content) usually leave their position and font size to the layout and master
		const layout = await loadRelatedPart(zip, slidePath, 'slideLayout');
		const master = layout ? await loadRelatedPart(zip, layout.path, 'slideMaster') : null;
		const theme = master ? await loadRelatedPart(zip, master.path, 'theme') : null;
		// The theme's body font, which text without a font of its own is drawn in
		const themeFont = theme?.doc
			.getElementsByTagName('a:minorFont')[0]
			?.getElementsByTagName('a:latin')[0]
			?.getAttribute('typeface');
		const fontFamily = `${themeFont ? `"${themeFont}", ` : ''}Calibri, Arial, sans-serif`;

		// ── Create canvas and render slide ───────────────────────────
		const canvas = document.createElement('canvas');
		canvas.width = slideW;
		canvas.height = slideH;
		const ctx = canvas.getContext('2d')!;

		// White background
		ctx.fillStyle = '#ffffff';
		ctx.fillRect(0, 0, slideW, slideH);

		const spTree = slideDoc.getElementsByTagName('p:spTree')[0];
		if (!spTree) {
			images.push(canvas.toDataURL('image/png'));
			continue;
		}

		const shapes = [
			...Array.from(spTree.getElementsByTagName('p:sp')),
			...Array.from(spTree.getElementsByTagName('p:pic'))
		];

		for (const shape of shapes) {
			const ph = placeholderOf(shape);
			const xfrm =
				shape.getElementsByTagName('a:xfrm')[0] ??
				shape.getElementsByTagName('p:xfrm')[0] ??
				(ph
					? inherited(ph, layout?.doc ?? null, master?.doc ?? null, (sp) =>
							sp.getElementsByTagName('a:xfrm')[0]
						)
					: null);
			if (!xfrm) continue;

			const off = xfrm.getElementsByTagName('a:off')[0];
			const ext = xfrm.getElementsByTagName('a:ext')[0];
			if (!off || !ext) continue;

			const x = emuToPx(parseEmu(off.getAttribute('x')));
			const y = emuToPx(parseEmu(off.getAttribute('y')));
			const w = emuToPx(parseEmu(ext.getAttribute('cx')));
			const h = emuToPx(parseEmu(ext.getAttribute('cy')));

			if (w === 0 && h === 0) continue;

			// ── Picture ──────────────────────────────────────────────
			const blipFill = shape.getElementsByTagName('p:blipFill')[0];
			if (blipFill) {
				const blip = blipFill.getElementsByTagName('a:blip')[0];
				if (blip) {
					const rEmbed = blip.getAttribute('r:embed') ?? '';
					const mediaPath = rels[rEmbed];
					const dataUri = mediaPath ? media[mediaPath] : '';
					if (dataUri && !dataUri.includes('image/x-emf')) {
						try {
							const img = await loadImage(dataUri);
							ctx.drawImage(img, x, y, w, h);
						} catch {
							// Skip images that fail to load
						}
					}
				}
				continue;
			}

			// ── Text shape ───────────────────────────────────────────
			const txBody = shape.getElementsByTagName('p:txBody')[0];
			if (!txBody) continue;

			ctx.save();
			ctx.rect(x, y, w, h);
			ctx.clip();

			const paragraphs = txBody.getElementsByTagName('a:p');
			let cursorY = y;
			const kind = ph ? placeholderKind(ph.type) : null;
			// Words are laid out first and drawn once the text's height is known, so it can sit at the bottom or middle
			const words: { text: string; x: number; y: number; font: string; color: string }[] = [];
			const anchor =
				bodyAnchor(shape) ??
				(ph ? inherited(ph, layout?.doc ?? null, master?.doc ?? null, bodyAnchor) : null);

			for (let pi = 0; pi < paragraphs.length; pi++) {
				const para = paragraphs[pi];
				const runs = para.getElementsByTagName('a:r');
				const pPr = para.getElementsByTagName('a:pPr')[0];
				const level = parseInt(pPr?.getAttribute('lvl') ?? '0', 10) || 0;
				const defaultFontSize = (kind && masterFontPt(master?.doc ?? null, kind, level)) || 12;
				// Content placeholders get their bullets from the master, which isn't rendered, so they're drawn here
				const bullet =
					kind === 'body' && !pPr?.getElementsByTagName('a:buNone').length
						? `${masterBullet(master?.doc ?? null, level)} `
						: '';

				if (runs.length === 0) {
					cursorY += defaultFontSize * 1.5;
					continue;
				}

				// Calculate max font size in this paragraph for line height
				let maxFontPt = defaultFontSize;
				for (let ri = 0; ri < runs.length; ri++) {
					const rPr = runs[ri].getElementsByTagName('a:rPr')[0];
					if (rPr) {
						const sz = rPr.getAttribute('sz');
						if (sz) {
							const pt = parseInt(sz, 10) / 100;
							if (pt > maxFontPt) maxFontPt = pt;
						}
					}
				}

				const lineHeight = maxFontPt * 1.4;
				cursorY += maxFontPt; // baseline offset

				let cursorX = x + 4; // small left padding

				for (let ri = 0; ri < runs.length; ri++) {
					const run = runs[ri];
					const rPr = run.getElementsByTagName('a:rPr')[0];
					const runText = run.getElementsByTagName('a:t')[0]?.textContent ?? '';
					if (!runText) continue;
					const text = ri === 0 ? bullet + runText : runText;

					let fontPt = defaultFontSize;
					let bold = false;
					let italic = false;
					let color = '#000000';

					if (rPr) {
						if (rPr.getAttribute('b') === '1') bold = true;
						if (rPr.getAttribute('i') === '1') italic = true;
						const sz = rPr.getAttribute('sz');
						if (sz) fontPt = parseInt(sz, 10) / 100;
						const solidFill = rPr.getElementsByTagName('a:solidFill')[0];
						if (solidFill) {
							const srgb = solidFill.getElementsByTagName('a:srgbClr')[0];
							if (srgb) {
								const val = srgb.getAttribute('val');
								if (val) color = `#${val}`;
							}
						}
					}

					const font = `${italic ? 'italic ' : ''}${bold ? 'bold ' : ''}${fontPt}pt ${fontFamily}`;
					ctx.font = font;

					// Simple word-wrap within the shape bounds
					for (const word of text.split(/(\s+)/)) {
						const metrics = ctx.measureText(word);
						if (cursorX + metrics.width > x + w && cursorX > x + 4) {
							cursorX = x + 4;
							cursorY += lineHeight;
						}
						words.push({ text: word, x: cursorX, y: cursorY, font, color });
						cursorX += metrics.width;
					}
				}

				cursorY += lineHeight * 0.4; // paragraph spacing
			}

			const spare = Math.max(0, y + h - cursorY);
			const shift = anchor === 'b' ? spare : anchor === 'ctr' ? spare / 2 : 0;
			ctx.textBaseline = 'alphabetic';
			for (const word of words) {
				if (word.y + shift > y + h) break;
				ctx.font = word.font;
				ctx.fillStyle = word.color;
				ctx.fillText(word.text, word.x, word.y + shift);
			}

			ctx.restore();
		}

		images.push(canvas.toDataURL('image/png'));
	}

	return { images, width: slideW, height: slideH };
}
