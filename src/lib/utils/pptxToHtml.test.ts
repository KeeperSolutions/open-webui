import JSZip from 'jszip';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { pptxToImages } from './pptxToHtml';

const NS =
	'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"';

const xfrm = (x: number, y: number) =>
	`<p:spPr><a:xfrm><a:off x="${x * 9525}" y="${y * 9525}"/><a:ext cx="${800 * 9525}" cy="${400 * 9525}"/></a:xfrm></p:spPr>`;

const placeholder = (ph: string, spPr: string, body = '') =>
	`<p:sp><p:nvSpPr><p:cNvPr id="2" name=""/><p:cNvSpPr/><p:nvPr>${ph}</p:nvPr></p:nvSpPr>${spPr}${body}</p:sp>`;

const paragraphs = (...texts: string[]) =>
	`<p:txBody><a:bodyPr/>${texts.map((t) => `<a:p><a:r><a:t>${t}</a:t></a:r></a:p>`).join('')}</p:txBody>`;

const rels = (type: string, target: string) =>
	`<Relationships><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/${type}" Target="${target}"/></Relationships>`;

// Shaped like python-pptx output: the slide and layout placeholders have an empty spPr, only the master places them
const buildPptx = async (coverTitle = false) => {
	const zip = new JSZip();
	zip.file(
		'ppt/presentation.xml',
		`<p:presentation ${NS}><p:sldSz cx="${960 * 9525}" cy="${540 * 9525}"/></p:presentation>`
	);
	zip.file(
		'ppt/slides/slide1.xml',
		`<p:sld ${NS}><p:cSld><p:spTree>` +
			placeholder('<p:ph type="title"/>', '<p:spPr/>', paragraphs('Title')) +
			placeholder('<p:ph idx="1"/>', '<p:spPr/>', paragraphs('Prva', 'Druga')) +
			'</p:spTree></p:cSld></p:sld>'
	);
	zip.file('ppt/slides/_rels/slide1.xml.rels', rels('slideLayout', '../slideLayouts/slideLayout2.xml'));
	zip.file(
		'ppt/slideLayouts/slideLayout2.xml',
		`<p:sldLayout ${NS}><p:cSld><p:spTree>` +
			placeholder('<p:ph type="title"/>', '<p:spPr/>') +
			placeholder('<p:ph idx="1"/>', '<p:spPr/>') +
			'</p:spTree></p:cSld></p:sldLayout>'
	);
	zip.file(
		'ppt/slideLayouts/_rels/slideLayout2.xml.rels',
		rels('slideMaster', '../slideMasters/slideMaster1.xml')
	);
	zip.file(
		'ppt/slideMasters/slideMaster1.xml',
		`<p:sldMaster ${NS}><p:cSld><p:spTree>` +
			placeholder(
				'<p:ph type="title"/>',
				xfrm(40, 20),
				coverTitle ? '<p:txBody><a:bodyPr anchor="b"/></p:txBody>' : ''
			) +
			placeholder('<p:ph type="body" idx="1"/>', xfrm(40, 150)) +
			'</p:spTree></p:cSld><p:txStyles>' +
			'<p:titleStyle><a:lvl1pPr><a:defRPr sz="4400"/></a:lvl1pPr></p:titleStyle>' +
			'<p:bodyStyle><a:lvl1pPr><a:defRPr sz="3200"/></a:lvl1pPr></p:bodyStyle>' +
			'</p:txStyles></p:sldMaster>'
	);
	return zip.generateAsync({ type: 'arraybuffer' });
};

describe('pptxToImages', () => {
	let drawn: { text: string; x: number; y: number; font: string }[];

	beforeEach(() => {
		drawn = [];
		// jsdom has no canvas, so this records what the renderer draws instead
		const ctx = {
			font: '',
			fillStyle: '',
			textBaseline: '',
			fillRect: () => {},
			save: () => {},
			restore: () => {},
			rect: () => {},
			clip: () => {},
			drawImage: () => {},
			measureText: (text: string) => ({ width: text.length * 10 }),
			fillText(text: string, x: number, y: number) {
				drawn.push({ text, x, y, font: this.font });
			}
		};
		vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as never);
		vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,');
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('places placeholders from the master and uses its font sizes when the slide and layout leave them out', async () => {
		const { images } = await pptxToImages(await buildPptx());

		expect(images).toHaveLength(1);
		const title = drawn.find((d) => d.text === 'Title')!;
		expect(title.x).toBe(44);
		expect(title.y).toBeGreaterThan(20);
		expect(title.font).toContain('44pt');

		// Words are drawn one by one, so they're joined back per line
		const body = drawn.filter((d) => d.y > 150);
		const lines = [...new Set(body.map((d) => d.y))].map((y) =>
			body
				.filter((d) => d.y === y)
				.map((d) => d.text)
				.join('')
		);
		expect(lines).toEqual(['• Prva', '• Druga']);
		expect(body[0].font).toContain('32pt');
	});

	it('rests the text on the bottom of its box when the placeholder is anchored there', async () => {
		await pptxToImages(await buildPptx(true));

		// The master's title box runs from y 20 to 420, so bottom-anchored text lands near its end
		const title = drawn.find((d) => d.text === 'Title')!;
		expect(title.y).toBeGreaterThan(350);
		expect(title.y).toBeLessThanOrEqual(420);
	});
});
